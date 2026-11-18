"""自建任务：重复规则、完成后生成下一次、合成和 Canvas 条目同一个形状。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import DAILY, MONTHLY, WEEKLY, YEARLY, rrule
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.tasks import TaskList, TaskStep, UserTask
from app.schemas.planner import PlannerItemOut, StepOut
from app.services import planner

FREQS = {"daily": DAILY, "weekly": WEEKLY, "monthly": MONTHLY, "yearly": YEARLY}

#: 没挂课程、也没放清单的任务用的颜色
NEUTRAL_COLOR = "#64748b"
#: 日历里重复任务往后预览多远、最多几次
PROJECTION_DAYS = 180
PROJECTION_MAX = 60


def normalize_repeat(repeat: dict | None) -> dict | None:
    """校验并收拾重复规则。不认识的频率当成"不重复"，不报错 —— 规则是界面上
    几个固定选项生成的，走到这里说明是脏数据，丢掉比整条任务存不进去强。"""
    if not repeat or repeat.get("freq") not in FREQS:
        return None
    interval = max(1, min(99, int(repeat.get("interval") or 1)))
    out: dict = {"freq": repeat["freq"], "interval": interval}
    if repeat["freq"] == "weekly" and repeat.get("weekdays"):
        days = sorted({int(d) for d in repeat["weekdays"] if 0 <= int(d) <= 6})
        if days:
            out["weekdays"] = days
    return out


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _aware(value: datetime) -> datetime:
    """库里读出来的是 naive UTC。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _rule(repeat: dict, start: datetime) -> rrule:
    kwargs: dict = {}
    if repeat.get("weekdays"):
        kwargs["byweekday"] = repeat["weekdays"]
    return rrule(FREQS[repeat["freq"]], interval=repeat.get("interval", 1), dtstart=start, **kwargs)


def occurrences(
    task: UserTask, count: int, until: date | None = None
) -> list[tuple[str | None, datetime | None]]:
    """当前这一次之后的若干次，返回 [(due_date, due_at)]。

    带时刻的按任务自己的时区算：rrule 在墙上时间里推，再换回 UTC ——
    所以"每周一早上九点"跨过夏令时那一周还是九点。
    """
    if not task.repeat:
        return []
    out: list[tuple[str | None, datetime | None]] = []
    if task.due_date:
        start = datetime.fromisoformat(task.due_date)
        for occ in _rule(task.repeat, start).xafter(start, count=count):
            if until and occ.date() > until:
                break
            out.append((occ.date().isoformat(), None))
    elif task.due_at:
        zone = _zone(task.tz)
        start = _aware(task.due_at).astimezone(zone)
        for occ in _rule(task.repeat, start).xafter(start, count=count):
            if until and occ.date() > until:
                break
            out.append((None, occ.astimezone(UTC)))
    return out


def today_in(tz: str | None) -> str:
    return datetime.now(_zone(tz)).date().isoformat()


def ensure_due_for_repeat(task: UserTask) -> None:
    """重复任务必须有截止日期（To Do 也是这么做的）：设了重复却没日期，就从今天开始。"""
    if task.repeat and not task.due_date and not task.due_at:
        task.due_date = today_in(task.tz)


async def complete(db: AsyncSession, task: UserTask) -> UserTask | None:
    """勾掉一条。重复任务同时生成下一次，返回新生成的那条。"""
    if task.completed_at is not None:
        return None
    task.completed_at = datetime.now(UTC)
    nxt = occurrences(task, 1)
    if not nxt:
        return None
    due_date, due_at = nxt[0]
    if task.series_id is None:
        task.series_id = uuid.uuid4().hex
    spawned = UserTask(
        title=task.title,
        note=task.note,
        course_id=task.course_id,
        list_id=task.list_id,
        due_date=due_date,
        due_at=due_at,
        tz=task.tz,
        important=task.important,
        repeat=task.repeat,
        series_id=task.series_id,
        steps=[TaskStep(title=s.title, done=False, position=s.position) for s in task.steps],
    )
    db.add(spawned)
    await db.flush()
    return spawned


def replace_steps(task: UserTask, steps: list[dict]) -> None:
    """子步骤整份替换。前端每次把完整列表发回来 —— 增删改排序都是这一个口子，
    比给每一步单独开增删改接口简单得多，而一个任务最多十几步。"""
    existing = {s.id: s for s in task.steps}
    kept: list[TaskStep] = []
    for position, raw in enumerate(steps):
        title = (raw.get("title") or "").strip()
        if not title:
            continue
        step = existing.get(raw.get("id")) if raw.get("id") else None
        if step is None:
            step = TaskStep(title=title)
        step.title = title
        step.done = bool(raw.get("done"))
        step.position = position
        kept.append(step)
    task.steps = kept


def color_of(task: UserTask, course: Course | None, lst: TaskList | None) -> str:
    """挂了课程用课程色，放了清单用清单色，都没有用中性色。"""
    if course is not None:
        return planner.course_color(course) or NEUTRAL_COLOR
    if lst is not None and lst.color:
        return lst.color
    return NEUTRAL_COLOR


def to_out(
    task: UserTask,
    course: Course | None,
    lst: TaskList | None,
    *,
    due_date: str | None = None,
    due_at: datetime | None = None,
    virtual: bool = False,
) -> PlannerItemOut:
    """合成和 Canvas 条目同一个形状。`virtual` 是日历里往后预览的那几次，不落库。"""
    if not virtual:
        due_date, due_at = task.due_date, task.due_at
    all_day = due_date is not None
    # 全天的给一个"那天零点 UTC"当排序键；显示靠 all_day_date，不靠它
    sort_at = datetime.fromisoformat(due_date).replace(tzinfo=UTC) if all_day else due_at
    key = f"task:{task.id}"
    if virtual:
        key += f"@{due_date or (due_at.isoformat() if due_at else '')}"
    return PlannerItemOut(
        id=task.id,
        item_key=key,
        kind="task",
        plannable_type="user_task",
        source="user",
        course_id=task.course_id,
        course_code=course.course_code if course else None,
        course_name=course.name if course else None,
        color=color_of(task, course, lst),
        list_id=task.list_id,
        list_name=lst.name if lst else None,
        title=task.title,
        canvas_title=task.title,
        date=sort_at,
        due_at=due_at,
        all_day=all_day,
        all_day_date=due_date,
        completed=False if virtual else task.completed_at is not None,
        completed_at=None if virtual else task.completed_at,
        important=task.important,
        note=task.note,
        steps=[] if virtual else [StepOut(id=s.id, title=s.title, done=s.done) for s in task.steps],
        repeat=task.repeat,
        series_id=task.series_id,
        tz=task.tz,
        virtual=virtual,
    )


def projections(
    task: UserTask, course: Course | None, lst: TaskList | None
) -> list[PlannerItemOut]:
    """一条还没完成的重复任务，往后几次的预览。"""
    if task.completed_at is not None or not task.repeat:
        return []
    until = date.today() + timedelta(days=PROJECTION_DAYS)
    return [
        to_out(task, course, lst, due_date=d, due_at=a, virtual=True)
        for d, a in occurrences(task, PROJECTION_MAX, until)
    ]
