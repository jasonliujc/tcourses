"""用户自己建的任务（类似 Microsoft To Do）。

和 Canvas 同步来的条目分开存：那边每轮同步对齐，这边完全是用户的数据，
同步一行都不碰。两边在 `/planner/items` 合成一份给前端，日历和任务页
用同一张卡片、同一套筛选，只靠 `source` 区分。

**重复任务按 To Do 的方式做**：一条重复任务就是"当前这一次"，勾掉它，
它留在已完成里，同时按规则生成下一次（同一个 series_id）。日历里往后的几次
是现算的预览，不落库 —— 落库的话改一次规则就得把未来几十条全改一遍。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class TaskList(Base, TimestampMixin):
    """用户自建的清单，比如「生活」「实习申请」。课程本身已经是一种分类，
    清单装的是不属于任何课程的事。"""

    __tablename__ = "task_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    color: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UserTask(Base, TimestampMixin):
    __tablename__ = "user_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    # 归属：可以挂课程、可以放清单、也可以都不挂。删课程 / 删清单不删任务
    course_id: Mapped[int | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True
    )
    list_id: Mapped[int | None] = mapped_column(
        ForeignKey("task_lists.id", ondelete="SET NULL"), index=True
    )

    # 截止。二选一：只有日期（全天）用 due_date，具体到几点用 due_at（UTC）。
    # 全天的必须存纯日期 —— 存成"那天零点"的时刻，换个时区就滑到前一天
    due_date: Mapped[str | None] = mapped_column(String(10), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # 建任务时用户所在的时区。重复任务要按它算"下周同一时刻" ——
    # 按 UTC 加七天的话，跨夏令时那一周会差一个小时
    tz: Mapped[str | None] = mapped_column(String(60))

    important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 重复规则：{"freq": "daily|weekly|monthly|yearly", "interval": 1, "weekdays": [0..6]}
    repeat: Mapped[dict | None] = mapped_column(JSON)
    # 同一个重复系列的每一次共用一个 id
    series_id: Mapped[str | None] = mapped_column(String(40), index=True)

    steps = relationship(
        "TaskStep",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskStep.position",
    )


class TaskStep(Base):
    """任务里的一步（子任务）。"""

    __tablename__ = "task_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("user_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    task = relationship("UserTask", back_populates="steps")
