"""API 出参里的公共类型。"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _as_utc_iso(value: datetime | None) -> str | None:
    """把时间统一序列化成带时区标记的 ISO 字符串。

    必须这么做：SQLite 不保存时区，从库里读出来的 datetime 是 naive 的，
    直接序列化就是 "2026-09-01T17:26:46"。浏览器拿到这种字符串会按**本地时区**
    解析，于是一个 UTC 时刻被当成本地时刻显示 —— 时差有多少就差多少小时。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


UTCDatetime = Annotated[
    datetime, PlainSerializer(_as_utc_iso, return_type=str | None, when_used="json")
]
