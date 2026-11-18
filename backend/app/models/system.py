from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class EventKind(StrEnum):
    NEW_ASSIGNMENT = "new_assignment"
    UPDATED_ASSIGNMENT = "updated_assignment"
    NEW_ANNOUNCEMENT = "new_announcement"
    NEW_MESSAGE = "new_message"
    UPDATED_PAGE = "updated_page"
    NEW_FILE = "new_file"
    UPDATED_FILE = "updated_file"
    NEW_LINK = "new_link"
    # 同一段锚文本指向了新的 URL —— 教授把链接换掉了
    UPDATED_LINK = "updated_link"
    DUE_SOON = "due_soon"
    SYNC_ERROR = "sync_error"


class NotificationEvent(Base, TimestampMixin):
    """给用户看的"有更新"事件流。同步时产出，前端 dashboard 消费。"""

    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True, nullable=False)

    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    canvas_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("canvas_items.id", ondelete="CASCADE")
    )
    file_id: Mapped[int | None] = mapped_column(ForeignKey("course_files.id", ondelete="CASCADE"))

    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    # 例如 {"changed_fields": ["due_at"], "old": "...", "new": "..."}
    payload: Mapped[dict | None] = mapped_column(JSON)

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 幂等键：同一个变化重复同步不会重复提醒
    dedup_key: Mapped[str | None] = mapped_column(String(300), unique=True, index=True)


class SyncState(Base, TimestampMixin):
    """每个同步资源的水位线，例如 resource_key = "course:1234:assignments"。"""

    __tablename__ = "sync_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(200), unique=True, index=True, nullable=False)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="idle", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    items_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_changed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 结构化附加信息。目前只有板块覆盖报告用它 —— 那份东西是一张
    # {板块: 状态/条数} 的表，塞进 error_message 那种自由文本里没法渲染
    payload: Mapped[dict | None] = mapped_column(JSON)
