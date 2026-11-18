"""用户整理结果（星标 / 手动分组）的读写，以及每轮同步后的对齐。

这一层存在的理由只有一条：**把"用户整理的"和"同步下来的"彻底分开。**
自动分组每轮重建，用户的东西一行都不能动。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.course import Course
from app.models.file import CourseFile
from app.models.userdata import (
    STARRED_GROUP_ID,
    CourseViewState,
    UserFile,
    UserGroup,
    UserGroupMember,
)

__all__ = [
    "STARRED_GROUP_ID",
    "add_to_group",
    "annotations_for",
    "ensure_view_state",
    "group_counts",
    "mark_all_seen",
    "mark_course_seen",
    "mark_files_seen",
    "purge_course",
    "referenced_resource_keys",
    "relink_after_sync",
    "remove_from_group",
    "set_starred",
]

logger = logging.getLogger(__name__)


async def _entry_for(db: AsyncSession, file: CourseFile) -> UserFile:
    """拿到（必要时新建）某个文件的用户记录。"""
    entry = (
        await db.execute(
            select(UserFile).where(
                UserFile.course_id == file.course_id,
                UserFile.resource_key == file.resource_key,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        entry = UserFile(
            course_id=file.course_id,
            resource_key=file.resource_key,
            file_id=file.id,
            display_name=file.display_name,
            linked_at=datetime.now(UTC),
        )
        db.add(entry)
        await db.flush()
    elif entry.file_id != file.id:
        # 文件行换了 id（重新同步过），把活指针接回去
        entry.file_id = file.id
        entry.display_name = file.display_name
        entry.linked_at = datetime.now(UTC)
    return entry


async def _drop_if_unused(db: AsyncSession, entry: UserFile) -> None:
    """既没星标又不在任何分组里的记录留着没意义，顺手回收。

    不回收的话，用户星标又取消、加组又移出，库里会攒下一堆什么都不表示的空行，
    "这门课有多少条用户数据"这个数字也就不可信了。
    """
    if entry.is_starred or entry.seen_at is not None:
        return
    still_grouped = (
        await db.execute(
            select(func.count())
            .select_from(UserGroupMember)
            .where(UserGroupMember.user_file_id == entry.id)
        )
    ).scalar_one()
    if not still_grouped:
        await db.delete(entry)


# ---------- 星标 ----------


async def set_starred(db: AsyncSession, files: list[CourseFile], starred: bool) -> int:
    changed = 0
    for file in files:
        if starred:
            entry = await _entry_for(db, file)
            if not entry.is_starred:
                entry.is_starred = True
                changed += 1
        else:
            entry = (
                await db.execute(
                    select(UserFile).where(
                        UserFile.course_id == file.course_id,
                        UserFile.resource_key == file.resource_key,
                    )
                )
            ).scalar_one_or_none()
            if entry is not None and entry.is_starred:
                entry.is_starred = False
                changed += 1
                await _drop_if_unused(db, entry)
    await db.flush()
    return changed


# ---------- 看过了 ----------
#
# 两个口径，各管一件事（见 models/userdata.py 里的两段说明）：
#   按课程的水位线 = "这门课的列表我扫过一眼了"
#   按资料的 seen_at = "这一份我专门点开过"


async def ensure_view_state(db: AsyncSession, course_id: int) -> CourseViewState:
    """保证这门课有一条水位线，没有就按"现在"建。

    在**每轮同步的末尾**调用。首次同步完的那一刻成为初值，于是那一轮收进来的
    几百个文件不会全部变成"更新" —— 和"首次同步不产生提醒"是同一条规则。
    """
    state = (
        await db.execute(select(CourseViewState).where(CourseViewState.course_id == course_id))
    ).scalar_one_or_none()
    if state is None:
        now = datetime.now(UTC)
        state = CourseViewState(course_id=course_id, seen_at=now, first_synced_at=now)
        db.add(state)
        await db.flush()
    return state


async def mark_course_seen(db: AsyncSession, course_id: int) -> None:
    """把一门课的水位线推到现在。用户打开这门课的资源页就算。

    还没同步完过的课不建水位线：建了的话"第一次同步完"的时刻就会早于这一轮
    还没收完的文件，它们会被当成新东西。同步完时会以那一刻为初值建出来。
    """
    state = (
        await db.execute(select(CourseViewState).where(CourseViewState.course_id == course_id))
    ).scalar_one_or_none()
    if state is None:
        return
    state.seen_at = datetime.now(UTC)
    await db.flush()


async def mark_all_seen(db: AsyncSession) -> int:
    """全部标记为已看。返回涉及的课程数。

    只推**在同步的**课程：没在同步的课压根不出现在任何列表里，给它记水位线
    没有意义，而重新勾回来时它会当成首次同步重新建。
    """
    course_ids = list(
        (await db.execute(select(Course.id).where(Course.sync_enabled.is_(True))))
        .scalars()
        .all()
    )
    for course_id in course_ids:
        await mark_course_seen(db, course_id)
    return len(course_ids)


async def mark_files_seen(db: AsyncSession, files: list[CourseFile]) -> int:
    """把这几份资料标成"专门看过"。下载、打开链接、看详情都算。"""
    now = datetime.now(UTC)
    for file in files:
        entry = await _entry_for(db, file)
        entry.seen_at = now
    await db.flush()
    return len(files)


# ---------- 分组 ----------


async def add_to_group(db: AsyncSession, group: UserGroup, files: list[CourseFile]) -> int:
    existing = {
        uid
        for (uid,) in (
            await db.execute(
                select(UserGroupMember.user_file_id).where(UserGroupMember.group_id == group.id)
            )
        ).all()
    }
    added = 0
    for file in files:
        entry = await _entry_for(db, file)
        if entry.id in existing:
            continue
        db.add(UserGroupMember(group_id=group.id, user_file_id=entry.id))
        existing.add(entry.id)
        added += 1
    await db.flush()
    return added


async def remove_from_group(db: AsyncSession, group: UserGroup, files: list[CourseFile]) -> int:
    keys = [f.resource_key for f in files]
    entries = (
        (
            await db.execute(
                select(UserFile).where(
                    UserFile.course_id == group.course_id,
                    UserFile.resource_key.in_(keys or [""]),
                )
            )
        )
        .scalars()
        .all()
    )
    removed = 0
    for entry in entries:
        result = await db.execute(
            delete(UserGroupMember).where(
                UserGroupMember.group_id == group.id,
                UserGroupMember.user_file_id == entry.id,
            )
        )
        if result.rowcount:
            removed += result.rowcount
            await _drop_if_unused(db, entry)
    await db.flush()
    return removed


# ---------- 给列表接口用的批量查询 ----------


async def annotations_for(db: AsyncSession, file_ids: list[int]) -> dict[int, dict]:
    """一批文件的用户标记：`{course_files.id: {"starred": bool, "groups": [(id, name)]}}`。

    列表接口一次性查完再分发，避免每行一次查询。
    """
    if not file_ids:
        return {}
    rows = (
        await db.execute(
            select(UserFile.file_id, UserFile.is_starred, UserGroup.id, UserGroup.name)
            .outerjoin(UserGroupMember, UserGroupMember.user_file_id == UserFile.id)
            .outerjoin(UserGroup, UserGroup.id == UserGroupMember.group_id)
            .where(UserFile.file_id.in_(file_ids))
        )
    ).all()

    out: dict[int, dict] = {}
    for file_id, starred, group_id, group_name in rows:
        item = out.setdefault(file_id, {"starred": False, "groups": []})
        item["starred"] = item["starred"] or bool(starred)
        if group_id is not None:
            item["groups"].append((group_id, group_name))
    return out


async def group_counts(
    db: AsyncSession, course_id: int, only_files=None
) -> tuple[dict[int, int], int]:
    """返回 (每个分组的文件数, 星标数)。

    **只数活指针还在的记录。** 计数和列表必须用同一个口径：列表是
    `UserFile JOIN CourseFile` 出来的，断了链的记录列不出来；如果计数这边照数，
    侧边栏就会显示"3 个文件"而点进去只有 2 个 —— 数字一旦不可信，
    整个分组功能就没法用了。

    `only_files` 是一个 file_id 的子查询：界面上有筛选条件生效时，这些数字
    也必须只数命中的那些，理由和上面是同一条。
    """
    group_stmt = (
        select(UserGroupMember.group_id, func.count())
        .join(UserGroup, UserGroup.id == UserGroupMember.group_id)
        .join(UserFile, UserFile.id == UserGroupMember.user_file_id)
        .where(UserGroup.course_id == course_id, UserFile.file_id.is_not(None))
        .group_by(UserGroupMember.group_id)
    )
    star_stmt = (
        select(func.count())
        .select_from(UserFile)
        .where(
            UserFile.course_id == course_id,
            UserFile.is_starred.is_(True),
            UserFile.file_id.is_not(None),
        )
    )
    if only_files is not None:
        group_stmt = group_stmt.where(UserFile.file_id.in_(only_files))
        star_stmt = star_stmt.where(UserFile.file_id.in_(only_files))

    counts = {gid: n for gid, n in (await db.execute(group_stmt)).all()}
    starred = (await db.execute(star_stmt)).scalar_one()
    return counts, starred


# ---------- 同步后的对齐 ----------


async def relink_after_sync(db: AsyncSession, course: Course) -> tuple[int, int]:
    """把用户记录和刚同步下来的文件对一遍，返回 (重新接上的, 断开的)。

    同步不动用户数据，但文件那边会变，所以每轮跑完要对齐一次：

    - 文件还在（可能换了 id、改了名）→ 接上活指针，刷新快照
    - 文件没了 → 指针置空，用户的记录**原样留着**

    "没了"这件事本身很少发生：只要还有用户记录指着，同步就不会删那个文件行
    （见 `mark_or_delete`）。这里主要是兜底和自愈 —— 比如上一轮清理被打断，
    或者用户取消同步某门课又重新勾回来。
    """
    entries = (
        (await db.execute(select(UserFile).where(UserFile.course_id == course.id)))
        .scalars()
        .all()
    )
    if not entries:
        return 0, 0

    live = {
        key: (fid, name)
        for key, fid, name in (
            await db.execute(
                select(CourseFile.resource_key, CourseFile.id, CourseFile.display_name).where(
                    CourseFile.course_id == course.id
                )
            )
        ).all()
    }

    now = datetime.now(UTC)
    relinked = 0
    orphaned = 0
    for entry in entries:
        match = live.get(entry.resource_key)
        if match is None:
            if entry.file_id is not None:
                entry.file_id = None
                orphaned += 1
            continue
        file_id, display_name = match
        if entry.file_id != file_id or entry.display_name != display_name:
            entry.file_id = file_id
            # 文件改名了，用户那边跟着改 —— 收藏的是同一个文件，不是同一个名字
            entry.display_name = display_name
            relinked += 1
        entry.linked_at = now

    await db.flush()
    if orphaned:
        logger.info("course %s: %s 条用户记录失去对应文件", course.name, orphaned)
    return relinked, orphaned


async def referenced_resource_keys(db: AsyncSession, course_id: int) -> set[str]:
    """这门课里被用户标记过的资源身份（文件和链接都算）。

    清理时要绕开它们：用户星标/分组过的东西，宁可留一条打了"已从 Canvas 删除"
    标记的记录，也不能让它凭空消失。内联图片清理、链接被撤下都走这一条。
    """
    return set(
        (
            await db.execute(
                select(UserFile.resource_key).where(UserFile.course_id == course_id)
            )
        )
        .scalars()
        .all()
    )


async def purge_course(db: AsyncSession, course_id: int) -> tuple[int, int]:
    """删掉一门课下的全部用户数据，返回 (分组数, 文件标记数)。

    只有一种情况会走到这里：用户自己取消同步了这门课。那是明确的"我不要了"。
    """
    groups = (
        await db.execute(
            select(func.count()).select_from(UserGroup).where(UserGroup.course_id == course_id)
        )
    ).scalar_one()
    files = (
        await db.execute(
            select(func.count()).select_from(UserFile).where(UserFile.course_id == course_id)
        )
    ).scalar_one()
    await db.execute(delete(UserGroup).where(UserGroup.course_id == course_id))
    await db.execute(delete(UserFile).where(UserFile.course_id == course_id))
    # 水位线跟着一起删：重新勾回来时这门课算首次同步，不该把当时库里的东西
    # 全报成"更新" —— 和 `relink_after_sync` 之后重建水位线是同一条规则
    await db.execute(delete(CourseViewState).where(CourseViewState.course_id == course_id))
    await db.flush()
    return groups, files
