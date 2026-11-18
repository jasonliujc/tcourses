"""更新提醒流。第一阶段前端轮询这个接口；SSE 端点已留好位置。"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import api_error
from app.db.session import get_db
from app.models.course import Course
from app.models.system import NotificationEvent
from app.schemas.system import EventOut

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[EventOut])
async def list_events(
    unread_only: bool = False,
    course_id: int | None = None,
    kind: str | None = Query(None),
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[EventOut]:
    stmt = select(NotificationEvent, Course.name).join(
        Course, Course.id == NotificationEvent.course_id, isouter=True
    )
    if unread_only:
        stmt = stmt.where(NotificationEvent.is_read.is_(False))
    if course_id is not None:
        stmt = stmt.where(NotificationEvent.course_id == course_id)
    if kind:
        stmt = stmt.where(NotificationEvent.kind == kind)
    stmt = stmt.order_by(NotificationEvent.created_at.desc()).limit(limit)

    out: list[EventOut] = []
    for event, course_name in (await db.execute(stmt)).all():
        item = EventOut.model_validate(event)
        item.course_name = course_name
        out.append(item)
    return out


@router.get("/unread-count")
async def unread_count(db: AsyncSession = Depends(get_db)) -> dict:
    total = (
        await db.execute(
            select(func.count())
            .select_from(NotificationEvent)
            .where(NotificationEvent.is_read.is_(False))
        )
    ).scalar_one()
    return {"unread": total}


@router.post("/{event_id}/read", response_model=EventOut)
async def mark_read(event_id: int, db: AsyncSession = Depends(get_db)) -> EventOut:
    event = await db.get(NotificationEvent, event_id)
    if event is None:
        raise api_error(404, "event_not_found")
    event.is_read = True
    event.read_at = datetime.now(UTC)
    await db.commit()
    return EventOut.model_validate(event)


@router.post("/read-all")
async def mark_all_read(course_id: int | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    stmt = update(NotificationEvent).where(NotificationEvent.is_read.is_(False))
    if course_id is not None:
        stmt = stmt.where(NotificationEvent.course_id == course_id)
    result = await db.execute(stmt.values(is_read=True, read_at=datetime.now(UTC)))
    await db.commit()
    return {"marked": result.rowcount or 0}
