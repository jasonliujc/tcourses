import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import api_error
from app.db.session import get_db
from app.models.canvas_item import CanvasItem
from app.models.course import Course
from app.models.file import CourseFile, FileGroup
from app.models.system import NotificationEvent
from app.schemas.canvas import (
    CanvasItemOut,
    CourseStatsOut,
    CourseUpdate,
    SidebarLayoutResult,
    SidebarLayoutUpdate,
)
from app.services import planner

router = APIRouter(prefix="/courses", tags=["courses"])

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def course_display_order():
    """按界面上**显示的那个名字**排序。

    界面显示的是 `course_code || name`，而排序一直用的是 `name` —— 两者常常
    完全不同（course_code 是 "DB301H1F 20269"，name 是 "Introduction to
    Databases"），于是边栏看上去毫无规律。lower() 让排序大小写无关，
    也不依赖 SQLite 特有的 NOCASE 排序规则。
    """
    return func.lower(func.coalesce(func.nullif(Course.course_code, ""), Course.name))


def sidebar_order():
    """边栏顺序：用户排过的在前（按手动顺序），没排过的在后（按字母序）。

    `sidebar_sort_order` 为 0 表示"从没排过"。不特殊处理的话它会排到 1 前面，
    于是每次同步新发现一门课，那门课就凭空跳到边栏最上面 —— 用户排好的顺序
    等于每学期自己坏一次。
    """
    return (
        case((Course.sidebar_sort_order == 0, 1), else_=0),
        Course.sidebar_sort_order,
        course_display_order(),
    )


async def _count(db: AsyncSession, stmt) -> dict[int, int]:
    rows = (await db.execute(stmt)).all()
    return {row[0]: row[1] for row in rows}


async def _stats(
    db: AsyncSession, course_ids: list[int] | None = None
) -> dict[str, dict[int, int]]:
    """课程维度的三个计数。course_ids 为 None 时统计全部。"""

    def scoped(stmt, column):
        return stmt if course_ids is None else stmt.where(column.in_(course_ids))

    return {
        "files": await _count(
            db,
            scoped(
                select(CourseFile.course_id, func.count()).group_by(CourseFile.course_id),
                CourseFile.course_id,
            ),
        ),
        "groups": await _count(
            db,
            scoped(
                select(FileGroup.course_id, func.count()).group_by(FileGroup.course_id),
                FileGroup.course_id,
            ),
        ),
        "events": await _count(
            db,
            scoped(
                select(NotificationEvent.course_id, func.count())
                .where(NotificationEvent.is_read.is_(False))
                .group_by(NotificationEvent.course_id),
                NotificationEvent.course_id,
            ),
        ),
    }


def _with_stats(course: Course, stats: dict[str, dict[int, int]]) -> CourseStatsOut:
    item = CourseStatsOut.model_validate(course)
    item.color = planner.course_color(course)
    item.custom_color = bool(course.color)
    item.file_count = stats["files"].get(course.id, 0)
    item.group_count = stats["groups"].get(course.id, 0)
    item.unread_event_count = stats["events"].get(course.id, 0)
    return item


@router.get("", response_model=list[CourseStatsOut])
async def list_courses(
    include_hidden: bool = False, db: AsyncSession = Depends(get_db)
) -> list[CourseStatsOut]:
    # 只列用户选择要同步的课。没勾选的课在库里只是一条目录记录，没有内容 ——
    # 让它出现在边栏里只会让人以为同步坏了。要改选择去「设置与同步」。
    stmt = select(Course).where(Course.sync_enabled.is_(True))
    if not include_hidden:
        stmt = stmt.where(Course.is_hidden.is_(False))
    stmt = stmt.order_by(*sidebar_order())
    courses = (await db.execute(stmt)).scalars().all()

    stats = await _stats(db)
    return [_with_stats(c, stats) for c in courses]


@router.put("/sidebar", response_model=SidebarLayoutResult)
async def save_sidebar_layout(
    payload: SidebarLayoutUpdate, db: AsyncSession = Depends(get_db)
) -> SidebarLayoutResult:
    """保存「课程管理」里的显示与顺序。

    只认在同步的课：没同步的课在库里只是一条目录记录，让它出现在边栏上
    只会是一个永远空的课程页。
    """
    by_id = {
        c.id: c
        for c in (
            await db.execute(select(Course).where(Course.sync_enabled.is_(True)))
        )
        .scalars()
        .all()
    }

    visible = 0
    hidden = 0
    # 从 1 开始编号：0 被留作"从没排过"的哨兵值（见 sidebar_order）
    for position, item in enumerate(payload.items, start=1):
        course = by_id.get(item.course_id)
        if course is None:
            continue
        course.sidebar_sort_order = position
        course.is_hidden = item.is_hidden
        if item.is_hidden:
            hidden += 1
        else:
            visible += 1

    await db.commit()
    return SidebarLayoutResult(visible=visible, hidden=hidden)


@router.get("/{course_id}", response_model=CourseStatsOut)
async def get_course(course_id: int, db: AsyncSession = Depends(get_db)) -> CourseStatsOut:
    course = await db.get(Course, course_id)
    if course is None:
        raise api_error(404, "course_not_found")
    return _with_stats(course, await _stats(db, [course_id]))


@router.patch("/{course_id}", response_model=CourseStatsOut)
async def update_course(
    course_id: int, payload: CourseUpdate, db: AsyncSession = Depends(get_db)
) -> CourseStatsOut:
    course = await db.get(Course, course_id)
    if course is None:
        raise api_error(404, "course_not_found")
    for key, value in payload.model_dump(exclude_none=True).items():
        if key == "color":
            # 空串 = 恢复默认；其余必须是 #rrggbb，不然前端画不出来
            if value and not _HEX.fullmatch(value):
                raise api_error(400, "invalid_color")
            value = value or None
        setattr(course, key, value)
    await db.commit()
    return _with_stats(course, await _stats(db, [course_id]))


@router.get("/{course_id}/items", response_model=list[CanvasItemOut])
async def list_course_items(
    course_id: int,
    kind: str | None = Query(None, description="assignment / announcement / page / message"),
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
) -> list[CanvasItem]:
    stmt = select(CanvasItem).where(CanvasItem.course_id == course_id)
    if kind:
        stmt = stmt.where(CanvasItem.kind == kind)
    stmt = stmt.order_by(CanvasItem.canvas_updated_at.desc().nullslast()).limit(limit)
    return list((await db.execute(stmt)).scalars().all())
