"""日历与待办：Canvas Planner 同步下来的条目，以及用户对它们的改动。

Canvas 网页版的 List View、手机 App 的 To Do 页、日历里的截止和事件，背后都是
同一个接口 `/planner/items`。它给的是**对这个学生生效**的那一份：分组、section
的截止时间差异已经算好，还带着本人的提交状态和"标记完成"。所以以它为准。

和文件系统同一个原则：**同步来的和用户改的分两张表**。`planner_items` 每轮同步
对齐，`planner_overlays` 是用户的隐藏 / 完成 / 改期 / 备注，同步一行都不碰 ——
否则下一轮同步刷新截止时间，用户手动改过的日期就被冲掉了。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PlannerKind(StrEnum):
    """界面上的类型。由 Canvas 的 plannable_type 归一而来 ——
    它的词（`discussion_topic`、`wiki_page`、`assessment_request`）直接给用户看不合适。"""

    ASSIGNMENT = "assignment"
    QUIZ = "quiz"
    DISCUSSION = "discussion"
    EVENT = "event"  # 日历事件：office hours、讲座、考试
    ANNOUNCEMENT = "announcement"
    PAGE = "page"  # 教授给页面设了待办日期
    NOTE = "note"  # 用户自己在 Canvas 里建的 To Do
    PEER_REVIEW = "peer_review"
    OTHER = "other"
    # 用户自己建的任务（不来自 Canvas）
    TASK = "task"


class PlannerItem(Base, TimestampMixin):
    """Canvas Planner 里的一条。身份是 `item_key` = `{plannable_type}:{plannable_id}`。"""

    __tablename__ = "planner_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_key: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    plannable_type: Mapped[str] = mapped_column(String(40), nullable=False)
    plannable_id: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    # 用户自己的 Canvas To Do 可以不挂课程
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    html_url: Mapped[str | None] = mapped_column(String(1000))
    description: Mapped[str | None] = mapped_column(Text)

    # Canvas 给每条算好的"这条属于哪个时刻"：作业是截止，事件是开始，公告是发布
    plannable_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    todo_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 全天事件的日期本身（YYYY-MM-DD）。全天事件在 Canvas 里存成"那天零点"的
    # UTC 时刻，换到别的时区会滑到前一天 —— 显示要用这个，不能用 start_at 换算
    all_day_date: Mapped[str | None] = mapped_column(String(10))
    location_name: Mapped[str | None] = mapped_column(String(500))
    location_address: Mapped[str | None] = mapped_column(String(500))
    points_possible: Mapped[float | None] = mapped_column(Float)

    # ---- 本人的提交状态（Planner 的 submissions 字段）----
    submitted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    graded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    missing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    late: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    excused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_feedback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ---- Canvas 那边的"标记完成 / 隐藏"（planner override）----
    canvas_marked_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canvas_dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 回写完成状态时要用：有就 PUT，没有就 POST 新建
    canvas_override_id: Mapped[int | None] = mapped_column(Integer)
    new_activity: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 重复事件（office hours 每周一次）。Canvas 每次发生都是一条独立事件，
    # 这两个字段只用来把它们认成"同一个系列"
    series_uuid: Mapped[str | None] = mapped_column(String(60))
    rrule: Mapped[str | None] = mapped_column(String(300))

    # Canvas 上已经没了，但用户改过它，所以留着（没改过的直接删）
    deleted_on_canvas: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    raw: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlannerOverlay(Base, TimestampMixin):
    """用户对一条同步条目的改动。锚点是 item_key 而不是 planner_items.id ——
    条目会因为同步重建而换 id，key 不会；条目从 Canvas 上消失后又回来，改动还在。"""

    __tablename__ = "planner_overlays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_key: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 三态：None = 跟随 Canvas（已提交 / Canvas 里标了完成就算完成），
    # True / False = 用户在这边明确勾过或取消过
    completed: Mapped[bool | None] = mapped_column(Boolean)
    title: Mapped[str | None] = mapped_column(String(1000))
    # 用户改过的时间。截止类的改 due_at（比如提前两天给自己留余量），
    # 事件改 start_at / end_at。Canvas 的原始时间原样保留在 planner_items 里
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(Text)
    important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def is_modified(self) -> bool:
        """用户有没有真的动过这一条。动过的条目从 Canvas 上消失也留着 ——
        用户在上面花的功夫（改名、改时间、备注）不能被一次同步抹掉。"""
        return bool(
            self.hidden
            or self.important
            or self.completed is not None
            or self.title
            or self.due_at
            or self.start_at
            or self.end_at
            or self.location
            or self.note
        )
