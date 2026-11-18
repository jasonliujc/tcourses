"""日历与待办 API。

条目量很小（一学期两三百条），所以列表一次全取、在 Python 里合并用户改动再筛 ——
"显示在哪一天"取决于用户有没有改过截止，这件事在 SQL 里做要多一层 join 和 coalesce，
换来的只是几百行的扫描。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import api_error
from app.api.filters import parse_instant
from app.core.config import settings
from app.db.session import get_db
from app.integrations.canvas.client import CanvasClient
from app.models.canvas_item import CanvasItem, ItemKind
from app.models.course import Course
from app.models.planner import PlannerItem, PlannerKind, PlannerOverlay
from app.models.system import SyncState
from app.models.tasks import TaskList, UserTask
from app.schemas.planner import (
    PlannerItemOut,
    PlannerItemUpdate,
    PlannerStatusOut,
    PlannerSyncState,
    PlannerUpdateResult,
)
from app.services import planner, runtime_settings
from app.services import tasks as task_service

router = APIRouter(prefix="/planner", tags=["planner"])

def _naive(value: datetime | None) -> datetime | None:
    """库里读出来的是 naive UTC，请求里解析出来的是 aware —— 比较前统一成 naive。"""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


def _display_date(item: PlannerItem, overlay: PlannerOverlay | None) -> datetime | None:
    """显示和排序用的时刻：用户改过的时间优先，其次 Canvas 的截止 / 待办日期 / 开始。"""
    if overlay is not None:
        if overlay.due_at is not None:
            return overlay.due_at
        if overlay.start_at is not None:
            return overlay.start_at
    return item.due_at or item.todo_date or item.start_at or item.plannable_date


def _to_out(
    item: PlannerItem,
    overlay: PlannerOverlay | None,
    course: Course | None,
    item_due: datetime | None,
) -> PlannerItemOut:
    return PlannerItemOut(
        id=item.id,
        item_key=item.item_key,
        kind=item.kind,
        plannable_type=item.plannable_type,
        course_id=item.course_id,
        course_code=course.course_code if course else None,
        course_name=course.name if course else None,
        color=planner.course_color(course),
        title=(overlay.title if overlay and overlay.title else item.title),
        canvas_title=item.title,
        date=_display_date(item, overlay),
        due_at=item.due_at,
        my_due_at=overlay.due_at if overlay else None,
        item_due_at=item_due,
        todo_date=item.todo_date,
        start_at=(overlay.start_at if overlay and overlay.start_at else item.start_at),
        end_at=(overlay.end_at if overlay and overlay.end_at else item.end_at),
        canvas_start_at=item.start_at,
        canvas_end_at=item.end_at,
        time_modified=bool(overlay and (overlay.due_at or overlay.start_at or overlay.end_at)),
        all_day=item.all_day,
        all_day_date=item.all_day_date,
        location_name=(overlay.location if overlay and overlay.location else item.location_name),
        canvas_location=item.location_name,
        location_address=item.location_address,
        points_possible=item.points_possible,
        html_url=item.html_url,
        description=item.description,
        series_uuid=item.series_uuid,
        submitted=item.submitted,
        graded=item.graded,
        missing=item.missing,
        late=item.late,
        excused=item.excused,
        has_feedback=item.has_feedback,
        new_activity=item.new_activity,
        canvas_marked_complete=item.canvas_marked_complete,
        completed=planner.is_completed(item, overlay),
        completed_override=overlay.completed if overlay else None,
        hidden=bool(overlay and overlay.hidden),
        note=overlay.note if overlay else None,
        important=bool(overlay and overlay.important),
        deleted_on_canvas=item.deleted_on_canvas,
    )


async def _item_due_dates(db: AsyncSession, items: list[PlannerItem]) -> dict[str, datetime]:
    """作业 / 测验本身的截止（课程作业列表里那份），按 item_key 返回。"""
    wanted = {
        ItemKind.ASSIGNMENT: [i.plannable_id for i in items if i.kind == PlannerKind.ASSIGNMENT],
        ItemKind.QUIZ: [i.plannable_id for i in items if i.kind == PlannerKind.QUIZ],
    }
    out: dict[str, datetime] = {}
    for kind, ids in wanted.items():
        if not ids:
            continue
        rows = await db.execute(
            select(CanvasItem.canvas_id, CanvasItem.due_at).where(
                CanvasItem.kind == kind, CanvasItem.canvas_id.in_(ids)
            )
        )
        ptype = "assignment" if kind == ItemKind.ASSIGNMENT else "quiz"
        for canvas_id, due in rows.all():
            if due is not None:
                out[planner.item_key(ptype, canvas_id)] = due
    return out


async def _serialize(db: AsyncSession, items: list[PlannerItem]) -> list[PlannerItemOut]:
    keys = [i.item_key for i in items] or [""]
    overlays = {
        o.item_key: o
        for o in (
            await db.execute(select(PlannerOverlay).where(PlannerOverlay.item_key.in_(keys)))
        ).scalars()
    }
    course_ids = {i.course_id for i in items if i.course_id}
    courses = {
        c.id: c
        for c in (
            await db.execute(select(Course).where(Course.id.in_(course_ids or {0})))
        ).scalars()
    }
    item_dues = await _item_due_dates(db, items)
    return [
        _to_out(i, overlays.get(i.item_key), courses.get(i.course_id), item_dues.get(i.item_key))
        for i in items
    ]


@router.get("/items", response_model=list[PlannerItemOut])
async def list_items(
    start: str | None = Query(None, description="只看这个时刻之后的，ISO 时刻"),
    end: str | None = Query(None, description="只看这个时刻之前的，ISO 时刻"),
    kind: list[str] = Query(default_factory=list, description="类型，可重复"),
    course: list[int] = Query(default_factory=list, description="课程 id，可重复"),
    include_hidden: bool = Query(False, description="连隐藏的一起给"),
    include_completed: bool = Query(True, description="连已完成的一起给"),
    undated: bool = Query(True, description="没有日期的也给"),
    projections: bool = Query(
        True, description="带上重复任务往后几次的预览（日历要，任务页不要）"
    ),
    db: AsyncSession = Depends(get_db),
) -> list[PlannerItemOut]:
    stmt = (
        select(PlannerItem)
        .outerjoin(Course, Course.id == PlannerItem.course_id)
        # 没在同步的课不出现；不挂课程的（用户自己的 Canvas To Do）照常出现
        .where(or_(PlannerItem.course_id.is_(None), Course.sync_enabled.is_(True)))
    )
    if kind:
        stmt = stmt.where(PlannerItem.kind.in_(kind))
    if course:
        stmt = stmt.where(PlannerItem.course_id.in_(course))
    items = list((await db.execute(stmt)).scalars().all())
    out = await _serialize(db, items)
    if not kind or PlannerKind.TASK in kind:
        out += await _user_tasks(db, course, projections)

    lo, hi = _naive(parse_instant(start)), _naive(parse_instant(end))
    kept: list[PlannerItemOut] = []
    for row in out:
        if row.hidden and not include_hidden:
            continue
        if row.completed and not include_completed:
            continue
        when = _naive(row.date)
        if when is None:
            if undated:
                kept.append(row)
            continue
        if lo is not None and when < lo:
            continue
        if hi is not None and when > hi:
            continue
        kept.append(row)
    # 有日期的按时间，没日期的排最后
    kept.sort(key=lambda r: (_naive(r.date) is None, _naive(r.date) or datetime.max, r.title))
    return kept


async def _user_tasks(
    db: AsyncSession, course: list[int], with_projections: bool
) -> list[PlannerItemOut]:
    """自建任务，和 Canvas 条目同一个形状。课程筛选对它们也生效。"""
    stmt = select(UserTask).options(selectinload(UserTask.steps))
    if course:
        stmt = stmt.where(UserTask.course_id.in_(course))
    tasks = list((await db.execute(stmt)).scalars().all())
    if not tasks:
        return []
    courses = {
        c.id: c
        for c in (
            await db.execute(
                select(Course).where(
                    Course.id.in_({t.course_id for t in tasks if t.course_id} or {0})
                )
            )
        ).scalars()
    }
    lists = {lst.id: lst for lst in (await db.execute(select(TaskList))).scalars()}
    out: list[PlannerItemOut] = []
    for task in tasks:
        c, lst = courses.get(task.course_id), lists.get(task.list_id)
        out.append(task_service.to_out(task, c, lst))
        if with_projections:
            out += task_service.projections(task, c, lst)
    return out


@router.patch("/items/{item_id}", response_model=PlannerUpdateResult)
async def update_item(
    item_id: int, payload: PlannerItemUpdate, db: AsyncSession = Depends(get_db)
) -> PlannerUpdateResult:
    item = await db.get(PlannerItem, item_id)
    if item is None:
        raise api_error(404, "planner_item_not_found")

    overlay = (
        await db.execute(select(PlannerOverlay).where(PlannerOverlay.item_key == item.item_key))
    ).scalar_one_or_none()
    if overlay is None:
        overlay = PlannerOverlay(item_key=item.item_key)
        db.add(overlay)

    # 字段没传 = 不动；传了 null = 清掉
    fields = payload.model_fields_set
    if "hidden" in fields:
        overlay.hidden = bool(payload.hidden)
    if "completed" in fields:
        overlay.completed = planner.completion_override(item, payload.completed)
    if "title" in fields:
        overlay.title = (payload.title or "").strip() or None
    if "due_at" in fields:
        overlay.due_at = payload.due_at
    if "start_at" in fields:
        overlay.start_at = payload.start_at
    if "end_at" in fields:
        overlay.end_at = payload.end_at
    if "location" in fields:
        overlay.location = (payload.location or "").strip() or None
    if "note" in fields:
        overlay.note = (payload.note or "").strip() or None
    if "important" in fields:
        overlay.important = bool(payload.important)

    writeback = "off"
    # 只有用户明确勾 / 取消时才回写。"恢复跟随 Canvas"（null）没有东西可写
    if "completed" in fields and payload.completed is not None:
        rt = await runtime_settings.load(db)
        if rt.planner_writeback and settings.canvas_configured:
            try:
                async with CanvasClient(
                    settings.canvas_base_url, settings.canvas_access_token
                ) as client:
                    await planner.writeback_completion(client, item, payload.completed)
                writeback = "ok"
            except Exception:  # noqa: BLE001  本地改动照常保存，只是告诉前端没写成
                writeback = "failed"

    await db.commit()
    await db.refresh(item)
    out = (await _serialize(db, [item]))[0]
    return PlannerUpdateResult(item=out, writeback=writeback)


@router.get("/status", response_model=PlannerStatusOut)
async def planner_status(db: AsyncSession = Depends(get_db)) -> PlannerStatusOut:
    rows = {
        s.resource_key: s
        for s in (
            await db.execute(
                select(SyncState).where(
                    SyncState.resource_key.in_([planner.ITEMS_STATE, planner.EVENTS_STATE])
                )
            )
        ).scalars()
    }

    def state(key: str) -> PlannerSyncState:
        s = rows.get(key)
        if s is None:
            return PlannerSyncState()
        return PlannerSyncState(
            count=s.items_seen,
            status=s.status,
            last_success_at=s.last_success_at,
            last_synced_at=s.last_synced_at,
            error=s.error_message,
        )

    return PlannerStatusOut(items=state(planner.ITEMS_STATE), events=state(planner.EVENTS_STATE))
