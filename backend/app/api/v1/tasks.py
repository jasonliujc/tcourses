"""自建任务和清单。列表不在这里：它和 Canvas 条目一起从 `/planner/items` 出，
日历和任务页只认那一个接口。"""

import re
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import api_error
from app.db.session import get_db
from app.models.course import Course
from app.models.tasks import TaskList, UserTask
from app.schemas.planner import (
    PlannerItemOut,
    TaskCreate,
    TaskListIn,
    TaskListOut,
    TaskUpdate,
    TaskUpdateResult,
)
from app.services import tasks as task_service

router = APIRouter(tags=["tasks"])

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _valid_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise api_error(400, "invalid_date") from None


async def _load(db: AsyncSession, task_id: int) -> UserTask:
    task = (
        await db.execute(
            select(UserTask).where(UserTask.id == task_id).options(selectinload(UserTask.steps))
        )
    ).scalar_one_or_none()
    if task is None:
        raise api_error(404, "task_not_found")
    return task


async def _out(db: AsyncSession, task: UserTask) -> PlannerItemOut:
    course = await db.get(Course, task.course_id) if task.course_id else None
    lst = await db.get(TaskList, task.list_id) if task.list_id else None
    return task_service.to_out(task, course, lst)


async def _check_refs(db: AsyncSession, course_id: int | None, list_id: int | None) -> None:
    if course_id is not None and await db.get(Course, course_id) is None:
        raise api_error(404, "course_not_found")
    if list_id is not None and await db.get(TaskList, list_id) is None:
        raise api_error(404, "task_list_not_found")


# ---------- 任务 ----------


@router.post("/tasks", response_model=PlannerItemOut, status_code=201)
async def create_task(payload: TaskCreate, db: AsyncSession = Depends(get_db)) -> PlannerItemOut:
    title = payload.title.strip()
    if not title:
        raise api_error(400, "task_title_empty")
    await _check_refs(db, payload.course_id, payload.list_id)
    task = UserTask(
        title=title,
        note=(payload.note or "").strip() or None,
        due_date=_valid_date(payload.due_date),
        # 两个都给了以全天为准：界面上"全天"是一个开关，开着就没有时刻
        due_at=None if payload.due_date else payload.due_at,
        tz=payload.tz,
        course_id=payload.course_id,
        list_id=payload.list_id,
        important=payload.important,
        repeat=task_service.normalize_repeat(payload.repeat),
        steps=[],
    )
    task_service.replace_steps(task, [s.model_dump() for s in payload.steps])
    task_service.ensure_due_for_repeat(task)
    db.add(task)
    await db.commit()
    return await _out(db, await _load(db, task.id))


@router.patch("/tasks/{task_id}", response_model=TaskUpdateResult)
async def update_task(
    task_id: int, payload: TaskUpdate, db: AsyncSession = Depends(get_db)
) -> TaskUpdateResult:
    task = await _load(db, task_id)
    fields = payload.model_fields_set

    if "title" in fields:
        title = (payload.title or "").strip()
        if not title:
            raise api_error(400, "task_title_empty")
        task.title = title
    if "note" in fields:
        task.note = (payload.note or "").strip() or None
    if "tz" in fields:
        task.tz = payload.tz
    # 截止：全天和具体时刻二选一，设了一个就清掉另一个
    if "due_date" in fields:
        task.due_date = _valid_date(payload.due_date)
        if task.due_date:
            task.due_at = None
    if "due_at" in fields:
        task.due_at = payload.due_at
        if task.due_at:
            task.due_date = None
    if "course_id" in fields or "list_id" in fields:
        await _check_refs(
            db,
            payload.course_id if "course_id" in fields else None,
            payload.list_id if "list_id" in fields else None,
        )
        if "course_id" in fields:
            task.course_id = payload.course_id
        if "list_id" in fields:
            task.list_id = payload.list_id
    if "important" in fields:
        task.important = bool(payload.important)
    if "repeat" in fields:
        task.repeat = task_service.normalize_repeat(payload.repeat)
    if "steps" in fields and payload.steps is not None:
        task_service.replace_steps(task, [s.model_dump() for s in payload.steps])
    task_service.ensure_due_for_repeat(task)

    spawned = None
    if "completed" in fields:
        if payload.completed:
            spawned = await task_service.complete(db, task)
        else:
            # 取消完成不删已经生成的下一次：那一条用户可能已经改过了
            task.completed_at = None

    await db.commit()
    item = await _out(db, await _load(db, task.id))
    spawned_out = await _out(db, await _load(db, spawned.id)) if spawned else None
    return TaskUpdateResult(item=item, spawned=spawned_out)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)) -> None:
    task = await _load(db, task_id)
    await db.delete(task)
    await db.commit()


# ---------- 清单 ----------


async def _list_out(db: AsyncSession, lst: TaskList) -> TaskListOut:
    count = (
        await db.execute(
            select(func.count()).select_from(UserTask).where(UserTask.list_id == lst.id)
        )
    ).scalar_one()
    return TaskListOut(
        id=lst.id, name=lst.name, color=lst.color, sort_order=lst.sort_order, task_count=count
    )


@router.get("/task-lists", response_model=list[TaskListOut])
async def list_task_lists(db: AsyncSession = Depends(get_db)) -> list[TaskListOut]:
    lists = (
        await db.execute(select(TaskList).order_by(TaskList.sort_order, TaskList.id))
    ).scalars()
    return [await _list_out(db, lst) for lst in lists]


def _clean_list(payload: TaskListIn, require_name: bool) -> dict:
    out: dict = {}
    fields = payload.model_fields_set
    if "name" in fields or require_name:
        name = (payload.name or "").strip()
        if not name:
            raise api_error(400, "task_list_name_empty")
        out["name"] = name
    if "color" in fields:
        if payload.color and not _HEX.fullmatch(payload.color):
            raise api_error(400, "invalid_color")
        out["color"] = payload.color or None
    return out


@router.post("/task-lists", response_model=TaskListOut, status_code=201)
async def create_task_list(payload: TaskListIn, db: AsyncSession = Depends(get_db)) -> TaskListOut:
    values = _clean_list(payload, require_name=True)
    last = (await db.execute(select(func.max(TaskList.sort_order)))).scalar() or 0
    lst = TaskList(sort_order=last + 1, **values)
    db.add(lst)
    await db.commit()
    return await _list_out(db, lst)


@router.patch("/task-lists/{list_id}", response_model=TaskListOut)
async def update_task_list(
    list_id: int, payload: TaskListIn, db: AsyncSession = Depends(get_db)
) -> TaskListOut:
    lst = await db.get(TaskList, list_id)
    if lst is None:
        raise api_error(404, "task_list_not_found")
    for key, value in _clean_list(payload, require_name=False).items():
        setattr(lst, key, value)
    await db.commit()
    return await _list_out(db, lst)


@router.delete("/task-lists/{list_id}", status_code=204)
async def delete_task_list(list_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """删清单不删里面的任务：它们回到"没有清单"。"""
    lst = await db.get(TaskList, list_id)
    if lst is None:
        raise api_error(404, "task_list_not_found")
    await db.delete(lst)
    await db.commit()
