"""运行时设置：数据库里的值优先，没有就用 .env 的默认值。

只放"用户会在界面上切换"的开关。Canvas 令牌、数据库地址这类启动期配置仍留在 .env。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.setting import AppSetting

AUTO_DOWNLOAD_FILES = "auto_download_files"
SYNC_INLINE_IMAGES = "sync_inline_images"
SYNC_LINKS = "sync_links"
SYNC_ANNOUNCEMENT_LINKS = "sync_announcement_links"
SYNC_DISCUSSIONS = "sync_discussions"
SYNC_INTERVAL_MINUTES = "sync_interval_minutes"
# 在这边勾完成时，同时写回 Canvas 的 planner override。默认关：
# 打开意味着 TCourses 会改动用户 Canvas 上的数据
PLANNER_WRITEBACK = "planner_writeback"

# 界面上给用户选的间隔档位。0 表示关闭自动同步，只能手动触发。
INTERVAL_OFF = 0
INTERVAL_CHOICES = [INTERVAL_OFF, 1, 5, 15, 30, 60, 1440]

_BOOL_DEFAULTS: dict[str, bool] = {
    AUTO_DOWNLOAD_FILES: settings.auto_download_files,
    SYNC_INLINE_IMAGES: settings.sync_inline_images,
    SYNC_LINKS: settings.sync_links,
    SYNC_ANNOUNCEMENT_LINKS: settings.sync_announcement_links,
    SYNC_DISCUSSIONS: settings.sync_discussions,
    PLANNER_WRITEBACK: False,
}


@dataclass
class RuntimeSettings:
    auto_download_files: bool
    sync_inline_images: bool = False
    sync_links: bool = True
    sync_announcement_links: bool = False
    sync_discussions: bool = False
    sync_interval_minutes: int = 15
    planner_writeback: bool = False


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


async def load(db: AsyncSession) -> RuntimeSettings:
    rows = (await db.execute(select(AppSetting))).scalars().all()
    stored = {r.key: r.value for r in rows}

    interval = settings.sync_interval_minutes
    if SYNC_INTERVAL_MINUTES in stored:
        try:
            interval = int(stored[SYNC_INTERVAL_MINUTES])
        except ValueError:
            pass

    return RuntimeSettings(
        auto_download_files=_parse_bool(
            stored.get(AUTO_DOWNLOAD_FILES, str(_BOOL_DEFAULTS[AUTO_DOWNLOAD_FILES]))
        ),
        sync_inline_images=_parse_bool(
            stored.get(SYNC_INLINE_IMAGES, str(_BOOL_DEFAULTS[SYNC_INLINE_IMAGES]))
        ),
        sync_links=_parse_bool(stored.get(SYNC_LINKS, str(_BOOL_DEFAULTS[SYNC_LINKS]))),
        sync_announcement_links=_parse_bool(
            stored.get(SYNC_ANNOUNCEMENT_LINKS, str(_BOOL_DEFAULTS[SYNC_ANNOUNCEMENT_LINKS]))
        ),
        sync_discussions=_parse_bool(
            stored.get(SYNC_DISCUSSIONS, str(_BOOL_DEFAULTS[SYNC_DISCUSSIONS]))
        ),
        sync_interval_minutes=interval,
        planner_writeback=_parse_bool(
            stored.get(PLANNER_WRITEBACK, str(_BOOL_DEFAULTS[PLANNER_WRITEBACK]))
        ),
    )


async def set_value(db: AsyncSession, key: str, value: bool | int) -> None:
    if key == SYNC_INTERVAL_MINUTES:
        if int(value) not in INTERVAL_CHOICES:
            raise ValueError(f"同步间隔只能是 {INTERVAL_CHOICES} 之一")
        stored = str(int(value))
    elif key in _BOOL_DEFAULTS:
        stored = str(bool(value)).lower()
    else:
        raise ValueError(f"未知设置项: {key}")

    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=stored))
    else:
        row.value = stored
    await db.flush()
