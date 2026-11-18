"""Canvas 同步编排。

Canvas 没有面向学生的通用 webhook（Live Events 需要机构管理员配置），
所以第一阶段用**轮询 + 内容哈希比对**：每轮把资源拉下来，和库里的哈希比，
变了才产生一条 NotificationEvent。架构上把"检测变化"和"产生通知"分开，
以后接入 Live Events 只需换掉数据来源。

冷启动保护：某个资源第一次同步时只入库、不产生通知，否则用户一进来就会被
几百条"新作业"淹没。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.canvas import mapper
from app.integrations.canvas.client import (
    CanvasClient,
    CanvasError,
    CanvasNotAvailable,
)
from app.models.canvas_item import CanvasItem, ItemKind
from app.models.course import Course
from app.models.file import NO_CANVAS_ID, CourseFile, FileSource, Medium
from app.models.system import EventKind, NotificationEvent, SyncState
from app.services import boards, links, planner, purge, runtime_settings, storage, userdata
from app.services.file_collector import CollectedLink, FileCollector, split_item_ref
from app.services.file_organizer import organize_course_files
from app.services.file_summary import pick_display_date

logger = logging.getLogger(__name__)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读出来的 datetime 没有时区，和 aware 的当前时间不能直接比。"""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)

# 补页面正文时的并发上限，和文件元数据补齐用同一个量级
_PAGE_FETCH_CONCURRENCY = 5

# 写多少条提交一次。SQLite 同时只允许一个写者，事务开太久其他并发课程就会
# 撞 "database is locked" —— 分批提交让写锁频繁释放。
_COMMIT_BATCH = 50

_NEW_EVENT_KIND = {
    ItemKind.ASSIGNMENT: EventKind.NEW_ASSIGNMENT,
    ItemKind.ANNOUNCEMENT: EventKind.NEW_ANNOUNCEMENT,
    ItemKind.MESSAGE: EventKind.NEW_MESSAGE,
    ItemKind.PAGE: EventKind.UPDATED_PAGE,
    ItemKind.QUIZ: EventKind.NEW_ASSIGNMENT,
}

_UPDATED_EVENT_KIND = {
    ItemKind.ASSIGNMENT: EventKind.UPDATED_ASSIGNMENT,
    ItemKind.ANNOUNCEMENT: EventKind.NEW_ANNOUNCEMENT,
    ItemKind.MESSAGE: EventKind.NEW_MESSAGE,
    ItemKind.PAGE: EventKind.UPDATED_PAGE,
    ItemKind.QUIZ: EventKind.UPDATED_ASSIGNMENT,
}


@dataclass
class SyncReport:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    courses_synced: int = 0
    items_created: int = 0
    items_updated: int = 0
    files_seen: int = 0
    # 本轮扫到的外部链接数（去重后的链接实体）
    links_seen: int = 0
    files_downloaded: int = 0
    # 因为用户关掉了"同步内联图片"而删掉的记录数
    files_removed: int = 0
    # 本轮判定为内联装饰图的文件数（不管收不收）
    inline_images_skipped: int = 0
    # 用户整理结果的对齐情况：重新接上的 / 失去对应文件的
    user_files_relinked: int = 0
    user_files_orphaned: int = 0
    events_created: int = 0
    # 因为端点已知不可用而省下的请求数
    skipped_requests: int = 0
    # 日历与待办：Planner 条目数 / 课程日历事件数
    planner_items: int = 0
    planner_events: int = 0
    errors: list[str] = field(default_factory=list)
    # 不是错误但用户需要知道的，比如"页面太多没扫全"
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: SyncReport) -> None:
        """把并发跑的分支结果汇总回主报告。"""
        self.courses_synced += other.courses_synced
        self.items_created += other.items_created
        self.items_updated += other.items_updated
        self.files_seen += other.files_seen
        self.links_seen += other.links_seen
        self.files_downloaded += other.files_downloaded
        self.files_removed += other.files_removed
        self.inline_images_skipped += other.inline_images_skipped
        self.user_files_relinked += other.user_files_relinked
        self.user_files_orphaned += other.user_files_orphaned
        self.events_created += other.events_created
        self.skipped_requests += other.skipped_requests
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "courses_synced": self.courses_synced,
            "items_created": self.items_created,
            "items_updated": self.items_updated,
            "files_seen": self.files_seen,
            "links_seen": self.links_seen,
            "files_downloaded": self.files_downloaded,
            "files_removed": self.files_removed,
            "inline_images_skipped": self.inline_images_skipped,
            "user_files_relinked": self.user_files_relinked,
            "user_files_orphaned": self.user_files_orphaned,
            "events_created": self.events_created,
            "skipped_requests": self.skipped_requests,
            "planner_items": self.planner_items,
            "planner_events": self.planner_events,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class CanvasSyncService:
    def __init__(self, db: AsyncSession, client: CanvasClient, on_progress=None):
        self.db = db
        self.client = client
        self.report = SyncReport()
        # 回调签名 (已完成课程数, 总课程数, 当前课程名)，供界面显示进度
        self._on_progress = on_progress
        # 每轮同步开始时加载一次，中途不变
        self.runtime: runtime_settings.RuntimeSettings | None = None
        # 本轮通过 pages 列表精确核对过的页面。列表带 updated_at，
        # 比对下来"没变"也算核对过 —— 不记的话这些页面会被当成"从没查过"反复重抓
        self._checked_page_ids: set[str] = set()

    # ---------- 入口 ----------

    async def sync_all(self) -> SyncReport:
        """并发同步所有课程。

        瓶颈是 Canvas 的响应延迟（实测每个请求 2-8 秒），不是本地计算 ——
        所以课程之间并发跑能直接把总时长压下去（实测 6 门课并发 6 是 2.0x）。
        每门课用独立 session，靠 SQLite 的 WAL + busy_timeout 扛并发写。
        """
        self.runtime = await runtime_settings.load(self.db)
        courses = await self.sync_courses()
        await self.db.commit()

        course_ids = [c.id for c in courses]
        total = len(course_ids)
        done = 0
        sem = asyncio.Semaphore(settings.sync_concurrency)
        # 同时有好几门课在跑，所以进度里报的是**一组**课名而不是一个。
        # 早先只存一个名字：谁开始谁覆盖、谁结束谁清空，于是任何一门课跑完
        # 都会把名字抹掉，界面上剩下光秃秃的"正在同步…"，而那时其实还有
        # 三门在跑。用 list 而不是 set 是为了保持进入顺序，名字不会来回跳。
        in_flight: list[str] = []

        async def run_one(course_id: int) -> None:
            nonlocal done
            async with sem:
                # 每门课自己的 session：AsyncSession 不能并发使用，
                # 而且一门课失败要 rollback，不能牵连其他课的未提交改动
                async with SessionLocal() as db:
                    worker = CanvasSyncService(db, self.client)
                    worker.runtime = self.runtime
                    course = await db.get(Course, course_id)
                    if course is None:
                        return
                    in_flight.append(course.name)
                    self._progress(done, total, in_flight)
                    try:
                        await worker.sync_one_course(course)
                        self.report.merge(worker.report)
                    finally:
                        # 收尾必须在 finally 里：这门课炸了也算跑完了一门，
                        # 不摘名字的话进度条会一直挂着它，不补这次上报的话
                        # 界面停在"还在同步"，而它其实已经结束了
                        in_flight.remove(course.name)
                        done += 1
                        self._progress(done, total, in_flight)

        await asyncio.gather(*(run_one(cid) for cid in course_ids))

        await self._sweep_disabled_data()
        await self._sweep_disabled_link_data()

        # 分组整理放在最后串行跑：它是几百条 insert 的长事务，
        # 和同步并发抢写锁只会互相拖慢
        for course_id in course_ids:
            try:
                await organize_course_files(self.db, course_id)
            except Exception as exc:  # noqa: BLE001
                await self.db.rollback()
                logger.warning("课程 %s 自动整理失败: %s", course_id, exc)

        try:
            await self.sync_messages()
        except CanvasError as exc:
            self.report.errors.append(f"收件箱同步失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            self.report.errors.append(f"收件箱同步异常: {type(exc).__name__}: {exc}"[:300])

        await self.sync_planner()

        await self.db.commit()
        self.report.finished_at = datetime.now(UTC)
        return self.report

    async def sync_planner(self, course: Course | None = None) -> None:
        """日历与待办。放在课程内容之后：它只需要课程目录在，不依赖文件和条目，
        失败也不该拖累前面已经同步好的东西。"""
        try:
            result = await planner.sync_planner(self.db, self.client, course)
            await self.db.commit()
            self.report.planner_items += result.items
            self.report.planner_events += result.events
            self.report.errors.extend(result.errors)
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            self.report.errors.append(f"日历与待办同步异常: {type(exc).__name__}: {exc}"[:300])

    async def sync_one_course(self, course: Course) -> None:
        """同步一门课。失败只影响这一门，并给它打上"下轮全量重来"的标记。"""
        # rollback 会让 ORM 对象过期，之后再读 course.id 会触发一次懒加载查询，
        # 在 except 块里这会抛 MissingGreenlet 把真正的错误盖掉 —— 先取出来
        course_id = course.id
        name = course.name
        try:
            await self.sync_course_content(course)
            await self.sync_course_files(course)
            course.last_synced_at = datetime.now(UTC)
            course.last_sync_failed = False
            course.last_sync_error = None
            await self.db.commit()
            self.report.courses_synced += 1
        except CanvasError as exc:
            await self.db.rollback()
            msg = f"课程 {name} 同步失败: {exc}"
            await self._mark_failed(course_id, str(exc))
            logger.warning(msg)
            self.report.errors.append(msg[:300])
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            msg = f"课程 {name} 同步异常: {type(exc).__name__}: {exc}"
            await self._mark_failed(course_id, f"{type(exc).__name__}: {exc}")
            logger.exception(msg)
            self.report.errors.append(msg[:300])

    @property
    def _runtime(self) -> runtime_settings.RuntimeSettings:
        """单独同步某门课时 sync_all 没跑过，这里兜底成 .env 的默认值。"""
        if self.runtime is None:
            self.runtime = runtime_settings.RuntimeSettings(
                auto_download_files=settings.auto_download_files,
                sync_inline_images=settings.sync_inline_images,
            )
        return self.runtime

    def _progress(self, done: int, total: int, course_names: list[str]) -> None:
        # 传副本：调用方把 in_flight 一直改来改去，直接传引用的话
        # SyncManager 存下来的那份会跟着变，读到的永远是"当前"而不是"当时"
        if self._on_progress is not None:
            self._on_progress(done, total, list(course_names))

    # ---------- 课程 ----------

    async def sync_courses(self) -> list[Course]:
        """维护课程目录，返回本轮真正要拉内容的那些课。

        **两件事，别混起来**：目录是"Canvas 上有哪些课"，`sync_enabled` 是
        "用户要同步哪些课"。所以在读和已结课**两批都要拉** —— 哪怕一门都没选，
        「选择同步课程」那个界面也得能把它们列出来给用户勾。代价是每轮多一个
        列表请求（不是每门课一个），相对几十秒的同步可以忽略。
        """
        out: list[Course] = []
        for enrollment_state, is_concluded in (("active", False), ("completed", True)):
            try:
                raw_courses = await self.client.list_courses(enrollment_state)
            except CanvasError as exc:
                self.report.errors.append(f"拉取 {enrollment_state} 课程失败: {exc}")
                continue

            for raw in raw_courses:
                # 学校对过期课程会加访问限制，这类返回里连 name 都没有
                if raw.get("access_restricted_by_date"):
                    continue
                data = mapper.map_course(raw)
                data["is_concluded"] = is_concluded
                existing = (
                    await self.db.execute(
                        select(Course).where(Course.canvas_id == data["canvas_id"])
                    )
                ).scalar_one_or_none()

                if existing is None:
                    # 新发现的课：在读默认同步，已结课默认不同步。
                    # 一门课后来结课**不会**改这个值 —— 结课只是加个标签，
                    # 资料往往还要用很久，替用户把它关掉是自作主张。
                    existing = Course(**data, sync_enabled=not is_concluded)
                    self.db.add(existing)
                else:
                    for key, value in data.items():
                        # is_favorite 是用户在本平台上的偏好，不让 Canvas 覆盖
                        if key != "is_favorite":
                            setattr(existing, key, value)
                out.append(existing)

        await self.db.flush()
        return [c for c in out if not c.is_hidden and c.sync_enabled]

    # ---------- 作业 / 公告 / 页面 ----------

    async def sync_course_content(self, course: Course) -> None:
        self._checked_page_ids = set()
        await self._sync_items(
            course,
            ItemKind.ASSIGNMENT,
            lambda: self.client.list_assignments(course.canvas_id),
            lambda raw: mapper.map_assignment(raw, course.id),
        )
        await self._sync_items(
            course,
            ItemKind.ANNOUNCEMENT,
            lambda: self._list_announcements_incrementally(course),
            lambda raw: mapper.map_announcement(raw, course.id),
        )
        await self._sync_items(
            course,
            ItemKind.PAGE,
            lambda: self._list_pages_with_body(course),
            lambda raw: mapper.map_page(raw, course.id),
        )
        await self._mark_pages_checked(course)

    async def _mark_failed(self, course_id: int, reason: str = "") -> None:
        """标记这门课需要全量重来。rollback 之后单独写，避免被一起回滚掉。"""
        course = await self.db.get(Course, course_id)
        if course is not None:
            course.last_sync_failed = True
            course.last_sync_error = reason[:500] or None
            course.last_synced_at = datetime.now(UTC)
            await self.db.commit()

    def _recently_unavailable(self, state: SyncState) -> bool:
        """这个端点上次是 403，且还没到重试时间 —— 别再白发一次请求。

        学校关掉 Files / Pages 标签页是长期设置，不是临时故障。每门课每轮
        为此浪费两个必然失败的请求，一轮下来就是十几秒。
        """
        if state.status != "unavailable" or state.last_synced_at is None:
            return False
        last = state.last_synced_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return last > datetime.now(UTC) - timedelta(hours=settings.unavailable_retry_hours)

    async def _list_announcements_incrementally(self, course: Course) -> list[dict]:
        """公告按时间倒序返回，翻到"整页都是库里已有且没变的"就停。

        一门 638 条公告的课全量要 7 个请求，日常增量只要 1 个。
        """
        known = {
            canvas_id: content_hash
            for canvas_id, content_hash in (
                await self.db.execute(
                    select(CanvasItem.canvas_id, CanvasItem.content_hash).where(
                        CanvasItem.course_id == course.id,
                        CanvasItem.kind == ItemKind.ANNOUNCEMENT,
                    )
                )
            ).all()
        }
        # 上轮失败过 → 全量翻页，否则那次没拉完的旧公告会被永远跳过
        if not known or course.last_sync_failed:
            return await self.client.list_announcements(course.canvas_id)

        def page_is_all_known(page_items: list[dict]) -> bool:
            for raw in page_items:
                data = mapper.map_announcement(raw, course.id)
                if known.get(data["canvas_id"]) != data["content_hash"]:
                    return False
            return True

        return await self.client.list_announcements(
            course.canvas_id, stop_after_page=page_is_all_known
        )

    async def _list_pages_with_body(self, course: Course) -> list[dict]:
        """页面列表接口不返回正文，得逐个补。

        不补的话页面里贴的文件全都扫不到 —— 而正文正是很多教授放讲义的地方。

        但每轮都重抓所有页面太贵：页面多的课一门就要几百个请求，整轮同步要十几分钟。
        所以先比对 `updated_at`：页面没动过就直接复用库里存着的正文。
        """
        pages = await self.client.list_pages(course.canvas_id)
        # 列表里出现过就是精确核对过了（它带 updated_at），无论要不要重抓 body。
        # 不记的话这些页面的 content_fetched_at 一直是空，而空值在复查评分里
        # 等于"从没查过"（最高优先级）—— 结果每轮都白抓一遍。
        self._checked_page_ids = {
            str(p.get("page_id") or p.get("url"))
            for p in pages
            if p.get("page_id") or p.get("url")
        }

        cached = {
            item.canvas_id: item
            for item in (
                await self.db.execute(
                    select(CanvasItem).where(
                        CanvasItem.course_id == course.id, CanvasItem.kind == ItemKind.PAGE
                    )
                )
            )
            .scalars()
            .all()
        }

        stale: list[dict] = []
        reused = 0
        for page in pages:
            key = str(page.get("page_id") or page.get("url"))
            old = cached.get(key)
            unchanged = (
                old is not None
                and old.raw is not None
                and old.raw.get("body") is not None
                and mapper.same_moment(
                    old.canvas_updated_at, mapper.parse_dt(page.get("updated_at"))
                )
            )
            # 这条路径能拿到 Canvas 给的 updated_at，是精确比对，
            # 不需要 content_fetched_at 那套按时间猜的逻辑
            if unchanged:
                page["body"] = old.raw["body"]
                reused += 1
            else:
                stale.append(page)

        budget = settings.max_page_fetch
        if len(stale) > budget:
            self.report.warnings.append(
                f"{course.name}: 有 {len(stale)} 个页面需要抓取，超过上限 {budget}，"
                f"未抓取的页面里如果有文件会漏掉（调大 MAX_PAGE_FETCH 可解决）"
            )

        sem = asyncio.Semaphore(_PAGE_FETCH_CONCURRENCY)

        async def fill(page: dict) -> None:
            url = page.get("url")
            if not url:
                return
            async with sem:
                try:
                    full = await self.client.get_page_body(course.canvas_id, url)
                    page["body"] = full.get("body")
                except CanvasError:
                    pass  # 单个页面拿不到不影响其余

        await asyncio.gather(*(fill(p) for p in stale[:budget]))
        if reused:
            logger.info(
                "course %s: %s 个页面正文复用缓存，%s 个需要抓取",
                course.canvas_id,
                reused,
                len(stale),
            )
        return pages

    async def _sync_items(self, course: Course, kind: ItemKind, fetch, map_fn) -> None:
        resource_key = f"course:{course.canvas_id}:{kind}"
        state = await self._get_sync_state(resource_key)
        cold_start = state.last_success_at is None

        if not course.last_sync_failed and self._recently_unavailable(state):
            self.report.skipped_requests += 1
            return

        # 发网络请求之前清空未提交的写，别拿着 SQLite 的写锁去等 Canvas
        await self.db.commit()

        try:
            raws = await fetch()
        except CanvasNotAvailable as exc:
            # 学校关掉了某个标签页（很常见）—— 记录但不当作错误
            logger.info("%s 不可用: %s", resource_key, exc)
            state.status = "unavailable"
            state.last_synced_at = datetime.now(UTC)
            return

        changed = 0
        for index, raw in enumerate(raws):
            data = map_fn(raw)
            if await self._upsert_item(data, kind, course, cold_start):
                changed += 1
            if (index + 1) % _COMMIT_BATCH == 0:
                await self.db.commit()

        state.last_synced_at = datetime.now(UTC)
        state.last_success_at = datetime.now(UTC)
        state.status = "ok"
        state.items_seen = len(raws)
        state.items_changed = changed
        await self.db.flush()

    async def _upsert_item(
        self, data: dict, kind: ItemKind, course: Course | None, cold_start: bool
    ) -> bool:
        existing = (
            await self.db.execute(
                select(CanvasItem).where(
                    CanvasItem.kind == kind, CanvasItem.canvas_id == data["canvas_id"]
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(UTC)

        if existing is None:
            item = CanvasItem(**data, first_seen_at=now)
            self.db.add(item)
            await self.db.flush()
            self.report.items_created += 1
            if not cold_start:
                await self._emit_event(
                    kind=_NEW_EVENT_KIND[kind],
                    course_id=course.id if course else None,
                    item=item,
                    title=item.title,
                    summary=self._summarize_new(kind, item, course),
                    # 界面按 kind + payload 现拼摘要。上面那句 summary 只是
                    # 给老客户端和排查用的兜底，切语言时不会被显示
                    payload=self._new_payload(kind, item),
                )
            return True

        if existing.content_hash == data["content_hash"]:
            return False

        changed_fields = self._diff(existing, data)
        for key, value in data.items():
            setattr(existing, key, value)
        await self.db.flush()
        self.report.items_updated += 1

        if not cold_start:
            await self._emit_event(
                kind=_UPDATED_EVENT_KIND[kind],
                course_id=course.id if course else None,
                item=existing,
                title=existing.title,
                summary=(
                    f"{course.name if course else 'Canvas'} · "
                    f"有更新：{', '.join(changed_fields) or '内容'}"
                ),
                payload={"changed_fields": changed_fields},
            )
        return True

    @staticmethod
    def _diff(existing: CanvasItem, data: dict) -> list[str]:
        """变了哪几项。返回**字段名**，不是"标题 / 截止时间"这种说法 ——
        这份列表会存进事件的 payload，而界面有两种语言。"""
        watched = ("title", "due_at", "body_html")
        return [key for key in watched if getattr(existing, key) != data.get(key)]

    @staticmethod
    def _new_payload(kind: ItemKind, item: CanvasItem) -> dict | None:
        """新条目的摘要要用到的**数据**（截止时间、发件人），不是拼好的话。"""
        if kind == ItemKind.ASSIGNMENT and item.due_at is not None:
            return {"due_at": item.due_at.isoformat()}
        if kind == ItemKind.MESSAGE and item.author_name:
            return {"author": item.author_name}
        return None

    @staticmethod
    def _summarize_new(kind: ItemKind, item: CanvasItem, course: Course | None) -> str:
        prefix = course.name if course else "Canvas"
        if kind == ItemKind.ASSIGNMENT:
            due = item.due_at.strftime("%m-%d %H:%M") if item.due_at else "无截止时间"
            return f"{prefix} · 新作业，截止 {due}"
        if kind == ItemKind.ANNOUNCEMENT:
            return f"{prefix} · 新公告"
        if kind == ItemKind.MESSAGE:
            return f"来自 {item.author_name or '教授'} 的新消息"
        return f"{prefix} · 页面更新"

    # ---------- 收件箱（教授消息） ----------

    async def sync_messages(self) -> None:
        state = await self._get_sync_state("inbox:conversations")
        cold_start = state.last_success_at is None

        try:
            raws = await self.client.list_conversations()
        except CanvasNotAvailable as exc:
            logger.info("收件箱不可用: %s", exc)
            return

        # context_code → 本地 course，用于把消息挂到课程下
        courses = (await self.db.execute(select(Course))).scalars().all()
        by_canvas_id = {c.canvas_id: c for c in courses}

        for raw in raws:
            data = mapper.map_conversation(raw)
            canvas_course_id = mapper.conversation_course_canvas_id(raw)
            course = by_canvas_id.get(canvas_course_id) if canvas_course_id else None
            data["course_id"] = course.id if course else None
            await self._upsert_item(data, ItemKind.MESSAGE, course, cold_start)

        state.last_synced_at = datetime.now(UTC)
        state.last_success_at = datetime.now(UTC)
        state.status = "ok"
        state.items_seen = len(raws)
        await self.db.flush()

    # ---------- 文件 ----------

    async def sync_course_files(self, course: Course) -> None:
        """四路扫描一门课的文件。必须在 sync_course_content 之后跑 ——
        富文本扫描直接读库里 CanvasItem 的 raw，不再重复请求 Canvas。"""
        resource_key = f"course:{course.canvas_id}:files"
        state = await self._get_sync_state(resource_key)
        cold_start = state.last_success_at is None

        items = (
            (
                await self.db.execute(
                    select(CanvasItem).where(
                        CanvasItem.course_id == course.id,
                        CanvasItem.kind.in_(
                            [ItemKind.ANNOUNCEMENT, ItemKind.PAGE, ItemKind.ASSIGNMENT]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )

        # 库里已有的文件不再重新拉元数据 —— 这是后续同步能快起来的关键。
        # 但超期太久的要强制核对一次，否则文件名/大小在 Canvas 上改了永远发现不了。
        known_ids = set(
            (
                await self.db.execute(
                    select(CourseFile.canvas_id).where(CourseFile.course_id == course.id)
                )
            )
            .scalars()
            .all()
        )
        known_ids -= await self._files_due_for_recheck(course)

        # 上轮失败过 → 所有增量缓存作废，这门课完整重扫一遍
        full = course.last_sync_failed
        # 上轮失败过 → 全部重抓；否则复用缓存，只刷最旧的一小批
        if full:
            page_cache, stale_pages = {}, set()
        else:
            page_cache, stale_pages = await self._page_refresh_plan(course)
        skip = set() if full else await self._skippable_endpoints(course)

        # 同上：collect 里是一连串网络请求，进去之前必须先放掉写锁
        await self.db.commit()

        include_inline = self._runtime.sync_inline_images
        collector = FileCollector(
            self.client,
            course.canvas_id,
            course.name,
            include_inline_images=include_inline,
            include_links=self._runtime.sync_links,
            include_announcement_links=self._runtime.sync_announcement_links,
            include_discussions=self._runtime.sync_discussions,
        )
        try:
            collected = await collector.collect(
                list(items), known_ids, page_cache, skip, stale_pages
            )
        except CanvasError as exc:
            logger.warning("课程 %s 文件收集失败: %s", course.name, exc)
            state.status = "error"
            state.error_message = str(exc)[:500]
            state.last_synced_at = datetime.now(UTC)
            await self.db.flush()
            return

        # 分批提交：一门大课有近千个文件，全塞进一个事务会让写锁持有几十秒，
        # 并发同步的其他课程全部撞 "database is locked"
        for index, entry in enumerate(collected.values()):
            inline = entry.canvas_file_id in collector.inline_image_ids
            if entry.raw is None:
                # 复用库里已有的元数据，只刷新来源
                await self._refresh_sources_only(entry, course, inline)
            else:
                folder_id = entry.raw.get("folder_id")
                path = await collector.resolve_folder_path(folder_id)
                data = mapper.map_file(entry.raw, course.id, path)
                await self._upsert_file(data, entry.sources, course, cold_start, inline)
            self.report.files_seen += 1
            if (index + 1) % _COMMIT_BATCH == 0:
                await self.db.commit()

        if not include_inline:
            await self._purge_inline_images(course, collector.inline_image_ids)
        self.report.inline_images_skipped += len(collector.inline_image_ids)

        # 外部链接和文件走同一张表、同一套来源，只是 medium 不同。
        # 放在文件之后：换链接的判定要读上一轮的来源，而来源在上面刚被重建过。
        #
        # 链接有**自己的**冷启动水位线。用户在设置里打开「同步链接」之后，
        # 库里会一次性冒出几百个链接 —— 那不是"新消息"，是他刚点的那个开关的
        # 直接结果，全发一遍提醒只会把更新列表淹掉。改开关时水位线被清掉，
        # 于是打开后的第一轮只入库、不通知，和课程首次同步是同一条规则。
        link_state = await self._get_sync_state(f"course:{course.canvas_id}:links")
        link_cold = cold_start or link_state.last_success_at is None
        await self._sync_course_links(course, collector, link_cold)
        link_state.last_synced_at = datetime.now(UTC)
        link_state.last_success_at = datetime.now(UTC)
        link_state.status = "ok"
        link_state.items_seen = len(collector.links)

        # 文件那边定下来之后，把用户的整理结果对齐一遍：改名了跟着改，
        # 文件行换了 id 就重新接上。用户数据本身一行不动。
        relinked, orphaned = await userdata.relink_after_sync(self.db, course)
        self.report.user_files_relinked += relinked
        self.report.user_files_orphaned += orphaned

        # 这门课的"看过"水位线：没有就以现在为初值。放在这里而不是建课的时候 ——
        # 建课只是把 Canvas 上的课列进目录，那时一个文件都还没收，
        # 而水位线的含义是"这一刻之前的东西我都见过了"
        await userdata.ensure_view_state(self.db, course.id)

        # 新抓到的页面存成 CanvasItem：下轮能复用正文，页面内容变了也能发提醒
        await self._store_fetched_pages(course, collector.fetched_pages)
        await self._record_endpoint_availability(course, collector.unavailable_endpoints, skip)
        self.report.skipped_requests += len(skip)

        for note in collector.truncated:
            self.report.warnings.append(f"{course.name}: {note}")

        # 覆盖面：这门课有哪些板块、我们没扫哪些。**漏了就要说出来** ——
        # 静悄悄地漏是这套收集器最容易犯也最难发现的错（某门课整门资料都在
        # 大纲里，界面上就是 0 个资源，而没有任何地方提示过）
        await self._record_coverage(course, collector)

        state.last_synced_at = datetime.now(UTC)
        state.last_success_at = datetime.now(UTC)
        state.status = "ok"
        state.items_seen = len(collected)
        # 把"哪个来源贡献了多少文件"记下来，设置页面能直接看到 Files 被禁也不影响结果
        state.error_message = ", ".join(f"{k}={v}" for k, v in collector.stats.items())
        await self.db.flush()
        await self.db.commit()

    async def _record_coverage(self, course: Course, collector: FileCollector) -> None:
        """把"这门课有哪些板块、每个扫下来什么结果"整份存进 payload。

        设置页直接渲染这份东西 —— 用户看到的和我们扫的是同一张表，
        不再是"正常 / 部分覆盖 / 学校未开放"混在一起的一串状态。

        做不到覆盖每一种板块可以接受，**让用户以为自己看到的是全部**不行。
        """
        # 每个板块实际收到多少资源，直接从来源表数（按资源去重）
        counts = {
            boards.board_of(kind): n
            for kind, n in (
                await self.db.execute(
                    select(FileSource.source_kind, func.count(func.distinct(FileSource.file_id)))
                    .where(FileSource.course_id == course.id)
                    .group_by(FileSource.source_kind)
                )
            ).all()
        }

        report = []
        for board in boards.BOARDS:
            status = collector.board_status.get(board.id)
            if status is None and board.id not in counts:
                continue  # 这门课没有这个板块，不占一行
            report.append(
                {
                    "id": board.id,
                    "label": board.label,
                    "status": status or boards.SCANNED,
                    "count": counts.get(board.id, 0),
                }
            )

        state = await self._get_sync_state(f"course:{course.canvas_id}:coverage")
        state.last_synced_at = datetime.now(UTC)
        state.last_success_at = datetime.now(UTC)
        state.items_seen = len(collector.tabs)
        state.status = "ok"
        state.error_message = None
        state.payload = {"boards": report, "unsupported": collector.uncovered_tabs}
        if collector.uncovered_tabs:
            self.report.warnings.append(
                f"{course.name}: 这门课的「{'、'.join(collector.uncovered_tabs)}」还没有被扫描"
            )
        await self.db.flush()

    async def _skippable_endpoints(self, course: Course) -> set[str]:
        """上次确认被禁用、且还没到重试时间的端点。"""
        skip: set[str] = set()
        for name in ("files", "modules"):
            state = await self._get_sync_state(f"course:{course.canvas_id}:endpoint:{name}")
            if self._recently_unavailable(state):
                skip.add(name)
        return skip

    async def _record_endpoint_availability(
        self, course: Course, unavailable: set[str], skipped: set[str]
    ) -> None:
        for name in ("files", "modules"):
            if name in skipped:
                continue  # 这轮压根没试，别覆盖上次的判断
            state = await self._get_sync_state(f"course:{course.canvas_id}:endpoint:{name}")
            state.status = "unavailable" if name in unavailable else "ok"
            state.last_synced_at = datetime.now(UTC)
            if name not in unavailable:
                state.last_success_at = datetime.now(UTC)
        await self.db.flush()

    @staticmethod
    def _refresh_score(item: CanvasItem, now: datetime) -> float:
        """这个页面有多该被重新检查了。>= 1 表示已经超过它自己的期望间隔。

        按「页面自身的活跃度」分配刷新配额，而不是雨露均沾地轮询最旧的。
        实测一个真实账号：419 个页面里 371 个（88.5%）超过半年没改过，
        而 7 天内改过的只有 5 个。均分配额等于把绝大部分请求浪费在
        永远不会变的页面上 —— 恰恰是最近动过的页面最可能再动。

        做法是让每个页面有自己的期望复查间隔：在 Canvas 上静止越久的，间隔越长。
        """
        fetched = _as_utc(item.content_fetched_at)
        if fetched is None:
            return float("inf")  # 从没查过，最优先
        since_check = (now - fetched).total_seconds()

        last_change = _as_utc(item.canvas_updated_at) or _as_utc(item.first_seen_at)
        ceiling = settings.max_recheck_days * 86400.0
        idle = max((now - last_change).total_seconds(), 0.0) if last_change else ceiling

        floor = settings.page_cache_hours * 3600.0
        expected = min(max(idle * 0.5, floor), ceiling)
        return since_check / expected

    async def _page_refresh_plan(self, course: Course) -> tuple[dict[str, dict], set[str]]:
        """返回 (可复用的页面数据, 本轮要重新抓的 url)。

        缓存里存的是**整个 raw**，不只是正文。只存正文的话，命中缓存的页面就丢了
        `updated_at` —— 文件看不出来（它会回落到文件自己的更新时间），但链接没有
        这个回落，结果是 494 个链接里 451 个在界面上没有日期。

        module 里挂的页面动辄几百个，每轮全抓一遍要几分钟。Canvas 的 module 条目
        又不带页面的 updated_at，没法免费判断变没变 —— 只能自己决定什么时候复查。

        选谁复查有两级优先：
        1. **超期的排在最前**（硬性兜底）。光按活跃度排序不够：活跃页面的分数
           能到 8，而超期 31 天的陈旧页面才 1.03，会被一直挤在后面。要保证
           "库里不存在超过 max_recheck_days 没核对的页面"，超期项必须插队。
        2. 其余按活跃度分数，最近在 Canvas 上动过的优先。

        每轮总量受 `page_refresh_batch` 约束，保证每轮开销恒定。
        """
        now = datetime.now(UTC)
        deadline = now - timedelta(days=settings.max_recheck_days)
        items = (
            (
                await self.db.execute(
                    select(CanvasItem).where(
                        CanvasItem.course_id == course.id, CanvasItem.kind == ItemKind.PAGE
                    )
                )
            )
            .scalars()
            .all()
        )

        cache: dict[str, dict] = {}
        overdue: list[tuple[datetime, str]] = []
        due: list[tuple[float, str]] = []
        for item in items:
            raw = item.raw or {}
            url, body = raw.get("url"), raw.get("body")
            if not url or body is None:
                continue
            cache[url] = raw

            fetched = _as_utc(item.content_fetched_at)
            if fetched is None or fetched < deadline:
                overdue.append((fetched or datetime.min.replace(tzinfo=UTC), url))
            elif self._refresh_score(item, now) >= 1.0:
                due.append((self._refresh_score(item, now), url))

        overdue.sort()  # 超期最久的先
        due.sort(key=lambda pair: -pair[0])  # 再按活跃度

        budget = settings.page_refresh_batch
        picked = [url for _, url in overdue[:budget]]
        picked += [url for _, url in due[: budget - len(picked)]]
        return cache, set(picked)

    async def _store_fetched_pages(self, course: Course, pages: dict[str, dict]) -> None:
        if not pages:
            return
        state = await self._get_sync_state(f"course:{course.canvas_id}:{ItemKind.PAGE}")
        cold_start = state.last_success_at is None
        now = datetime.now(UTC)
        for raw in pages.values():
            data = mapper.map_page(raw, course.id)
            await self._upsert_item(data, ItemKind.PAGE, course, cold_start)
            # 关键：即使内容没变（_upsert_item 什么都不改），也要把抓取时刻记上。
            # 不记的话缓存过期后永远续不上，之后每轮都要重抓这个页面。
            item = (
                await self.db.execute(
                    select(CanvasItem).where(
                        CanvasItem.kind == ItemKind.PAGE,
                        CanvasItem.canvas_id == data["canvas_id"],
                    )
                )
            ).scalar_one_or_none()
            if item is not None:
                item.content_fetched_at = now
        state.last_success_at = datetime.now(UTC)
        state.last_synced_at = datetime.now(UTC)
        await self.db.flush()

    async def _sweep_disabled_link_data(self) -> None:
        """开关关着，它对应的数据就不该留在库里 —— 每轮兜底扫一遍。

        设置页那次删只覆盖"用户亲手改开关"。直接改库、升级带来的默认值变化
        都不经过它，而且没勾选同步的课根本不会再跑到，清理永远轮不到它们。
        和内联图片走的是同一条路子。
        """
        runtime = self._runtime
        if not runtime.sync_links:
            n = await purge.purge_links(self.db)
            if n:
                logger.info("兜底清理：删掉 %s 个外部链接（同步链接已关闭）", n)
                self.report.files_removed += n
        elif not runtime.sync_announcement_links:
            n = await purge.purge_announcement_links(self.db)
            if n:
                logger.info("兜底清理：删掉 %s 个只出现在公告里的链接", n)
                self.report.files_removed += n
        await self.db.commit()

    async def _sweep_disabled_data(self) -> None:
        """全库兜底：没勾选的课、关着的开关，对应的数据一律清掉。

        界面上改的时候已经删过一次，但那只覆盖"用户亲手点"这一条路径。
        直接改库、升级带来的默认值变化都绕过它。更要命的是每门课的清理只跟着
        那门课的同步走，而**没勾选的课根本不会再被同步** —— 清理永远轮不到
        它们，边栏里那些课就会一直挂着，和用户的选择对不上。
        """
        rt = self._runtime
        removed = 0
        courses, files = await purge.purge_disabled_courses(self.db)
        if courses:
            logger.info("兜底清理：清空 %s 门未勾选课程（%s 个文件）", courses, files)
            removed += files
        if not rt.sync_inline_images:
            n = await purge.purge_inline_images(self.db)
            if n:
                logger.info("兜底清理：删除 %s 个内联图片", n)
                removed += n
        if not rt.auto_download_files:
            n = await purge.purge_local_downloads(self.db)
            if n:
                logger.info("兜底清理：删除 %s 个本地文件副本", n)
        # 和三个开关无关，但同属"该跟着课程一起消失却没消失"的东西：
        # 留着会让重新加回来的课失去冷启动静默，一轮涌出几百条假提醒
        orphans = await purge.purge_orphan_sync_states(self.db)
        if orphans:
            logger.info("兜底清理：删除 %s 条孤儿同步状态", orphans)
        self.report.files_removed += removed
        await self.db.commit()

    async def _purge_inline_images(self, course: Course, inline_ids: set[int]) -> None:
        """用户关掉了"同步内联图片" —— 把库里已经收进来的那些删掉。

        收集器只是不再产出它们，光这样旧记录会永远留在库里（同步不做"消失即删除"，
        Canvas 上临时取不到的文件不该被误删）。所以这里显式删一次：每轮都会重扫
        正文，判定为内联图的 id 每轮都能重新算出来，删除是幂等的。

        两个来源合起来删：本轮重新判定出来的 id，加上库里早先就标记过的
        `is_inline_image`。只靠前者的话，banner 所在的页面被删掉之后那张图
        就再也不会被判定到，会永远留在库里。
        """
        match = CourseFile.is_inline_image.is_(True)
        if inline_ids:
            match = match | CourseFile.canvas_id.in_(inline_ids)
        rows = (
            (
                await self.db.execute(
                    select(CourseFile).where(CourseFile.course_id == course.id, match)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        # 用户星标过或加进分组的文件不能真删 —— 那是他自己整理的东西。
        # 改成打"已从 Canvas 删除"的标记留着，界面上照常显示、带个标。
        kept = await userdata.referenced_resource_keys(self.db, course.id)
        removed = 0
        for file in rows:
            if file.resource_key in kept:
                file.is_deleted_on_canvas = True
                continue
            # 之前开着自动下载的话，本地副本也要一并清掉
            if file.local_path:
                storage.remove(file.local_path)
            await self.db.delete(file)
            removed += 1
        logger.info(
            "course %s: 删除 %s 个内联图片（%s 个因用户整理过而保留）",
            course.name,
            removed,
            len(rows) - removed,
        )
        self.report.files_removed += removed
        await self.db.commit()

    async def _upsert_file(
        self,
        data: dict,
        sources: list,
        course: Course,
        cold_start: bool,
        is_inline_image: bool = False,
    ) -> None:
        existing = (
            await self.db.execute(
                select(CourseFile).where(
                    CourseFile.course_id == data["course_id"],
                    CourseFile.resource_key == data["resource_key"],
                )
            )
        ).scalar_one_or_none()

        now = datetime.now(UTC)
        is_new = existing is None
        changed = False

        if is_new:
            existing = CourseFile(
                **data,
                first_seen_at=now,
                metadata_fetched_at=now,
                is_inline_image=is_inline_image,
            )
            self.db.add(existing)
            await self.db.flush()
        else:
            if not mapper.same_moment(existing.canvas_updated_at, data.get("canvas_updated_at")):
                changed = True
            for key, value in data.items():
                setattr(existing, key, value)
            existing.is_deleted_on_canvas = False
            existing.is_inline_image = is_inline_image
            # 走到这里说明这轮真的从 Canvas 拉了元数据
            existing.metadata_fetched_at = now
            await self.db.flush()

        await self._sync_file_sources(existing, sources, course)

        if self._runtime.auto_download_files and (is_new or changed or not existing.local_path):
            await self._download(existing, course)

        if not cold_start and (is_new or changed):
            await self._emit_event(
                kind=EventKind.NEW_FILE if is_new else EventKind.UPDATED_FILE,
                course_id=course.id,
                file_id=existing.id,
                title=existing.display_name,
                summary=f"{course.name} · {'新文件' if is_new else '文件已更新'}",
            )

    # ---------- 外部链接 ----------

    @staticmethod
    def _unambiguous(pairs: list[tuple[tuple[str, str], str]]) -> dict[tuple[str, str], str]:
        """(条目, 锚文本) → 身份，**丢掉一个条目里重复出现的锚文本**。

        这一条是从真实数据里学来的：一个页面上出现两次「Click here」、两次
        「here」、两次「academic unit」指向不同地址，是常态而不是例外。锚文本
        重复时根本分不清"谁换成了谁"，硬猜的结果就是每轮同步都报一条假的
        "链接已更新" —— 那正是这个功能最该避免的噪声。分不清就不猜，
        退回成普通的"多了一条 / 少了一条"。
        """
        counts: dict[tuple[str, str], int] = {}
        for key, _ in pairs:
            counts[key] = counts.get(key, 0) + 1
        return {key: value for key, value in pairs if counts[key] == 1}

    async def _link_anchor_map(self, course: Course) -> dict[tuple[str, str], str]:
        """上一轮的 (条目, 锚文本) → 链接身份。

        判断"链接被换掉了"就靠它：URL 本身就是身份，换一个 URL 技术上等于
        "删一条 + 加一条"，只有**在同一个条目里按锚文本对齐**，才能把它认成
        "「Lecture 3 录播」这条链接换了地址"，而不是让用户看到"少了一个、多了一个"。
        """
        rows = (
            await self.db.execute(
                select(FileSource.source_key, FileSource.item_title, CourseFile.resource_key)
                .join(CourseFile, CourseFile.id == FileSource.file_id)
                .where(
                    CourseFile.course_id == course.id,
                    CourseFile.medium == Medium.LINK,
                )
            )
        ).all()
        return self._unambiguous(
            [
                ((split_item_ref(source_key), title), resource_key)
                for source_key, title, resource_key in rows
                if title
            ]
        )

    async def _detect_replaced_links(
        self, course: Course, collected: dict[str, CollectedLink], cold_start: bool
    ) -> set[str]:
        """找出被换掉的链接，发提醒，并返回这些链接的新身份。

        返回值交给 `_upsert_link` 压掉"新链接"提醒 —— 同一件事只该说一遍：
        用户要看到的是"这个链接更新了"，不是"少了一个、多了一个"。
        """
        before = await self._link_anchor_map(course)
        if not before:
            return set()

        # 新的这一侧同样要去掉重复锚文本，否则"两个 Click here"里随便挑一个
        # 去和旧的比，一样会比出假的更新
        now = self._unambiguous(
            [
                ((split_item_ref(src.source_key), src.item_title or ""), link.resource_key)
                for link in collected.values()
                for src in link.sources
                if src.item_title
            ]
        )

        replaced: set[str] = set()
        for anchor, resource_key in now.items():
            was = before.get(anchor)
            if was is None or was == resource_key:
                continue
            replaced.add(resource_key)
            if not cold_start:
                link = collected[resource_key]
                await self._emit_event(
                    kind=EventKind.UPDATED_LINK,
                    course_id=course.id,
                    title=link.title,
                    summary=f"{course.name} · 链接已更新",
                    payload={"url": link.url, "anchor": anchor[1]},
                    # 锚点是"这门课这个条目里的这段锚文本"，版本是它现在指向谁 ——
                    # 同一次替换不会重复报，之后再换一次仍然报得出来
                    dedup=(f"link:{course.id}:{anchor[0]}:{anchor[1]}", resource_key),
                )
        return replaced

    async def _upsert_link(
        self,
        link: CollectedLink,
        course: Course,
        cold_start: bool,
        suppress_new_event: bool,
    ) -> None:
        existing = (
            await self.db.execute(
                select(CourseFile).where(
                    CourseFile.course_id == course.id,
                    CourseFile.resource_key == link.resource_key,
                )
            )
        ).scalar_one_or_none()

        is_new = existing is None
        if is_new:
            existing = CourseFile(
                course_id=course.id,
                resource_key=link.resource_key,
                medium=Medium.LINK,
                # 链接没有 Canvas 文件 id，身份完全落在 resource_key 上
                canvas_id=NO_CANVAS_ID,
                external_url=link.url,
                display_name=link.title[:500],
                first_seen_at=datetime.now(UTC),
            )
            self.db.add(existing)
            await self.db.flush()
        else:
            # URL 就是身份，走到这里说明指向没变；变的只可能是锚文本
            existing.display_name = link.title[:500]
            existing.external_url = link.url
            existing.is_deleted_on_canvas = False

        await self._sync_file_sources(existing, link.sources, course)

        if is_new and not cold_start and not suppress_new_event:
            await self._emit_event(
                kind=EventKind.NEW_LINK,
                course_id=course.id,
                file_id=existing.id,
                title=existing.display_name,
                summary=f"{course.name} · 新链接",
            )

    async def _sync_course_links(
        self, course: Course, collector: FileCollector, cold_start: bool
    ) -> None:
        """把这门课本轮扫到的外部链接落库，并处理换链接和撤链接。"""
        replaced = await self._detect_replaced_links(course, collector.links, cold_start)
        for index, link in enumerate(collector.links.values()):
            await self._upsert_link(
                link, course, cold_start, suppress_new_event=link.resource_key in replaced
            )
            self.report.links_seen += 1
            if (index + 1) % _COMMIT_BATCH == 0:
                await self.db.commit()
        await self.db.commit()
        await self._sweep_vanished_links(course, collector)

    async def _sweep_vanished_links(self, course: Course, collector: FileCollector) -> None:
        """教授把链接从正文里删了 → 库里也该没有。

        **只在这一轮确实扫全了的时候做。** 页面抓取有预算上限，被截断的那轮里
        "没扫到"和"不存在了"分不开，照删会把好好的链接抹掉。

        只看 `truncated`，不看 `skip_endpoints`：被跳过的是 files / modules
        两个端点，而链接是从页面和作业的富文本里扒的，两者没关系。一开始把
        skip_endpoints 也算进来，结果是那些关掉了 Files 标签页的课**永远**
        不会清理链接 —— 改过规范化规则之后旧行全留在库里，同一个视频出现两次。

        用户整理过的链接不删，只打标记 —— 和文件是同一条规矩：用户收藏的东西
        不因为教授改了一行 HTML 就消失。
        """
        if collector.truncated:
            return

        rows = (
            (
                await self.db.execute(
                    select(CourseFile).where(
                        CourseFile.course_id == course.id,
                        CourseFile.medium == Medium.LINK,
                        CourseFile.resource_key.not_in(list(collector.links) or [""]),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return

        kept_keys = await userdata.referenced_resource_keys(self.db, course.id)
        removed = 0
        for row in rows:
            if row.resource_key in kept_keys:
                row.is_deleted_on_canvas = True
                continue
            await self.db.delete(row)
            removed += 1
        if removed:
            logger.info("course %s: 清掉 %s 个已从正文移除的链接", course.name, removed)
            self.report.files_removed += removed
        await self.db.commit()

    async def _mark_pages_checked(self, course: Course) -> None:
        """把"通过列表精确核对过"记到 content_fetched_at 上。

        这条路径能一个请求拿到所有页面的 updated_at，比对下来没变的页面
        同样是核对过的。不记的话它们的 content_fetched_at 一直是空，
        而空值在复查评分里等于"从没查过"（最高优先级）—— 结果每轮都白抓一遍。
        """
        if not self._checked_page_ids:
            return
        await self.db.execute(
            update(CanvasItem)
            .where(
                CanvasItem.course_id == course.id,
                CanvasItem.kind == ItemKind.PAGE,
                CanvasItem.canvas_id.in_(self._checked_page_ids),
            )
            .values(content_fetched_at=datetime.now(UTC))
        )
        await self.db.commit()

    async def _files_due_for_recheck(self, course: Course) -> set[int]:
        """超过 max_recheck_days 没和 Canvas 核对过的文件，本轮强制重拉元数据。

        增量同步默认跳过已知文件，好处是快，代价是文件的元数据会永久陈旧。
        这里给它一个硬性上限：库里不存在超过这个天数没核对的文件。
        """
        deadline = datetime.now(UTC) - timedelta(days=settings.max_recheck_days)
        rows = (
            await self.db.execute(
                select(CourseFile.canvas_id)
                .where(
                    CourseFile.course_id == course.id,
                    CourseFile.is_deleted_on_canvas.is_(False),
                    (CourseFile.metadata_fetched_at.is_(None))
                    | (CourseFile.metadata_fetched_at < deadline),
                )
                .order_by(CourseFile.metadata_fetched_at.asc().nullsfirst())
                .limit(settings.file_recheck_batch)
            )
        ).scalars().all()
        return set(rows)

    async def _refresh_sources_only(
        self, entry, course: Course, is_inline_image: bool = False
    ) -> None:
        """文件已在库里、这轮没重新拉元数据 —— 只把它的来源刷新一遍。"""
        file = (
            await self.db.execute(
                select(CourseFile).where(
                    CourseFile.course_id == course.id,
                    CourseFile.resource_key == links.file_resource_key(entry.canvas_file_id),
                )
            )
        ).scalar_one_or_none()
        if file is not None:
            file.is_inline_image = is_inline_image
            await self._sync_file_sources(file, entry.sources, course)

    async def _sync_file_sources(self, file: CourseFile, sources: list, course: Course) -> None:
        """重建这个文件的来源记录。来源完全由 Canvas 决定，每轮重算即可。"""
        existing = {
            (s.source_kind, s.source_key): s
            for s in (
                await self.db.execute(select(FileSource).where(FileSource.file_id == file.id))
            )
            .scalars()
            .all()
        }
        seen: set[tuple[str, str]] = set()

        # 公告/页面/作业的来源要挂回对应的 CanvasItem
        item_ids: dict[str, int] = {}
        wanted = {s.canvas_item_canvas_id for s in sources if s.canvas_item_canvas_id}
        if wanted:
            rows = (
                await self.db.execute(
                    select(CanvasItem.canvas_id, CanvasItem.id).where(
                        CanvasItem.course_id == course.id, CanvasItem.canvas_id.in_(wanted)
                    )
                )
            ).all()
            item_ids = {canvas_id: pk for canvas_id, pk in rows}

        # 收集器可能对同一位置产出重复项（同一个页面既在 module 里挂着、又在
        # pages 列表里），本轮内先去重，否则第二条会撞唯一键。
        # **有日期的优先**：两条路给的 key 一样，但 module 那条在缓存命中时
        # 可能没有 updated_at —— 先到先得的话日期就丢了。
        best: dict[tuple[str, str], object] = {}
        for src in sources:
            key = (src.source_kind, src.source_key)
            current = best.get(key)
            if current is None or (src.source_date and not current.source_date):
                best[key] = src

        for src in best.values():
            key = (src.source_kind, src.source_key)
            seen.add(key)
            # module 条目没有自己的时间，回落到文件的更新时间才能正确排序
            source_date = src.source_date or file.canvas_updated_at
            row = existing.get(key)
            if row is None:
                self.db.add(
                    FileSource(
                        file_id=file.id,
                        course_id=course.id,
                        source_kind=src.source_kind,
                        source_key=src.source_key,
                        container_name=src.container_name,
                        item_title=src.item_title,
                        label=src.label,
                        html_url=src.html_url,
                        source_date=source_date,
                        sort_hint=src.sort_hint,
                        canvas_item_id=item_ids.get(src.canvas_item_canvas_id or ""),
                    )
                )
            else:
                row.container_name = src.container_name
                row.item_title = src.item_title
                row.label = src.label
                row.html_url = src.html_url
                row.source_date = source_date
                row.sort_hint = src.sort_hint

        for key, row in existing.items():
            if key not in seen:
                await self.db.delete(row)

        await self.db.flush()

        # 来源定下来了，把"界面显示 + 排序"共用的那个日期算出来存上
        rows = (
            (await self.db.execute(select(FileSource).where(FileSource.file_id == file.id)))
            .scalars()
            .all()
        )
        file.display_date = pick_display_date(file, list(rows))
        await self.db.flush()

    async def _download(self, file: CourseFile, course: Course) -> None:
        if not file.download_url:
            return
        dest = storage.file_path(
            course.canvas_id, course.name, file.canvas_id, file.filename or file.display_name
        )
        try:
            await self.client.download_file(
                file.download_url, dest, settings.max_download_mb * 1024 * 1024
            )
            file.local_path = storage.relative_to_root(dest)
            file.downloaded_at = datetime.now(UTC)
            self.report.files_downloaded += 1
        except CanvasError as exc:
            logger.info("跳过下载 %s: %s", file.display_name, exc)
        except Exception as exc:  # noqa: BLE001 下载失败不该中断整轮同步
            logger.warning("下载 %s 失败: %s", file.display_name, exc)

    # ---------- 通知 & 水位线 ----------

    async def _emit_event(
        self,
        kind: EventKind,
        title: str,
        summary: str,
        course_id: int | None = None,
        item: CanvasItem | None = None,
        file_id: int | None = None,
        payload: dict | None = None,
        dedup: tuple[str, str] | None = None,
    ) -> None:
        # `dedup` 让调用方自己给 (锚点, 版本)。没有它的话"链接被换掉"这类事件
        # 既没有 item 也没有 file_id，锚点会退化成字面量 "fileNone"，
        # 于是同一天里所有的链接更新会被去重成一条。
        if dedup is not None:
            anchor, version = dedup
        else:
            anchor = f"item{item.id}" if item else f"file{file_id}"
            version = item.content_hash if item else str(datetime.now(UTC).date())
        dedup_key = f"{kind}:{anchor}:{version}"

        exists = (
            await self.db.execute(
                select(NotificationEvent.id).where(NotificationEvent.dedup_key == dedup_key)
            )
        ).scalar_one_or_none()
        if exists:
            return

        self.db.add(
            NotificationEvent(
                kind=kind,
                course_id=course_id,
                canvas_item_id=item.id if item else None,
                file_id=file_id,
                title=title,
                summary=summary,
                payload=payload,
                dedup_key=dedup_key,
            )
        )
        self.report.events_created += 1

    async def _get_sync_state(self, resource_key: str) -> SyncState:
        state = (
            await self.db.execute(
                select(SyncState).where(SyncState.resource_key == resource_key)
            )
        ).scalar_one_or_none()
        if state is None:
            state = SyncState(resource_key=resource_key)
            self.db.add(state)
            # 这里必须 commit 而不是 flush。flush 会开启写事务并一直持有到 commit，
            # 而接下来就是几十秒的 Canvas 请求 —— SQLite 只允许一个写者，
            # 拿着写锁去等网络会让其他并发的课程全部锁死。
            await self.db.commit()
        return state
