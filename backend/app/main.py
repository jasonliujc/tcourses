import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.services import canvas_connection
from app.workers.scheduler import sync_manager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.demo_mode:
        from app.demo import bootstrap

        await bootstrap.prepare()
    else:
        async with SessionLocal() as db:
            await canvas_connection.load(db)
    sync_manager.start()

    if settings.demo_mode:
        pass  # 演示数据在 prepare() 里已经同步过两轮
    elif settings.sync_on_startup and settings.canvas_configured:
        # 不阻塞启动：让首轮同步在后台跑，前端可以先进来看空壳
        asyncio.create_task(sync_manager.run_sync())
    elif not settings.canvas_configured:
        logger.warning("Canvas 未配置，跳过同步。在设置页填入 Canvas 地址和访问令牌")

    yield
    sync_manager.shutdown()


app = FastAPI(
    title="TCourses API",
    description="Local-first Canvas LMS companion: course resources, calendar and tasks.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {
        "status": "ok",
        "canvas_configured": settings.canvas_configured,
        "sync_running": sync_manager.is_running,
        "demo_mode": settings.demo_mode,
    }


# ---------- 打包好的前端 ----------
#
# 桌面版只有一个进程一个端口：后端顺带把 vite build 的产物发出去。必须放在文件最后，
# 兜底路由要排在所有 API 路由后面。开发时没设 STATIC_DIR，前端照旧走 vite。

_static = Path(settings.static_dir) if settings.static_dir else None
if _static is not None and (_static / "index.html").is_file():
    _root = _static.resolve()
    if (_root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = (_root / path).resolve()
        if path and target.is_file() and _root in target.parents:
            return FileResponse(target)
        # 前端路由（/calendar、/courses/3…）都交给 index.html
        return FileResponse(_root / "index.html")
