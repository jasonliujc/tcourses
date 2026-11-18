import os
import tempfile

# 必须在导入 app 之前设置：Settings 是 lru_cache 的，读一次就定型
_TMP_DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["STORAGE_ROOT"] = tempfile.mkdtemp()
os.environ["SYNC_ON_STARTUP"] = "false"
os.environ["CANVAS_ACCESS_TOKEN"] = ""

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db.session import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def _prepare_db():
    await init_db()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db():
    async with SessionLocal() as session:
        yield session
