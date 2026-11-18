from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class GroupKind(StrEnum):
    AUTO = "auto"  # 由规则管道生成
    MANUAL = "manual"  # 用户手建


class AssignedBy(StrEnum):
    AUTO = "auto"
    USER = "user"


class SourceKind(StrEnum):
    """文件在 Canvas 上"露面"的地方。

    教授把资料放哪儿全凭习惯：有人用 Files，有人只挂 Modules，有人直接贴在公告里。
    要做到"一门课的文件一个不漏"，就得把这些地方全扫一遍，并且记住每个文件是从哪儿来的。
    """

    FILES = "files"  # Canvas Files 页面
    MODULE = "module"  # 课程模块里的 File 条目 / ExternalUrl 条目
    ANNOUNCEMENT = "announcement"  # 公告的附件或正文链接
    PAGE = "page"  # 页面正文里的链接
    ASSIGNMENT = "assignment"  # 作业说明里的链接
    # 课程大纲。它既不是页面也不是作业，是课程对象上的一个字段，
    # 所以前面四路一个都扫不到 —— 而有的课**整门课的资料都堆在这里**
    # （实测某门课的大纲 12 万字符、91 个文件 + 36 个链接，其余来源全是 0）
    SYLLABUS = "syllabus"
    QUIZ = "quiz"  # 测验说明里的附件和链接
    # 普通讨论区的主题正文（不是公告）。Canvas 的 discussion_topics 一张表装两种
    # 东西：is_announcement=true 是公告，false 是师生都能回帖的讨论区
    DISCUSSION = "discussion"
    # 课程导航栏上的外部工具（Piazza / MarkUs / 教授自建的板块）。
    # 它不在任何正文里，是导航栏本身的一项
    NAV = "nav"


class Medium(StrEnum):
    """这一行是能下载的文件，还是要跳出去的外部链接。"""

    FILE = "file"
    LINK = "link"


#: 外部链接没有 Canvas 文件 id。用 0 而不是 NULL：老库里这一列是 NOT NULL，
#: 改成可空要重建整张 course_files，而重建会牵动 file_sources / user_files 的外键。
#: 真正的身份是 resource_key，这一列对链接来说没有意义。
NO_CANVAS_ID = 0


class CourseFile(Base, TimestampMixin):
    """课程里的一份资料：Canvas 文件，或教授贴在正文里的外部链接。

    元数据始终同步；文件本体是否下载到本地由 AUTO_DOWNLOAD_FILES 控制。
    """

    __tablename__ = "course_files"
    __table_args__ = (
        # **身份是 (课程, resource_key)，不是 canvas_id。**
        # canvas_id 原来是全局唯一的，于是同一个 Canvas 文件被两门课引用时
        # 两门课抢同一行，文件在界面上"跳课"。而链接压根没有 canvas_id。
        # 换成 resource_key（`file:<id>` / `link:<hash>`）之后两个问题一起解决，
        # 后续做全局文件管理时也有了一个跨文件和链接统一的身份。
        UniqueConstraint("course_id", "resource_key", name="uq_course_file_resource"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 没显式给的话就按"这是个 Canvas 文件"来定身份 —— 对文件来说这就是**定义**
    # （一个文件的身份就是它的 canvas file id）。链接没有 canvas_id，必须自己传。
    resource_key: Mapped[str] = mapped_column(
        String(300),
        index=True,
        nullable=False,
        default=lambda ctx: f"file:{ctx.get_current_parameters()['canvas_id']}",
    )
    medium: Mapped[str] = mapped_column(
        String(10), default=Medium.FILE, nullable=False, index=True
    )
    # 链接指向哪儿。存**原样**的 URL，不是规范化之后的 —— 规范化只用来算身份，
    # 给用户点的必须是教授贴的那一条
    external_url: Mapped[str | None] = mapped_column(String(2000))
    canvas_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )

    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(200))
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    # Canvas 里的原始文件夹路径，例如 "course files/Lectures/Week 3" —— 自动分组的最强信号
    canvas_folder_path: Mapped[str | None] = mapped_column(String(1000), index=True)
    download_url: Mapped[str | None] = mapped_column(String(2000))

    # 本地副本
    local_path: Mapped[str | None] = mapped_column(String(1000))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    canvas_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 界面上显示、也用来排序的那个日期 = 主来源的日期（公告用发布时间，其余回落到
    # 文件更新时间）。必须冗余在这里：排序要在数据库里做，而主来源在另一张表上，
    # 用 canvas_updated_at 排序会导致"显示的日期"和"排列顺序"对不上，看着就是乱序。
    display_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 元数据最后一次从 Canvas 核对的时刻。增量同步默认不重拉已知文件，
    # 没有这个字段的话文件名/大小改了永远发现不了 —— 靠它做超期强制复查。
    metadata_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted_on_canvas: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 只以正文 <img> 形式出现过的文件 —— 页面 banner、logo 这类装饰图。
    # 用户可以在设置里关掉"同步内联图片"，关掉后这些记录会被删除且不再收集。
    is_inline_image: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    # 用户手动整理过这个文件 → 自动分组管道不再改动它的归属
    organize_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 规则管道抽出的结构化特征（week/lecture 序号、类别猜测等），前端可展示"为什么这么分"
    features: Mapped[dict | None] = mapped_column(JSON)

    course = relationship("Course", back_populates="files")
    memberships = relationship(
        "FileGroupMember", back_populates="file", cascade="all, delete-orphan"
    )
    sources = relationship(
        "FileSource",
        back_populates="file",
        cascade="all, delete-orphan",
        order_by="FileSource.sort_hint",
    )


class FileSource(Base, TimestampMixin):
    """一个文件在 Canvas 上出现的一个位置。

    同一个 PDF 可能既在 Files 里、又挂在 Module "Week 3" 下、还被公告引用过 ——
    那就是一条 CourseFile 配三条 FileSource。界面上按来源分类看文件、给每个文件
    标注"它是从哪儿来的"，都依赖这张表。
    """

    __tablename__ = "file_sources"
    __table_args__ = (
        UniqueConstraint("file_id", "source_kind", "source_key", name="uq_file_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("course_files.id", ondelete="CASCADE"), index=True, nullable=False
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )

    source_kind: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    # 同一来源内的幂等键：module item id / 公告的 canvas_id / 文件夹路径
    source_key: Mapped[str] = mapped_column(String(300), nullable=False)

    # 容器名：module 名 / 公告标题 / 文件夹路径 —— 左侧分类树按它分组
    container_name: Mapped[str | None] = mapped_column(String(500))
    # 文件在该容器里的标题（module item 的 title 常和文件名不同）
    item_title: Mapped[str | None] = mapped_column(String(500))
    # 给用户看的一行标注，例如 "Week 1 · Slides from Class"、"08/24 公告 · 秋季答疑安排"
    label: Mapped[str | None] = mapped_column(String(600))
    html_url: Mapped[str | None] = mapped_column(String(1000))

    # 排序用日期：公告用发布时间，其他用文件更新时间
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # 在容器内的原始顺序（module position * 1000 + item position），保持和 Canvas 一致
    sort_hint: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    canvas_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("canvas_items.id", ondelete="SET NULL")
    )

    file = relationship("CourseFile", back_populates="sources")


class FileGroup(Base, TimestampMixin):
    """文件分组。auto 组由规则生成并带 rule_key 保证重复同步幂等；manual 组归用户所有。"""

    __tablename__ = "file_groups"
    __table_args__ = (UniqueConstraint("course_id", "rule_key", name="uq_group_course_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default=GroupKind.MANUAL, nullable=False)
    # 例如 "folder:Lectures/Week 3" / "pattern:week:3" / "type:slides"；manual 组为 None
    rule_key: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(String(1000))
    color: Mapped[str | None] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    course = relationship("Course", back_populates="groups")
    members = relationship("FileGroupMember", back_populates="group", cascade="all, delete-orphan")


class FileGroupMember(Base):
    """文件 ↔ 分组：多对多，一个文件可以同时在"Week 3"和"Slides"两个组里。"""

    __tablename__ = "file_group_members"
    __table_args__ = (UniqueConstraint("group_id", "file_id", name="uq_group_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("file_groups.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_id: Mapped[int] = mapped_column(
        ForeignKey("course_files.id", ondelete="CASCADE"), index=True, nullable=False
    )

    assigned_by: Mapped[str] = mapped_column(String(20), default=AssignedBy.AUTO, nullable=False)
    # 自动分组的置信度 + 命中的规则，用于前端解释和"低置信度待确认"筛选
    confidence: Mapped[float | None] = mapped_column(Float)
    matched_rule: Mapped[str | None] = mapped_column(String(200))

    group = relationship("FileGroup", back_populates="members")
    file = relationship("CourseFile", back_populates="memberships")
