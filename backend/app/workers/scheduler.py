"""后台同步调度。

第一阶段用进程内 APScheduler，够 demo 用且零外部依赖。
将来要扛多用户/长任务时，把 `run_sync` 换成 Celery/ARQ 任务即可，接口不变。

**间隔从"上次跑完"算起，不是固定周期。** 固定周期的问题很实际：一轮同步要几十秒
到几分钟，如果设成 1 分钟一次，上一轮还没跑完下一轮就到点了，只会一直空转。
所以每轮结束后才安排下一次。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.integrations.canvas.client import CanvasClient
from app.integrations.canvas.sync import CanvasSyncService
from app.models.course import Course
from app.models.system import SyncState
from app.services import runtime_settings

logger = logging.getLogger(__name__)

_JOB_ID = "canvas_sync"

# 上次全量同步完成时间存这条 sync_state 里。
# 只放在内存里的话，进程一重启排期基准就丢了，间隔会退化成"从重启那刻起算"。
_FULL_SYNC_KEY = "system:full_sync"


class SyncManager:
    """集中管理"当前在跑什么 / 上次结果 / 下次什么时候跑"，供 /sync/status 查询。"""

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self._lock = asyncio.Lock()
        self.is_running = False
        self.last_report: dict | None = None
        # 按课程数计的进度，前端拿它画进度条。current_courses 是**同时**在跑的
        # 那几门（并发同步），不是一门
        self.progress: dict = {"done": 0, "total": 0, "current_courses": []}
        # 正在单独同步的课程 id，前端用它在课程页显示转圈
        self.syncing_course_ids: set[int] = set()
        self.interval_minutes: int = settings.sync_interval_minutes
        self.next_run_at: datetime | None = None
        # 上一次全量同步跑完的时刻。排期以它为基准，不是以"现在"为基准 ——
        # 用户改间隔时想要的是"距上次同步 30 分钟"，不是"从我改设置这刻起再等 30 分钟"
        self.last_completed_at: datetime | None = None

    @property
    def is_busy(self) -> bool:
        """全量或单课，只要有同步在跑就算忙。

        判断"能不能开始一件新事"必须用它，而不是只看 is_running ——
        单课同步时 is_running 是 False，光看它会放行全量同步，
        结果两边同时写同一门课。
        """
        return self.is_running or bool(self.syncing_course_ids)

    # ---------- 全量同步 ----------

    def mark_started(self) -> None:
        """在后台任务真正跑起来之前先把状态点亮，界面不会有"点了没反应"的空档。"""
        self.is_running = True
        self.progress = {"done": 0, "total": 0, "current_courses": []}

    async def run_sync(self, reschedule: bool = True) -> dict:
        """同步所有课程。

        reschedule=True 会把下一次自动同步顺延 —— 用户刚手动全量同步过，
        没道理一分钟后又自动跑一遍。单课同步不走这里，也就不会影响排期。
        """
        if not settings.canvas_configured:
            return {"skipped": "Canvas 未配置"}

        if self._lock.locked():
            logger.info("已有同步在进行，跳过本次触发")
            return self.last_report or {"skipped": "同步进行中"}

        async with self._lock:
            self.is_running = True
            try:

                def on_progress(done: int, total: int, courses: list[str]) -> None:
                    self.progress = {"done": done, "total": total, "current_courses": courses}

                self.progress = {"done": 0, "total": 0, "current_courses": []}
                async with SessionLocal() as db, CanvasClient(
                    settings.canvas_base_url, settings.canvas_access_token
                ) as client:
                    service = CanvasSyncService(db, client, on_progress=on_progress)
                    report = await service.sync_all()
                    self.last_report = report.as_dict()
                    logger.info("同步完成: %s", self.last_report)
                    return self.last_report
            except Exception as exc:  # noqa: BLE001 定时任务不能因为一次失败就死掉
                logger.exception("同步失败")
                self.last_report = {"error": str(exc)}
                return self.last_report
            finally:
                self.is_running = False
                await self._record_completion()
                if reschedule:
                    await self.reschedule()

    # ---------- 单课同步 ----------

    async def run_course_sync(self, course_id: int) -> dict:
        """只同步一门课。不影响自动同步的排期 —— 用户想刷新某一门课，
        不代表其他课也该重来，更不该把下一次全量往后推。"""
        if not settings.canvas_configured:
            self.syncing_course_ids.discard(course_id)
            return {"skipped": "Canvas 未配置"}
        # API 层可能已经登记过（为了让按钮立刻变状态），这里补一次即可
        self.syncing_course_ids.add(course_id)
        try:
            async with SessionLocal() as db, CanvasClient(
                settings.canvas_base_url, settings.canvas_access_token
            ) as client:
                service = CanvasSyncService(db, client)
                service.runtime = await runtime_settings.load(db)
                course = await db.get(Course, course_id)
                if course is None:
                    return {"error": "课程不存在"}
                await service.sync_one_course(course)
                await service.sync_planner(course)
                from app.services.file_organizer import organize_course_files

                try:
                    await organize_course_files(db, course_id)
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    logger.warning("课程 %s 自动整理失败: %s", course_id, exc)
                return service.report.as_dict()
        except Exception as exc:  # noqa: BLE001
            logger.exception("单课同步失败")
            return {"error": str(exc)}
        finally:
            self.syncing_course_ids.discard(course_id)

    # ---------- 排期 ----------

    async def _record_completion(self) -> None:
        """记下这轮跑完的时刻，落库以便重启后仍能正确排期。"""
        now = datetime.now(UTC)
        self.last_completed_at = now
        async with SessionLocal() as db:
            state = (
                await db.execute(
                    select(SyncState).where(SyncState.resource_key == _FULL_SYNC_KEY)
                )
            ).scalar_one_or_none()
            if state is None:
                state = SyncState(resource_key=_FULL_SYNC_KEY)
                db.add(state)
            state.status = "ok"
            state.last_synced_at = now
            state.last_success_at = now
            await db.commit()

    async def last_completed(self) -> datetime | None:
        """上次全量同步完成的时刻，内存里没有就回库里找。"""
        if self.last_completed_at is not None:
            return self.last_completed_at
        async with SessionLocal() as db:
            state = (
                await db.execute(
                    select(SyncState).where(SyncState.resource_key == _FULL_SYNC_KEY)
                )
            ).scalar_one_or_none()
        if state is not None and state.last_success_at is not None:
            stamp = state.last_success_at
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            self.last_completed_at = stamp
        return self.last_completed_at

    async def reschedule(self) -> None:
        """按当前设置安排下一次同步。基准是上一次同步**跑完**的时刻。"""
        async with SessionLocal() as db:
            rt = await runtime_settings.load(db)
        self.interval_minutes = rt.sync_interval_minutes

        # 间隔 0 = 用户关掉了自动同步，撤掉排期，只保留手动触发
        if self.interval_minutes == runtime_settings.INTERVAL_OFF:
            if self.scheduler.get_job(_JOB_ID):
                self.scheduler.remove_job(_JOB_ID)
            self.next_run_at = None
            logger.info("自动同步已关闭")
            return

        # 从"上次跑完"起算。从没跑过才从现在算。
        now = datetime.now(UTC)
        base = await self.last_completed() or now
        run_at = base + timedelta(minutes=self.interval_minutes)
        if run_at <= now:
            # 按新间隔早就该跑了（比如把 1 天改成 1 分钟），给个短延迟立刻补上
            run_at = now + timedelta(seconds=10)
        self.next_run_at = run_at
        self.scheduler.add_job(
            self._tick,
            "date",
            run_date=run_at,
            id=_JOB_ID,
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("下次同步安排在 %s（间隔 %s 分钟）", run_at.isoformat(), self.interval_minutes)

    async def _tick(self) -> None:
        """定时触发点。run_sync 结束时会自己安排下一次，形成链式排期。"""
        await self.run_sync(reschedule=True)

    def start(self) -> None:
        self.scheduler.start()
        # 启动时先排一次；真正的间隔在每轮同步结束后重算
        asyncio.create_task(self.reschedule())

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)


sync_manager = SyncManager()
