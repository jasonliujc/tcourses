"""用户可改的运行时设置。

改完立刻生效，不需要重启 —— 值存在数据库里，每轮同步开始时读一次。

两个同步开关（自动下载 / 内联图片）和「选择同步课程」行为一致：改动触发一次
「同步所有课程」，关掉/取消勾选时当场把对应的数据删干净。所以都要用户确认。
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import api_error
from app.core.config import settings as env_settings
from app.db.session import get_db
from app.integrations.canvas.client import CanvasAuthError, CanvasError
from app.models.course import Course
from app.models.file import CourseFile, SourceKind
from app.schemas.system import (
    CanvasConnectionCheck,
    CanvasConnectionIn,
    CanvasConnectionResult,
    CourseChoiceOut,
    CourseSelectionResult,
    CourseSelectionUpdate,
    ResultNote,
    SettingsOut,
    SettingsUpdate,
    SettingsUpdateResult,
)
from app.services import canvas_connection, purge, runtime_settings
from app.workers.scheduler import sync_manager

router = APIRouter(prefix="/settings", tags=["settings"])


def course_display_order():
    """按界面上**显示的那个名字**排序。

    界面显示的是 `course_code || name`，而排序一直用的是 `name` —— 两者常常
    完全不同（course_code 是 "DB301H1F 20269"，name 是 "Introduction to
    Databases"），于是边栏看上去毫无规律。lower() 让排序大小写无关，
    也不依赖 SQLite 特有的 NOCASE 排序规则。
    """
    return func.lower(func.coalesce(func.nullif(Course.course_code, ""), Course.name))


async def _course_counts(db: AsyncSession) -> tuple[int, int]:
    selected = (
        await db.execute(
            select(func.count()).select_from(Course).where(Course.sync_enabled.is_(True))
        )
    ).scalar_one()
    total = (await db.execute(select(func.count()).select_from(Course))).scalar_one()
    return selected, total


async def _inline_image_count(db: AsyncSession) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(CourseFile)
            .where(CourseFile.is_inline_image.is_(True))
        )
    ).scalar_one()


@router.get("", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db)) -> SettingsOut:
    rt = await runtime_settings.load(db)
    selected, total = await _course_counts(db)
    return SettingsOut(
        auto_download_files=rt.auto_download_files,
        sync_inline_images=rt.sync_inline_images,
        sync_links=rt.sync_links,
        sync_announcement_links=rt.sync_announcement_links,
        sync_discussions=rt.sync_discussions,
        sync_interval_minutes=rt.sync_interval_minutes,
        interval_choices=runtime_settings.INTERVAL_CHOICES,
        planner_writeback=rt.planner_writeback,
        canvas_base_url=canvas_connection.current_url(),
        canvas_configured=env_settings.canvas_configured,
        canvas_token_hint=canvas_connection.token_hint(env_settings.canvas_access_token),
        canvas_user_name=await canvas_connection.account_name(db) or None,
        demo_mode=env_settings.demo_mode,
        inline_image_count=await _inline_image_count(db),
        selected_course_count=selected,
        total_course_count=total,
    )


@router.patch("", response_model=SettingsUpdateResult)
async def update_settings(
    payload: SettingsUpdate, db: AsyncSession = Depends(get_db)
) -> SettingsUpdateResult:
    # 同步期间不许改设置：关掉"同步过往课程"会当场删课程，
    # 而同步可能正在往那些课里写数据
    if sync_manager.is_busy:
        raise api_error(409, "settings_locked")

    before = await runtime_settings.load(db)
    notes: list[ResultNote] = []
    removed_courses = 0
    removed_files = 0

    # 两个开关的行为完全一致，所以放在一个循环里，不再各写各的：
    #   改了     → 同步一次所有课程
    #   关掉     → 当场把对应的数据删干净
    # "开关关着，但相关数据还在库里"是最容易让人不信任这个界面的状态。
    changed = [
        key
        for key, value in (
            (runtime_settings.AUTO_DOWNLOAD_FILES, payload.auto_download_files),
            (runtime_settings.SYNC_INLINE_IMAGES, payload.sync_inline_images),
            (runtime_settings.SYNC_LINKS, payload.sync_links),
            (runtime_settings.SYNC_ANNOUNCEMENT_LINKS, payload.sync_announcement_links),
            (runtime_settings.SYNC_DISCUSSIONS, payload.sync_discussions),
        )
        if value is not None and value != getattr(before, key)
    ]
    link_keys = (runtime_settings.SYNC_LINKS, runtime_settings.SYNC_ANNOUNCEMENT_LINKS)
    for key in changed:
        value = getattr(payload, key)
        await runtime_settings.set_value(db, key, value)
        # 链接的收集范围变了 → 清掉链接的冷启动水位线。**两个方向都要**：
        # 打开之后冒出来的几百个链接不是"新消息"，是这次点击的直接结果
        if key in link_keys:
            await purge.reset_link_watermarks(db)
        if value:
            continue
        if key == runtime_settings.SYNC_INLINE_IMAGES:
            n = await purge.purge_inline_images(db)
            removed_files += n
            notes.append(ResultNote(key="inline_images_removed", params={"n": n}))
        elif key == runtime_settings.SYNC_LINKS:
            n = await purge.purge_links(db)
            removed_files += n
            notes.append(ResultNote(key="links_removed", params={"n": n}))
        elif key == runtime_settings.SYNC_ANNOUNCEMENT_LINKS:
            n = await purge.purge_announcement_links(db)
            removed_files += n
            notes.append(ResultNote(key="announcement_links_removed", params={"n": n}))
        elif key == runtime_settings.SYNC_DISCUSSIONS:
            n = await purge.purge_source_only(db, SourceKind.DISCUSSION)
            removed_files += n
            notes.append(ResultNote(key="discussion_resources_removed", params={"n": n}))
        else:
            n = await purge.purge_local_downloads(db)
            notes.append(ResultNote(key="local_copies_removed", params={"n": n}))

    if changed:
        notes.append(ResultNote(key="sync_started"))

    if payload.sync_interval_minutes is not None:
        await runtime_settings.set_value(
            db, runtime_settings.SYNC_INTERVAL_MINUTES, payload.sync_interval_minutes
        )
        notes.append(ResultNote(key="interval_rescheduled"))

    # 完成状态回写 Canvas。只改行为、不动数据，所以不用触发同步
    if payload.planner_writeback is not None:
        await runtime_settings.set_value(
            db, runtime_settings.PLANNER_WRITEBACK, payload.planner_writeback
        )

    await db.commit()

    # 必须在 commit 之后再触发：同步会用自己的 session 读设置，
    # 提交之前它读到的还是旧值，白跑一轮。
    #
    # 跑的是「同步所有课程」这条普通增量路径，不是"忽略所有缓存的全量重扫"：
    # 这三个开关影响的都是"收哪些东西"，而不是"已经收进来的对不对"。开启时新冒出来的
    # 文件不在 known_file_ids 里，照常会去补元数据；关闭时该删的上面已经当场删了。
    # 走全量重扫要 ~420 秒而不是 ~33 秒，换不到任何东西。
    sync_started = False
    if changed and env_settings.canvas_configured:
        sync_manager.mark_started()
        asyncio.create_task(sync_manager.run_sync())
        sync_started = True

    # 必须在 commit 之后重排：reschedule 用自己的 session 去读设置，
    # 提交之前它读到的还是旧间隔，排出来的时间是错的
    if payload.sync_interval_minutes is not None:
        await sync_manager.reschedule()
    rt = await runtime_settings.load(db)
    return SettingsUpdateResult(
        auto_download_files=rt.auto_download_files,
        sync_inline_images=rt.sync_inline_images,
        sync_links=rt.sync_links,
        sync_announcement_links=rt.sync_announcement_links,
        sync_discussions=rt.sync_discussions,
        sync_interval_minutes=rt.sync_interval_minutes,
        planner_writeback=rt.planner_writeback,
        next_run_at=sync_manager.next_run_at,
        removed_courses=removed_courses,
        removed_files=removed_files,
        sync_started=sync_started,
        notes=notes,
    )


# ---------- Canvas 连接 ----------


async def _check_connection(
    payload: CanvasConnectionIn, db: AsyncSession
) -> tuple[str, str, canvas_connection.Account | None, bool]:
    """解析表单，没变就不去问 Canvas；变了就用新凭据问一次"我是谁"。"""
    if env_settings.demo_mode:
        raise api_error(409, "demo_readonly")
    # 同步正拿着旧令牌在跑，中途换掉会让这一轮一半用旧的一半用新的
    if sync_manager.is_busy:
        raise api_error(409, "settings_locked")
    try:
        base_url = canvas_connection.normalize_url(payload.base_url)
    except ValueError:
        raise api_error(400, "invalid_canvas_url") from None
    token = (payload.token or "").strip() or env_settings.canvas_access_token
    if not token:
        raise api_error(400, "canvas_token_required")
    if not canvas_connection.is_changed(base_url, token):
        return base_url, token, None, False
    try:
        account = await canvas_connection.identify(base_url, token)
    except CanvasAuthError:
        raise api_error(400, "canvas_auth_failed") from None
    except CanvasError:
        raise api_error(400, "canvas_unreachable") from None
    return base_url, token, account, await canvas_connection.account_changed(db, account)


@router.post("/canvas/check", response_model=CanvasConnectionCheck)
async def check_canvas_connection(
    payload: CanvasConnectionIn, db: AsyncSession = Depends(get_db)
) -> CanvasConnectionCheck:
    """保存前先问一次：变没变、连上的是谁、是不是换了账号。界面据此决定怎么确认。"""
    _, _, account, switched = await _check_connection(payload, db)
    if account is None:
        return CanvasConnectionCheck(changed=False)
    return CanvasConnectionCheck(changed=True, user_name=account.name, account_changed=switched)


@router.put("/canvas", response_model=CanvasConnectionResult)
async def save_canvas_connection(
    payload: CanvasConnectionIn, db: AsyncSession = Depends(get_db)
) -> CanvasConnectionResult:
    """保存新的地址和令牌，然后同步所有课程。

    换了账号先把上一个账号的数据清掉，再同步 —— 否则两个账号的课混在一起。
    """
    base_url, token, account, switched = await _check_connection(payload, db)
    if account is None:
        return CanvasConnectionResult(changed=False)

    removed = await purge.purge_account(db) if switched else 0
    await canvas_connection.save(db, base_url, token, account)
    await db.commit()

    # commit 之后再触发：同步用自己的 session，提交前读到的还是旧库
    sync_manager.mark_started()
    asyncio.create_task(sync_manager.run_sync())
    return CanvasConnectionResult(
        changed=True,
        user_name=account.name,
        account_changed=switched,
        sync_started=True,
        removed_courses=removed,
    )


@router.get("/courses", response_model=list[CourseChoiceOut])
async def list_course_choices(db: AsyncSession = Depends(get_db)) -> list[CourseChoiceOut]:
    """「选择同步课程」的候选列表 —— Canvas 上能看到的**所有**课。

    只列在同步的那些就成了循环依赖：没在同步的课不会出现，用户也就没法把它勾回来。
    所以 `courses` 表存的是目录（`sync_courses` 每轮把在读和已结课两批都拉进来），
    这里直接读库，打开对话框是瞬时的，不用现去问 Canvas。
    """
    rows = (
        (
            await db.execute(
                select(Course)
                .where(Course.is_hidden.is_(False))
                .order_by(Course.is_concluded, course_display_order())
            )
        )
        .scalars()
        .all()
    )
    counts = {
        cid: n
        for cid, n in (
            await db.execute(
                select(CourseFile.course_id, func.count()).group_by(CourseFile.course_id)
            )
        ).all()
    }
    return [
        CourseChoiceOut(
            course_id=c.id,
            canvas_id=c.canvas_id,
            name=c.name,
            course_code=c.course_code,
            term_name=c.term_name,
            is_concluded=c.is_concluded,
            sync_enabled=c.sync_enabled,
            file_count=counts.get(c.id, 0),
        )
        for c in rows
    ]


@router.put("/courses", response_model=CourseSelectionResult)
async def update_course_selection(
    payload: CourseSelectionUpdate, db: AsyncSession = Depends(get_db)
) -> CourseSelectionResult:
    """保存用户勾选的课程。整份覆盖：不在列表里的一律取消同步。

    取消勾选会**当场清空**那门课的内容（文件、条目、分组、提醒、水位线、本地副本），
    只留目录行。清水位线不是顺手打扫 —— 用户以后把这门课勾回来时要重新算作
    首次同步，否则库里每个文件都会发一条"新文件"提醒。
    """
    if sync_manager.is_busy:
        raise api_error(409, "settings_locked")

    courses = (
        (await db.execute(select(Course).where(Course.is_hidden.is_(False)))).scalars().all()
    )
    wanted = set(payload.course_ids)
    unknown = wanted - {c.id for c in courses}
    if unknown:
        raise api_error(400, "unknown_courses", ids=", ".join(str(i) for i in sorted(unknown)))

    enabled = 0
    disabled = 0
    removed_courses = 0
    removed_files = 0
    notes: list[ResultNote] = []

    for course in courses:
        want = course.id in wanted
        if want == course.sync_enabled:
            continue
        course.sync_enabled = want
        if want:
            enabled += 1
        else:
            disabled += 1
            files = await purge.purge_course_content(db, course)
            if files or course.last_synced_at is not None:
                removed_courses += 1
                removed_files += files

    if enabled:
        notes.append(ResultNote(key="courses_enabled", params={"n": enabled}))
    if disabled:
        notes.append(
            ResultNote(
                key="courses_disabled",
                params={"n": disabled, "files": removed_files},
            )
        )

    await db.commit()

    # 必须在 commit 之后再触发：同步用自己的 session 读 sync_enabled，
    # 提交之前它读到的还是旧值
    sync_started = False
    if (enabled or disabled) and env_settings.canvas_configured:
        sync_manager.mark_started()
        asyncio.create_task(sync_manager.run_sync())
        sync_started = True
        notes.append(ResultNote(key="sync_started"))

    return CourseSelectionResult(
        enabled=enabled,
        disabled=disabled,
        removed_courses=removed_courses,
        removed_files=removed_files,
        sync_started=sync_started,
        notes=notes,
    )


@router.get("/storage-usage")
async def storage_usage() -> dict:
    """本地已下载文件占了多少空间 —— 打开自动下载前值得看一眼。"""
    root = Path(env_settings.storage_path)
    total = 0
    count = 0
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
                count += 1
    return {"file_count": count, "bytes": total, "path": str(root)}
