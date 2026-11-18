"""日历与待办的同步、合并、回写。

**数据来源两路**，都只读：

- `/planner/items`：主来源。作业、测验、讨论、事件、公告、页面待办、用户自己的
  Canvas To Do 全在里面，截止时间是对本人生效的那一份，带提交状态和完成标记。
  一学期两百多条，三个请求。
- `/calendar_events`（按课程）：给事件补字段。Planner 里的事件没有全天日期和
  重复系列；全天日期尤其要紧 —— 全天事件在 Canvas 里存成"那天零点"的 UTC 时刻，
  换个时区就滑到前一天。

**删除只在同步窗口之内判定。** Planner 是按日期范围查的，窗口外的条目这次没返回
不代表它被删了。窗口内没再出现的：用户没动过的直接删；**用户改过的留着**，
打上"已从 Canvas 删除"的标记 —— 改名、改时间、备注都是用户的功夫。
条目回来时标记自动摘掉，改动按 key 接上。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.canvas import mapper
from app.integrations.canvas.client import CanvasClient, CanvasError
from app.models.course import Course
from app.models.planner import PlannerItem, PlannerKind, PlannerOverlay
from app.models.system import SyncState

logger = logging.getLogger(__name__)

#: 同步窗口。往前半年够覆盖整个学期的"已交 / 缺交"，往后一年够覆盖期末考试
WINDOW_BACK = timedelta(days=180)
WINDOW_AHEAD = timedelta(days=365)

ITEMS_STATE = "planner:items"
EVENTS_STATE = "planner:events"

_KINDS = {
    "assignment": PlannerKind.ASSIGNMENT,
    "sub_assignment": PlannerKind.ASSIGNMENT,
    "quiz": PlannerKind.QUIZ,
    "discussion_topic": PlannerKind.DISCUSSION,
    "calendar_event": PlannerKind.EVENT,
    "announcement": PlannerKind.ANNOUNCEMENT,
    "wiki_page": PlannerKind.PAGE,
    "planner_note": PlannerKind.NOTE,
    "assessment_request": PlannerKind.PEER_REVIEW,
}


def item_key(plannable_type: str, plannable_id) -> str:
    return f"{plannable_type}:{plannable_id}"


def window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(UTC)
    return now - WINDOW_BACK, now + WINDOW_AHEAD


def _absolute(url: str | None, base_url: str) -> str | None:
    """Planner 给的 html_url 是相对路径（`/courses/1/assignments/2`）。"""
    if not url:
        return None
    return url if url.startswith("http") else f"{base_url.rstrip('/')}{url}"


def _float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def map_planner_item(raw: dict, base_url: str) -> dict:
    """Planner 的一条 → planner_items 的列。"""
    p = raw.get("plannable") or {}
    ptype = raw.get("plannable_type") or "unknown"
    subs = raw.get("submissions") if isinstance(raw.get("submissions"), dict) else {}
    override = raw.get("planner_override") or {}
    return {
        "item_key": item_key(ptype, raw.get("plannable_id") or p.get("id")),
        "plannable_type": ptype,
        "plannable_id": str(raw.get("plannable_id") or p.get("id")),
        "kind": _KINDS.get(ptype, PlannerKind.OTHER),
        "title": p.get("title") or p.get("name") or "",
        "html_url": _absolute(raw.get("html_url") or p.get("html_url"), base_url),
        "description": p.get("description") or p.get("details"),
        "plannable_date": mapper.parse_dt(raw.get("plannable_date")),
        "due_at": mapper.parse_dt(p.get("due_at")),
        "todo_date": mapper.parse_dt(p.get("todo_date")),
        "start_at": mapper.parse_dt(p.get("start_at")),
        "end_at": mapper.parse_dt(p.get("end_at")),
        "all_day": bool(p.get("all_day")),
        "location_name": p.get("location_name"),
        "location_address": p.get("location_address"),
        "points_possible": _float(p.get("points_possible")),
        "submitted": bool(subs.get("submitted")),
        "graded": bool(subs.get("graded")),
        "missing": bool(subs.get("missing")),
        "late": bool(subs.get("late")),
        "excused": bool(subs.get("excused")),
        "has_feedback": bool(subs.get("has_feedback")),
        "canvas_marked_complete": bool(override.get("marked_complete")),
        "canvas_dismissed": bool(override.get("dismissed")),
        "canvas_override_id": override.get("id"),
        "new_activity": bool(raw.get("new_activity")),
        "raw": raw,
    }


def map_calendar_event(raw: dict, base_url: str) -> dict:
    """课程日历里的一个事件 → planner_items 的列（只给事件独有、Planner 缺的那几项）。"""
    return {
        "item_key": item_key("calendar_event", raw.get("id")),
        "plannable_type": "calendar_event",
        "plannable_id": str(raw.get("id")),
        "kind": PlannerKind.EVENT,
        "title": raw.get("title") or "",
        "html_url": _absolute(raw.get("html_url"), base_url),
        "description": raw.get("description"),
        "plannable_date": mapper.parse_dt(raw.get("start_at")),
        "start_at": mapper.parse_dt(raw.get("start_at")),
        "end_at": mapper.parse_dt(raw.get("end_at")),
        "all_day": bool(raw.get("all_day")),
        "all_day_date": raw.get("all_day_date"),
        "location_name": raw.get("location_name"),
        "location_address": raw.get("location_address"),
        "series_uuid": raw.get("series_uuid"),
        "rrule": raw.get("rrule"),
    }


def _course_of(raw: dict) -> int | None:
    """Canvas 课程 id。Planner 用 course_id，日历事件用 context_code=course_123。"""
    if raw.get("course_id"):
        return int(raw["course_id"])
    code = raw.get("context_code") or ""
    if code.startswith("course_"):
        try:
            return int(code.removeprefix("course_"))
        except ValueError:
            return None
    return None


@dataclass
class PlannerSyncResult:
    items: int = 0
    events: int = 0
    created: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


async def _record(db: AsyncSession, key: str, count: int, error: str | None) -> None:
    state = (
        await db.execute(select(SyncState).where(SyncState.resource_key == key))
    ).scalar_one_or_none()
    if state is None:
        state = SyncState(resource_key=key)
        db.add(state)
    now = datetime.now(UTC)
    state.last_synced_at = now
    state.items_seen = count
    if error:
        state.status = "failed"
        state.error_message = error[:500]
    else:
        state.status = "ok"
        state.error_message = None
        state.last_success_at = now


async def sync_colors(db: AsyncSession, client: CanvasClient) -> int:
    """把 Canvas 里的课程颜色存成默认色。用户自己改过的（color）不动。"""
    colors = await client.get_colors()
    courses = (await db.execute(select(Course))).scalars().all()
    changed = 0
    for course in courses:
        value = colors.get(f"course_{course.canvas_id}")
        if value and value != course.canvas_color:
            course.canvas_color = value
            changed += 1
    return changed


async def sync_planner(
    db: AsyncSession, client: CanvasClient, course: Course | None = None
) -> PlannerSyncResult:
    """同步 Planner 条目和课程事件。`course` 给定时只同步那一门（单课同步用）。"""
    result = PlannerSyncResult()
    start, end = window()
    start_s, end_s = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    if course is None:
        try:
            await sync_colors(db, client)
        except CanvasError as exc:
            result.errors.append(f"课程颜色: {exc}"[:300])

    enabled = {
        c.canvas_id: c.id
        for c in (
            await db.execute(select(Course).where(Course.sync_enabled.is_(True)))
        ).scalars()
    }
    if course is not None:
        enabled = {course.canvas_id: course.id} if course.id in enabled.values() else {}

    rows: dict[str, dict] = {}
    items_error: str | None = None
    try:
        raw_items = await client.list_planner_items(
            start_s, end_s, [f"course_{course.canvas_id}"] if course is not None else None
        )
        for raw in raw_items:
            canvas_course = _course_of(raw)
            # 没在同步的课不收。不挂课程的（用户自己的 Canvas To Do）只在全量时收
            if canvas_course is not None and canvas_course not in enabled:
                continue
            if canvas_course is None and course is not None:
                continue
            values = map_planner_item(raw, client.base_url)
            values["course_id"] = enabled.get(canvas_course) if canvas_course else None
            rows[values["item_key"]] = values
        result.items = len(rows)
    except CanvasError as exc:
        items_error = str(exc)
        result.errors.append(f"待办: {exc}"[:300])

    events_error: str | None = None
    sem = asyncio.Semaphore(4)

    async def events_of(canvas_id: int) -> list[dict]:
        async with sem:
            return await client.list_course_events(canvas_id, start_s, end_s)

    canvas_ids = list(enabled)
    fetched = await asyncio.gather(
        *(events_of(cid) for cid in canvas_ids), return_exceptions=True
    )
    event_count = 0
    for canvas_id, events in zip(canvas_ids, fetched, strict=True):
        if isinstance(events, Exception):
            events_error = str(events)
            result.errors.append(f"日历事件 course_{canvas_id}: {events}"[:300])
            continue
        for raw in events:
            values = map_calendar_event(raw, client.base_url)
            values["course_id"] = enabled[canvas_id]
            # Planner 里已经有这条：只补事件独有的字段；没有就整条收进来
            existing = rows.get(values["item_key"])
            if existing is not None:
                for k in ("all_day_date", "series_uuid", "rrule", "description"):
                    if values.get(k):
                        existing[k] = values[k]
            else:
                rows[values["item_key"]] = values
            event_count += 1
    result.events = event_count

    # 两路都失败就别动库：拿到的是空集，按"窗口内没出现就删"会把所有条目删光
    if items_error is not None and events_error is not None and not rows:
        await _record(db, ITEMS_STATE, 0, items_error)
        await _record(db, EVENTS_STATE, 0, events_error)
        return result

    now = datetime.now(UTC)
    stmt = select(PlannerItem)
    if course is not None:
        stmt = stmt.where(PlannerItem.course_id == course.id)
    existing_rows = {r.item_key: r for r in (await db.execute(stmt)).scalars().all()}

    for key, values in rows.items():
        row = existing_rows.get(key)
        if row is None:
            row = PlannerItem(first_seen_at=now)
            db.add(row)
            result.created += 1
        for k, v in values.items():
            setattr(row, k, v)
        row.last_seen_at = now
        # 又出现了：之前打过的"已从 Canvas 删除"摘掉
        row.deleted_on_canvas = False

    # 窗口内、这次没出现的 → Canvas 上没了。只有 Planner 那一路成功时才判定：
    # 它失败的话 rows 里只有事件，按这个删会把所有作业都当成"没了"
    if items_error is None:
        gone = [
            row
            for key, row in existing_rows.items()
            if key not in rows and not row.deleted_on_canvas and _in_window(row, start, end)
        ]
        modified = await _modified_keys(db, [r.item_key for r in gone])
        for row in gone:
            # 用户改过的留着、打个标记：改名、改时间、备注都是用户的功夫，
            # 不能因为 Canvas 那边删了就一起没了。没改过的直接删
            if row.item_key in modified:
                row.deleted_on_canvas = True
            else:
                await db.delete(row)
                result.removed += 1

    await _record(db, ITEMS_STATE, result.items, items_error)
    await _record(db, EVENTS_STATE, result.events, events_error)
    await db.flush()
    return result


def _in_window(row: PlannerItem, start: datetime, end: datetime) -> bool:
    when = mapper.parse_dt(row.plannable_date)
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when is None or start <= when <= end


async def _modified_keys(db: AsyncSession, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    overlays = (
        await db.execute(select(PlannerOverlay).where(PlannerOverlay.item_key.in_(keys)))
    ).scalars()
    return {o.item_key for o in overlays if o.is_modified}


async def purge_course(db: AsyncSession, course_id: int) -> int:
    """取消同步某门课时，连同用户对这些条目的改动一起删 —— 那是用户明确的"不要了"。"""
    keys = list(
        (
            await db.execute(select(PlannerItem.item_key).where(PlannerItem.course_id == course_id))
        ).scalars()
    )
    if keys:
        await db.execute(delete(PlannerOverlay).where(PlannerOverlay.item_key.in_(keys)))
    result = await db.execute(delete(PlannerItem).where(PlannerItem.course_id == course_id))
    return result.rowcount or 0


# ---------- 读取时的合并 ----------

#: 课程没有 Canvas 颜色、用户也没设过时的兜底色。按课程 id 取，同一门课永远同一个色
FALLBACK_COLORS = [
    "#2563eb", "#16a34a", "#d97706", "#dc2626", "#7c3aed",
    "#0891b2", "#db2777", "#65a30d", "#ea580c", "#4f46e5",
]


def course_color(course: Course | None) -> str | None:
    """显示用的课程颜色：用户改过的 > Canvas 里的 > 兜底色。"""
    if course is None:
        return None
    return course.color or course.canvas_color or FALLBACK_COLORS[course.id % len(FALLBACK_COLORS)]


def canvas_completed(item: PlannerItem) -> bool:
    """Canvas 那边算不算完成：交了、评了分、免交、或者在 Canvas 里标了完成。"""
    return item.submitted or item.graded or item.excused or item.canvas_marked_complete


def is_completed(item: PlannerItem, overlay: PlannerOverlay | None) -> bool:
    """用户明确勾过 / 取消过就听用户的；否则跟随 Canvas。"""
    if overlay is not None and overlay.completed is not None:
        return overlay.completed
    return canvas_completed(item)


def completion_override(item: PlannerItem, value: bool | None) -> bool | None:
    """用户点了完成 / 取消之后，该存成什么。

    **和 Canvas 一致就不钉住**（存 None = 继续跟随 Canvas）。否则勾上又取消、
    回到原样的那一条会被钉成"未完成"，以后在 Canvas 上交了也不会自动变完成 ——
    用户点两下只是想回到原样，不是想接管它。
    """
    if value is None or value == canvas_completed(item):
        return None
    return value


async def writeback_completion(
    client: CanvasClient, item: PlannerItem, completed: bool
) -> None:
    """把完成状态写回 Canvas（planner override）。失败直接抛，由调用方决定怎么报。"""
    if item.canvas_override_id:
        data = await client.update_planner_override(item.canvas_override_id, completed)
    else:
        data = await client.create_planner_override(
            item.plannable_type, item.plannable_id, completed
        )
    item.canvas_override_id = data.get("id") or item.canvas_override_id
    item.canvas_marked_complete = bool(data.get("marked_complete", completed))
