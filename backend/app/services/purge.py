"""关掉某个同步开关、或取消勾选某门课时，把对应的数据清干净。

**不变量：开关关着（或课没勾选），它对应的数据就不该留在库里。** 做不到这条，
界面就会出现"这门课明明没在同步，边栏里还挂着"这种状态 —— 用户没法判断
到底哪个是真的，整个设置面板的可信度就没了。

两个地方调用，缺一不可：

- **设置页**：用户点完确认当场删，立刻看到数字变化。同步要跑几十秒，
  不能让人盯着一个没变的数字等。
- **每轮同步兜底**：设置页那次删只覆盖"用户亲手改开关"这一条路径。改 `.env`、
  升级带来的默认值变化都不经过它。更要命的是每门课的清理只跟着那门课的同步走，
  而关掉开关之后那些数据所在的课根本不会再被同步（过往课程关掉后就不拉了），
  清理永远轮不到它们。
"""

from __future__ import annotations

import logging
import shutil

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canvas_item import CanvasItem
from app.models.course import Course
from app.models.file import CourseFile, FileGroup, FileSource, Medium, SourceKind
from app.models.system import NotificationEvent, SyncState
from app.services import planner, storage, userdata

logger = logging.getLogger(__name__)


async def purge_course_content(db: AsyncSession, course: Course) -> int:
    """清空一门课的全部内容，返回删掉的文件数。**目录行留着。**

    用户取消同步某门课时调用。`courses` 那一行不删 —— 它是目录，
    「选择同步课程」界面还要靠它把这门课列出来给用户重新勾。

    **用户在这门课下的星标和手动分组也一起删。** 这是唯一会动用户数据的地方：
    平时同步再怎么跑都不许碰它们，但"我不同步这门课了"是他自己的明确决定。

    水位线（`sync_states`）必须一起删，这不是顺手打扫：判断"是不是首次同步"
    靠 `last_success_at is None`，留着的话用户重新勾选这门课时不算冷启动，
    库里每个文件都会发一条"新文件"提醒。
    """
    file_count = (
        await db.execute(
            select(func.count()).select_from(CourseFile).where(CourseFile.course_id == course.id)
        )
    ).scalar_one()

    groups, marks = await userdata.purge_course(db, course.id)
    await planner.purge_course(db, course.id)
    if groups or marks:
        logger.info("课程 %s: 删除 %s 个用户分组、%s 条用户标记", course.name, groups, marks)

    # 文件下面的 file_sources / file_group_members 靠外键级联带走
    await db.execute(delete(CourseFile).where(CourseFile.course_id == course.id))
    await db.execute(delete(CanvasItem).where(CanvasItem.course_id == course.id))
    await db.execute(delete(FileGroup).where(FileGroup.course_id == course.id))
    await db.execute(delete(NotificationEvent).where(NotificationEvent.course_id == course.id))
    await db.execute(
        delete(SyncState).where(SyncState.resource_key.like(f"course:{course.canvas_id}:%"))
    )

    folder = storage.course_dir(course.canvas_id, course.name)
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)

    # 同步状态也要归零，否则界面上还挂着"上次同步成功、N 个文件"
    course.last_synced_at = None
    course.last_sync_failed = False
    course.last_sync_error = None

    await db.flush()
    return file_count


async def purge_account(db: AsyncSession) -> int:
    """换了一个 Canvas 账号：上一个账号的东西全部清掉，返回删掉的课程数。

    不清的话两个账号的课程目录会混在一起，旧账号的课还勾着同步，新令牌去拉就是
    一片 401；换了学校的话 canvas_id 还会撞号。自建任务和清单不属于任何账号，
    留着 —— 挂过的课程靠外键置空。
    """
    from app.models.planner import PlannerItem, PlannerOverlay

    courses = (await db.execute(select(Course))).scalars().all()
    for course in courses:
        await purge_course_content(db, course)
        await db.delete(course)
    # 不属于任何课程的那些：Canvas 里的个人待办、全局水位线、提醒
    await db.execute(delete(PlannerOverlay))
    await db.execute(delete(PlannerItem))
    await db.execute(delete(NotificationEvent))
    await db.execute(delete(SyncState))
    await db.flush()
    return len(courses)


async def purge_disabled_courses(db: AsyncSession) -> tuple[int, int]:
    """兜底：把所有"没在同步"的课的内容清干净，返回 (课程数, 文件数)。

    正常路径是用户在界面上取消勾选时当场清。这里覆盖它漏掉的：直接改库、
    升级带来的默认值变化，以及取消勾选那次只清了一部分的情况。
    """
    courses = (
        (await db.execute(select(Course).where(Course.sync_enabled.is_(False)))).scalars().all()
    )
    touched = 0
    files = 0
    for course in courses:
        # 无条件跑一遍（几条匹配不到任何行的 DELETE，可以忽略），只是别把
        # "本来就干净"的课算进汇报数字里 —— 那会让日志每轮都在喊清理了 15 门课。
        # 不加条件是有意的：上一次清到一半被打断的课，这里还能补上。
        had_content = course.last_synced_at is not None
        removed = await purge_course_content(db, course)
        if removed or had_content:
            touched += 1
            files += removed
    return touched, files


async def purge_orphan_sync_states(db: AsyncSession) -> int:
    """删掉那些对应课程已经不存在的同步水位线。

    `sync_states` 是靠字符串 `course:{canvas_id}:...` 关联课程的，没有外键 ——
    删课程带不走它们。留着会造成两个具体的错误，都在"删掉再加回来"时发作：

    - **冷启动静默失效**。是不是首次同步靠 `last_success_at is None` 判断，
      而孤儿状态里这个值是有的。于是重新加回来的课会被当成"老课"，
      库里每一个文件都发一条"新文件"提醒 —— 实测一轮涌出 649 条。
    - **端点可用性缓存是陈的**。上次记着 Files 端点被禁，重新加回来的课直接
      沿用，白白跳过本该重试的来源。
    """
    live = set((await db.execute(select(Course.canvas_id))).scalars().all())
    states = (
        (await db.execute(select(SyncState).where(SyncState.resource_key.like("course:%"))))
        .scalars()
        .all()
    )
    removed = 0
    for state in states:
        parts = state.resource_key.split(":")
        try:
            canvas_id = int(parts[1])
        except (IndexError, ValueError):
            continue
        if canvas_id not in live:
            await db.delete(state)
            removed += 1
    await db.flush()
    return removed


async def purge_inline_images(db: AsyncSession) -> int:
    """删掉所有标记为内联装饰图的文件记录。"""
    rows = (
        (await db.execute(select(CourseFile).where(CourseFile.is_inline_image.is_(True))))
        .scalars()
        .all()
    )
    for file in rows:
        if file.local_path:
            storage.remove(file.local_path)
        await db.delete(file)
    await db.flush()
    return len(rows)


async def _delete_links(db: AsyncSession, rows: list[CourseFile]) -> int:
    """删资源，但用户整理过的只打标记不真删 —— 和文件同一条规矩。"""
    by_course: dict[int, set[str]] = {}
    removed = 0
    for row in rows:
        if row.course_id not in by_course:
            by_course[row.course_id] = await userdata.referenced_resource_keys(db, row.course_id)
        if row.resource_key in by_course[row.course_id]:
            row.is_deleted_on_canvas = True
            continue
        await db.delete(row)
        removed += 1
    await db.flush()
    return removed


async def reset_link_watermarks(db: AsyncSession) -> int:
    """清掉链接的冷启动水位线，让下一轮同步把链接当成"首次"。

    两个链接开关改动时都要调用（**开关两个方向都要**）。打开之后库里会一次性
    冒出几百个链接，那不是"新消息"，是用户刚点的那个开关的直接结果 ——
    全发一遍提醒只会把更新列表淹掉。
    """
    result = await db.execute(
        delete(SyncState).where(SyncState.resource_key.like("course:%:links"))
    )
    await db.flush()
    return result.rowcount or 0


async def purge_links(db: AsyncSession) -> int:
    """关掉「同步链接」→ 把所有外部链接记录删掉。"""
    rows = (
        (await db.execute(select(CourseFile).where(CourseFile.medium == Medium.LINK)))
        .scalars()
        .all()
    )
    return await _delete_links(db, list(rows))


async def purge_source_only(
    db: AsyncSession, source_kind: str, links_only: bool = False
) -> int:
    """删掉**只**出现在某一种来源里的资源。

    "只"是关键。同一个资源可能既在公告里贴过、也在某个页面上挂着 —— 那种情况下
    这条记录该留下：用户关掉的是"从这里收"，不是"删掉这个东西"。所以判断条件是
    "没有任何一条别的来源"，而不是"有这一种来源"。
    """
    other_sources = (
        select(FileSource.file_id)
        .where(FileSource.source_kind != source_kind)
        .scalar_subquery()
    )
    stmt = select(CourseFile).where(CourseFile.id.not_in(other_sources))
    if links_only:
        stmt = stmt.where(CourseFile.medium == Medium.LINK)
    rows = (await db.execute(stmt)).scalars().all()
    return await _delete_links(db, list(rows))


async def purge_announcement_links(db: AsyncSession) -> int:
    """关掉「同步公告中的链接」→ 只删仅仅出现在公告里的**链接**（文件不受影响）。"""
    return await purge_source_only(db, SourceKind.ANNOUNCEMENT, links_only=True)


async def purge_local_downloads(db: AsyncSession) -> int:
    """删掉所有下载到本地的文件副本，文件记录本身留着。

    元数据始终同步，这个开关关掉的只是"另存一份到本地"。所以
    `local_path` / `downloaded_at` 必须一起清空 —— 不清的话界面还以为本地有副本，
    点「打开」会指向一个已经不存在的路径。
    """
    rows = (
        (await db.execute(select(CourseFile).where(CourseFile.local_path.is_not(None))))
        .scalars()
        .all()
    )
    for file in rows:
        storage.remove(file.local_path or "")
        file.local_path = None
        file.downloaded_at = None
    await db.flush()
    return len(rows)
