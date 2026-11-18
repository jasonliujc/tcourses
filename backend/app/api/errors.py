"""报错也只给 code，不给句子。

前端有两套界面语言，而 `HTTPException(409, "同步进行中…")` 这样的写法把中文
钉死在了接口里 —— 英文界面上只能原样弹一句中文出来。所以这里统一改成
`{"code": "...", "params": {...}}`，措辞在前端的词典里。

参数只放**数据**（文件名、数量），不放已经拼好的短语 —— 那又会把语言带回来。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def api_error(status: int, code: str, **params: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "params": params})
