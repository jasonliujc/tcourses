"""Canvas 连接（地址 + 令牌）：存在数据库里，在设置页里改。

`.env` 里的 CANVAS_BASE_URL / CANVAS_ACCESS_TOKEN 退化成首次启动的默认值 ——
打包成桌面软件之后用户碰不到 `.env`。

存进来之后直接写回进程里的 `settings` 单例：用到凭据的地方都是调用时才读
`settings.canvas_*`，所以改完立刻生效，不用重启。

令牌以明文存在本地 SQLite 里，和它原来躺在 `.env` 里是同一个安全级别。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.canvas.client import CanvasClient
from app.models.course import Course
from app.models.setting import AppSetting

BASE_URL_KEY = "canvas_base_url"
TOKEN_KEY = "canvas_access_token"
# "主机#用户 id"。用来判断新令牌是不是换了一个账号
ACCOUNT_KEY = "canvas_account"
ACCOUNT_NAME_KEY = "canvas_account_name"


@dataclass
class Account:
    key: str
    name: str | None


def normalize_url(raw: str) -> str:
    """只留协议和主机：用户常常把课程页的地址整个贴进来。"""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty")
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or " " in text:
        raise ValueError(text)
    return f"{parts.scheme}://{parts.netloc.lower()}"


def current_url() -> str:
    try:
        return normalize_url(settings.canvas_base_url)
    except ValueError:
        return settings.canvas_base_url


def token_hint(token: str) -> str | None:
    """界面上只给末四位，令牌本身永远不出接口。"""
    return token[-4:] if len(token) >= 8 else None


def is_changed(base_url: str, token: str) -> bool:
    if not settings.canvas_configured:
        return True
    return base_url != current_url() or token != settings.canvas_access_token


def _apply(base_url: str, token: str) -> None:
    settings.canvas_base_url = base_url
    settings.canvas_access_token = token


async def _stored(db: AsyncSession) -> dict[str, str]:
    keys = (BASE_URL_KEY, TOKEN_KEY, ACCOUNT_KEY, ACCOUNT_NAME_KEY)
    rows = (await db.execute(select(AppSetting).where(AppSetting.key.in_(keys)))).scalars()
    return {r.key: r.value for r in rows}


async def load(db: AsyncSession) -> None:
    """启动时调用：库里有就用库里的，没有就保持 .env 的值。"""
    stored = await _stored(db)
    if stored.get(BASE_URL_KEY) and stored.get(TOKEN_KEY):
        _apply(stored[BASE_URL_KEY], stored[TOKEN_KEY])


async def account_name(db: AsyncSession) -> str | None:
    return (await _stored(db)).get(ACCOUNT_NAME_KEY)


async def identify(base_url: str, token: str) -> Account:
    """拿新的地址和令牌问一次 Canvas"我是谁"。失败抛 CanvasError。"""
    async with CanvasClient(base_url, token) as client:
        profile = await client.probe()
    host = urlsplit(base_url).netloc
    return Account(key=f"{host}#{profile.get('id')}", name=profile.get("name"))


async def account_changed(db: AsyncSession, new: Account) -> bool:
    """换成了另一个账号，而且库里还有上一个账号的课。

    第一次配置（库里没课）不算换。老版本只配过 .env、没记下账号的，
    用当前凭据补问一次；当前凭据也失效了就只能看主机变没变。
    """
    has_courses = (await db.execute(select(func.count()).select_from(Course))).scalar_one()
    if not has_courses:
        return False
    old = (await _stored(db)).get(ACCOUNT_KEY)
    if old is None and settings.canvas_configured:
        try:
            old = (await identify(current_url(), settings.canvas_access_token)).key
        except Exception:  # noqa: BLE001 旧令牌失效是常见情况，不是错误
            old = None
    if old is None:
        return urlsplit(current_url()).netloc != new.key.split("#", 1)[0]
    return old != new.key


async def save(db: AsyncSession, base_url: str, token: str, account: Account) -> None:
    values = {
        BASE_URL_KEY: base_url,
        TOKEN_KEY: token,
        ACCOUNT_KEY: account.key,
        ACCOUNT_NAME_KEY: account.name or "",
    }
    for key, value in values.items():
        row = await db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value))
        else:
            row.value = value
    await db.flush()
    _apply(base_url, token)
