from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "dev"
    database_url: str = "sqlite+aiosqlite:///./tcourses.db"
    storage_root: str = "../storage"
    cors_origins: str = "http://localhost:5173"

    canvas_base_url: str = "https://canvas.instructure.com"
    canvas_access_token: str = ""
    # 演示模式：连的是 app/demo 里模拟的 Canvas，数据是生成的一个学期。
    # 由启动器（app.desktop --demo）设置，并且总是配一个单独的数据库
    demo_mode: bool = False
    # 打包好的前端（vite build 的产物）。设了就由后端一并提供，整个应用一个端口
    static_dir: str = ""

    sync_interval_minutes: int = 15
    sync_on_startup: bool = True
    # 两个开关都默认关闭：第一次跑起来时给最小、最快、最干净的一份数据，
    # 想要更多再自己去设置页打开。每一个打开都意味着更慢的同步或更吵的列表。
    # （同步哪些课程不在这里 —— 那是逐门选的，见 Course.sync_enabled）
    auto_download_files: bool = False
    max_download_mb: int = 100
    # 同步页面/公告正文里的内联图片（banner、logo）
    sync_inline_images: bool = False
    # 收不收教授贴在正文里的外部链接。默认开：录播、Google Docs 这类东西
    # 对学生和 PDF 一样重要
    sync_links: bool = True
    # 公告里的链接默认**不收**。实测公告贡献 74% 的链接，而内容压倒性是招聘会
    # 报名、问卷、活动通知 —— 收进来会把资源库淹掉（一门通知类课程 58 个文件
    # 对 1432 个链接）
    sync_announcement_links: bool = False
    # 普通讨论区（不是公告）。默认关：实测这几门课都是 0 条，UofT 的讨论基本都
    # 搬到 Piazza / Ed 了；真有内容时也只该收主题正文，回帖是学生内容
    sync_discussions: bool = False
    # 每门课最多抓多少个页面的正文。解析富文本本身不限量（纯本地正则），
    # 但取正文要发网络请求，所以这里设上限。超出会在同步状态里明确告警，
    # 而不是悄悄截断 —— 截断意味着有文件永远扫不到。
    max_page_fetch: int = 500
    # 页面正文多久算"该刷新了"（小时）。Canvas 的 module 条目不带页面的
    # updated_at，没法免费判断页面变没变，只能靠时间。
    page_cache_hours: int = 6
    # 每门课每轮最多重新抓取几个页面。分批是为了让每轮开销恒定 ——
    # 如果到期就全刷，所有页面会在同一轮集体过期，同步时间突然跳几分钟。
    page_refresh_batch: int = 10
    # 硬性兜底：任何页面/文件超过这么多天没和 Canvas 核对过，就必须优先复查，
    # 排在所有"按活跃度该查"的前面。保证库里不存在超过这个天数没核对的条目。
    max_recheck_days: int = 30
    # 每门课每轮最多重新拉几个文件的元数据（只有超期的才会被拉）
    file_recheck_batch: int = 10
    # 同时同步几门课。瓶颈是 Canvas 的响应延迟，并发能线性摊薄；
    # 但开太大会撞 Canvas 的配额和 SQLite 的写锁，实测 4-6 是甜点区。
    sync_concurrency: int = 4
    # 被学校禁用的端点（403）多久重试一次（小时）。不记住的话每门课每轮都要
    # 白白发几个必然失败的请求，每个 2-8 秒。
    unavailable_retry_hours: int = 12
    # 已结课课程多久重新同步一次（小时）。它们的内容不会再变，
    # 却占了同步时间的大头 —— 每 15 分钟陪跑一遍纯属浪费。

    @field_validator("database_url")
    @classmethod
    def _anchor_sqlite_path(cls, url: str) -> str:
        """SQLite 相对路径按 backend/ 解析，而不是按启动时的工作目录。
        否则从项目根目录启动（比如桌面版启动器）会悄悄建一个空库。"""
        prefix = "sqlite+aiosqlite:///"
        if not url.startswith(prefix):
            return url
        p = Path(url[len(prefix):])
        if str(p) == ":memory:" or p.is_absolute():
            return url
        return prefix + (BACKEND_DIR / p).resolve().as_posix()

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_root)
        if not p.is_absolute():
            p = (BACKEND_DIR / p).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def canvas_configured(self) -> bool:
        return bool(self.canvas_access_token and self.canvas_base_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
