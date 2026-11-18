import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_canvas_client
from app.api.errors import api_error
from app.core.config import settings
from app.db.session import get_db
from app.integrations.canvas.client import CanvasClient, CanvasError
from app.models.course import Course
from app.models.file import CourseFile
from app.models.system import SyncState
from app.schemas.system import (
    BoardStatusOut,
    ConnectionCheckOut,
    CourseSyncStatusOut,
    SyncProgressOut,
    SyncStatusOut,
)
from app.services import boards, runtime_settings
from app.workers.scheduler import sync_manager

router = APIRouter(prefix="/sync", tags=["sync"])


def course_display_order():
    """按界面上**显示的那个名字**排序。

    界面显示的是 `course_code || name`，而排序一直用的是 `name` —— 两者常常
    完全不同（course_code 是 "DB301H1F 20269"，name 是 "Introduction to
    Databases"），于是边栏看上去毫无规律。lower() 让排序大小写无关，
    也不依赖 SQLite 特有的 NOCASE 排序规则。
    """
    return func.lower(func.coalesce(func.nullif(Course.course_code, ""), Course.name))

@router.get("/status", response_model=SyncStatusOut)
async def sync_status(db: AsyncSession = Depends(get_db)) -> SyncStatusOut:
    rt = await runtime_settings.load(db)
    return SyncStatusOut(
        canvas_configured=settings.canvas_configured,
        canvas_base_url=settings.canvas_base_url,
        is_running=sync_manager.is_running,
        interval_minutes=rt.sync_interval_minutes,
        interval_choices=runtime_settings.INTERVAL_CHOICES,
        next_run_at=sync_manager.next_run_at,
        last_full_sync_at=await sync_manager.last_completed(),
        last_report=sync_manager.last_report,
        progress=SyncProgressOut(**sync_manager.progress),
        syncing_course_ids=sorted(sync_manager.syncing_course_ids),
    )


def _board_rows(states: list[SyncState]) -> list[BoardStatusOut]:
    """把同步时存下的板块报告翻成给用户看的一行行。

    报告是同步那一刻算好整份存进 `payload` 的 —— 界面渲染的和收集器扫的是
    同一份东西，不再由前端去猜几个内部状态之间的关系。
    """
    coverage = next((s for s in states if s.resource_key.endswith(":coverage")), None)
    payload = (coverage.payload if coverage else None) or {}
    unsupported = payload.get("unsupported") or []

    rows: list[BoardStatusOut] = []
    for entry in payload.get("boards") or []:
        status = entry.get("status", boards.SCANNED)
        rows.append(
            BoardStatusOut(
                id=entry.get("id", ""),
                label=entry.get("label", ""),
                status=status,
                count=entry.get("count", 0),
                detail="、".join(unsupported) if status == boards.UNSUPPORTED else None,
            )
        )
    return rows


@router.get("/courses", response_model=list[CourseSyncStatusOut])
async def course_sync_status(db: AsyncSession = Depends(get_db)) -> list[CourseSyncStatusOut]:
    """按课程看同步结果 —— 哪门课成功、哪门课失败、失败原因是什么。

    之前的界面把所有资源平铺成一张表，里面一半是 "unavailable"，
    既看不出哪门课有问题，也解释不了 unavailable 到底算不算问题。
    """
    # 和边栏一致：只显示在同步的课。没勾选的课没有同步状态可言
    courses = (
        (
            await db.execute(
                select(Course)
                .where(Course.sync_enabled.is_(True))
                .order_by(course_display_order())
            )
        )
        .scalars()
        .all()
    )
    file_counts = {
        cid: n
        for cid, n in (
            await db.execute(
                select(CourseFile.course_id, func.count()).group_by(CourseFile.course_id)
            )
        ).all()
    }
    states = (await db.execute(select(SyncState))).scalars().all()

    by_course: dict[int, list[SyncState]] = {}
    for state in states:
        parts = state.resource_key.split(":")
        if len(parts) >= 3 and parts[0] == "course":
            try:
                by_course.setdefault(int(parts[1]), []).append(state)
            except ValueError:
                continue

    out: list[CourseSyncStatusOut] = []
    for course in courses:
        if course.id in sync_manager.syncing_course_ids or sync_manager.is_running:
            status = "syncing" if course.id in sync_manager.syncing_course_ids else None
        else:
            status = None
        if status is None:
            if course.last_synced_at is None:
                status = "never"
            elif course.last_sync_failed:
                status = "failed"
            else:
                status = "ok"

        board_rows = _board_rows(by_course.get(course.canvas_id, []))

        out.append(
            CourseSyncStatusOut(
                course_id=course.id,
                name=course.name,
                course_code=course.course_code,
                is_concluded=course.is_concluded,
                status=status,
                last_synced_at=course.last_synced_at,
                error=course.last_sync_error,
                file_count=file_counts.get(course.id, 0),
                boards=board_rows,
            )
        )
    return out


@router.post("/run")
async def run_sync(wait: bool = False) -> dict:
    """同步所有课程。会把下一次自动同步顺延 —— 刚全量跑过，没必要马上又来一遍。

    用 create_task 而不是 BackgroundTasks：后者要等响应发完才开始，
    界面上会有一两秒"点了没反应"的空档。这里先把状态置为运行中再返回，
    前端立刻就能显示进度。
    """
    if sync_manager.is_running:
        return {"status": "already_running"}
    # 单课同步也要挡：全量会把那门课再同步一遍，两边同时写就冲突了
    if sync_manager.syncing_course_ids:
        raise api_error(409, "course_sync_running")
    if wait:
        return await sync_manager.run_sync()
    sync_manager.mark_started()
    asyncio.create_task(sync_manager.run_sync())
    return {"status": "started"}


@router.post("/courses/{course_id}")
async def run_course_sync(course_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """只同步一门课。不影响自动同步的排期。"""
    course = await db.get(Course, course_id)
    if course is None:
        raise api_error(404, "course_not_found")
    if sync_manager.is_running:
        raise api_error(409, "sync_running")
    if course_id in sync_manager.syncing_course_ids:
        return {"status": "already_running"}
    # 同样先登记再返回，界面上按钮立刻变成"同步中"
    sync_manager.syncing_course_ids.add(course_id)
    asyncio.create_task(sync_manager.run_course_sync(course_id))
    return {"status": "started", "course_id": course_id}


@router.get("/check", response_model=ConnectionCheckOut)
async def check_connection(
    client: CanvasClient = Depends(get_canvas_client),
) -> ConnectionCheckOut:
    """验证 token 和地址是否有效 —— 配置页面用它给用户即时反馈。"""
    try:
        profile = await client.get_self()
        courses = await client.list_courses()
        return ConnectionCheckOut(
            ok=True,
            message="连接成功",
            user_name=profile.get("name"),
            course_count=len(courses),
        )
    except CanvasError as exc:
        return ConnectionCheckOut(ok=False, message=str(exc))
