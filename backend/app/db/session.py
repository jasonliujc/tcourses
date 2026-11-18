from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _record) -> None:
        """每个连接都要单独设，SQLite 的 PRAGMA 不是全局的。"""
        cursor = dbapi_connection.cursor()
        # 默认不执行外键约束，不开的话所有 ondelete="CASCADE" 都是摆设 ——
        # 删掉一门课，它的文件、分组、更新提醒会全部变成孤儿留在库里
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL 让读写不互斥：同步时多门课并发写，前端还能正常读
        cursor.execute("PRAGMA journal_mode=WAL")
        # 拿不到写锁时等待而不是立刻报 "database is locked"
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每个请求一个 session。"""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """第一阶段 demo 用 create_all 建表；正式迁移走 alembic。"""
    from app.db.base import Base
    from app.db.migrate import ensure_columns
    from app.models import registry  # noqa: F401  确保所有模型被导入

    async with engine.begin() as conn:
        await ensure_columns(conn)  # 先补列，再建新表
        await conn.run_sync(Base.metadata.create_all)
