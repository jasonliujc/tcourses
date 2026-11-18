from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class ItemKind(StrEnum):
    """Canvas 上会"更新"并需要提醒用户的内容类型。

    统一成一张表的理由：变更检测、通知流、前端时间线都可以走同一套逻辑；
    各类型的独有字段留在 raw JSON 里，需要时再提升为列。
    """

    ASSIGNMENT = "assignment"
    ANNOUNCEMENT = "announcement"  # 课程公告
    MESSAGE = "message"  # Canvas 收件箱会话（教授私信）
    PAGE = "page"  # 课程 wiki 页面
    QUIZ = "quiz"


class CanvasItem(Base, TimestampMixin):
    __tablename__ = "canvas_items"
    __table_args__ = (UniqueConstraint("kind", "canvas_id", name="uq_item_kind_canvas_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    canvas_id: Mapped[str] = mapped_column(String(100), index=True, nullable=False)

    # message 类型可能不属于任何课程 → 允许为空
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    body_html: Mapped[str | None] = mapped_column(Text)
    html_url: Mapped[str | None] = mapped_column(String(1000))
    author_name: Mapped[str | None] = mapped_column(String(300))

    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canvas_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 变更检测的核心：对参与比较的字段做哈希，哈希变了才产生通知
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Canvas 原始返回，方便以后提升新字段而无需重新同步
    raw: Mapped[dict | None] = mapped_column(JSON)

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 正文最后一次从 Canvas 拉回来的时刻。**不能用 updated_at 代替**：
    # 那个字段只在内容变化时才刷新，页面没改动的话永远停在第一次抓取，
    # 缓存过期后就再也续不上，每轮都得重抓。
    content_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_read: Mapped[bool] = mapped_column(default=False, nullable=False)

    course = relationship("Course", back_populates="items")
