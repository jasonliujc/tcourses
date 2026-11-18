from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Course(Base, TimestampMixin):
    """一门课。canvas_id 是与 Canvas 对齐的唯一锚点。"""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canvas_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    course_code: Mapped[str | None] = mapped_column(String(200))
    term_name: Mapped[str | None] = mapped_column(String(200))
    enrollment_state: Mapped[str | None] = mapped_column(String(50))

    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 用户偏好：是否在 dashboard 置顶 / 隐藏这门课
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 在边栏里藏起来。**和 sync_enabled 是两件事**：藏起来的课照常同步，
    # 只是不占边栏的位置 —— 学期结束但资料还要用的课就属于这一类，
    # 用取消同步来"清场"会把它的资料一起删掉。
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 边栏里的手动顺序。0 = 从没排过，这时按显示名的字母序排 ——
    # 所以不排也有一个稳定的顺序，排过之后手动顺序整体压在字母序前面。
    sidebar_sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 已结课（enrollment_state=completed）。只是一个标签 —— 结课不影响同不同步，
    # 那由 sync_enabled 单独决定。一门课结课了资料往往还要用很久。
    is_concluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 用户选择要不要同步这门课。
    #
    # **courses 表是"目录"，不是"已同步的东西"**：Canvas 上能看到的课全部入表，
    # 哪些真正拉内容由这个开关决定。不这么做的话，"选择同步课程"那个界面就没法
    # 列出没在同步的课 —— 每次打开都得现去 Canvas 问一遍，慢且不可靠。
    # 关掉时删的是这门课的**内容**（文件/条目/提醒/水位线），目录行留着。
    sync_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    # 课程颜色。Canvas 那边用户给每门课设过颜色（日历里就是这个色），同步时取来当默认；
    # 用户在这边改的存在 color 里，同步永远不碰它。显示时 color 优先。
    canvas_color: Mapped[str | None] = mapped_column(String(20))
    color: Mapped[str | None] = mapped_column(String(20))

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 上一轮同步失败过。下一轮对这门课走全量：忽略所有增量缓存重扫一遍，
    # 否则失败时拉了一半的数据会被当成"已经是最新的"，缺口永远补不上。
    last_sync_failed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 失败原因，直接展示给用户 —— 不然界面上只有一个红点，没人知道该怎么办
    last_sync_error: Mapped[str | None] = mapped_column(String(500))

    items = relationship("CanvasItem", back_populates="course", cascade="all, delete-orphan")
    files = relationship("CourseFile", back_populates="course", cascade="all, delete-orphan")
    groups = relationship("FileGroup", back_populates="course", cascade="all, delete-orphan")
