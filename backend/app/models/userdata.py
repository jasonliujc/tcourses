"""用户自己整理出来的东西：星标和手动分组。

**核心约束：这是用户的数据，不跟着同步走。** 自动分组（`file_groups`）每轮同步
都会被推倒重建，用户手动整理的结果绝不能混在里面陪葬 —— 所以单独立表，
同步流程只允许"对齐"，不允许"重建"。

一个文件被改了，用户这边跟着更新；一个文件从 Canvas 上没了，用户这边**保留原样**，
只是打上"已从 Canvas 删除"的标记。唯一会真的删掉用户数据的，是用户自己
取消同步这门课 —— 那是他明确说了"这门课我不要了"。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# 星标在界面上是一个分组，但库里是 UserFile 上的一个布尔值。
# 做成布尔值而不是"每门课自动建一个特殊分组"，省掉了一堆维护那行记录的边界情况
# （建课时要建、删组时要拦、改名时要拦），而且"查所有星标文件"就是一个 where。
STARRED_GROUP_ID = -1  # 前端用这个伪 id 表示星标分组


class UserFile(Base, TimestampMixin):
    """用户标记过的一份资料（星标了，或者加进了某个分组）。文件和外部链接都算。

    锚点是 `resource_key` 而不是本地的 `course_files.id`：本地行会因为重新同步、
    清理内联图片等原因换 id，而 `file:<canvas 文件 id>` / `link:<url 哈希>`
    是稳定的。`file_id` 只是一个方便查询的活指针，文件行没了就置空，
    靠每轮同步后的对齐重新接上。

    锚点原来是 `canvas_file_id`（一个整数）。外部链接根本没有 Canvas 文件 id，
    所以换成和 `course_files.resource_key` 同一套字符串身份 —— 两边用同一个词，
    对齐逻辑才只有一条路径。

    `display_name` 是快照。正常情况下界面读的是 `course_files` 里的实时值，
    这份快照是兜底 —— 万一活指针断了，用户至少还知道自己收藏的是什么。
    """

    __tablename__ = "user_files"
    __table_args__ = (UniqueConstraint("course_id", "resource_key", name="uq_user_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 课程被取消同步时，用户在这门课下的整理结果一并删除 —— 这是用户明确的意思
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    resource_key: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    # 活指针。SET NULL 而不是 CASCADE：文件行没了，用户的记录必须活下来
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_files.id", ondelete="SET NULL"), index=True
    )

    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    is_starred: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    # 用户确实看过**这一份**资料的时刻（下载了、打开了链接、看了详情）。
    #
    # 和 `CourseViewState` 那条按课程的水位线是两回事，两者都需要：水位线管的是
    # "这门课我扫过一眼了"，这一列管的是"这个东西我专门点开过"。一份资料在
    # 水位线之后又被 Canvas 改了，它会重新变成"未看"，而这一列的时刻也就自然
    # 比新的变更时间旧了 —— 判定是比时刻，不是比布尔值。
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 最后一次和 course_files 对齐上的时刻，排查"为什么这条断了"用
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships = relationship(
        "UserGroupMember", back_populates="user_file", cascade="all, delete-orphan"
    )


class CourseViewState(Base, TimestampMixin):
    """用户上次"看过"某门课资源列表的时刻 —— 全局「更新」靠它判定。

    **为什么按课程记，而不是记一条全局的。** 用户常常是在课程页看文件的，
    而不是从全局页进去。只记一条全局水位线的话，他在某门课的页面把这周的
    讲义全看完了，全局「更新」里那些东西还在 —— 一个永远清不掉的红点。

    **为什么不逐个文件记。** 在列表里扫一眼就算看过，逐个记就得在前端判断
    "哪几行真的进入了视野"；而不记的话「更新」会长期积压几百条。按课程记
    取的是中间那条线：打开某门课的资源页 = 这门课在那一刻之前的变动都算看过。
    真正点开过的单份资料另有 `UserFile.seen_at`。

    行是在**这门课第一次同步完**的时候建的，初值就是那一刻 —— 所以首次同步
    收进来的几百个文件不会全部变成"更新"，和"首次同步不产生提醒"是同一条规则。
    没有这一行时一律算"没有更新"：宁可少报，不可把整个库报成新的。
    """

    __tablename__ = "course_view_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: 这门课第一次同步完的时刻，之后不再变。那一轮收进来的东西"第一次收到"的时间
    #: 就是同步那一刻，不代表它是新的 —— 「今天」「本周」只看它在 Canvas 上的时间。
    #: 不用 created_at：那是数据库填的整秒，和带微秒的 first_seen_at 比会差出一秒
    first_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserGroup(Base, TimestampMixin):
    """用户手建的分组。名字由用户定，重命名和删除都只影响这张表。"""

    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("course_id", "name", name="uq_user_group_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    members = relationship(
        "UserGroupMember", back_populates="group", cascade="all, delete-orphan"
    )


class UserGroupMember(Base):
    __tablename__ = "user_group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_file_id", name="uq_user_group_file"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("user_groups.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_file_id: Mapped[int] = mapped_column(
        ForeignKey("user_files.id", ondelete="CASCADE"), index=True, nullable=False
    )

    group = relationship("UserGroup", back_populates="members")
    user_file = relationship("UserFile", back_populates="memberships")
