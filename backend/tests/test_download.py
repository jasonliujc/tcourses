"""单个文件下载。

两条设计决定要被钉住：

- **不看链接多旧，先拿库里那条试。** 换一条新链接要向 Canvas 多发一个请求，
  实测在这条链路上就是十几秒（4 次 TLS 握手）。而实测 37 小时前取的链接照样
  返回 200 —— 按 TTL 保守判断等于给绝大多数下载白加一次往返。
- **失败要有兜底，而且要说得出话。** 链接真过期了就换一条重试；两次都不行
  必须返回一个带文件名的错误，前端要靠它在右下角弹提示。

接口是转发（StreamingResponse）而不是 302 重定向：重定向会让浏览器跳走，
按钮转不了圈，失败也只能让用户对着 Canvas 的报错页发呆。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.api.v1 import files as files_api
from app.core.config import settings as real_settings
from app.models.course import Course
from app.models.file import CourseFile


class _Configured:
    """真实配置的代理，只把 canvas_configured 顶成 True。"""

    canvas_configured = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


class _Resp:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self._body = body
        self.headers = {"content-length": str(len(body))}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]


class _Ctx:
    def __init__(self, resp: _Resp, log: list[str]):
        self._resp = resp
        self._log = log

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *_exc):
        self._log.append("closed")
        return False


class _FakeCanvas:
    """按 url 决定成败，并记下调用顺序 —— 顺序本身就是要测的东西。"""

    #: url -> (状态码, 内容)
    routes: dict[str, tuple[int, bytes]] = {}
    #: get_file 返回的新链接；None 表示换不到
    refreshed: str | None = None
    calls: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def stream(self, url: str):
        type(self).calls.append(f"stream:{url}")
        status, body = type(self).routes.get(url, (404, b""))
        return _Ctx(_Resp(status, body), type(self).calls)

    async def get_file(self, canvas_id: int) -> dict:
        type(self).calls.append(f"get_file:{canvas_id}")
        return {"url": type(self).refreshed} if type(self).refreshed else {}

    async def close(self):
        type(self).calls.append("close")


@pytest.fixture
def canvas(monkeypatch):
    _FakeCanvas.routes = {}
    _FakeCanvas.refreshed = None
    _FakeCanvas.calls = []
    monkeypatch.setattr(files_api, "CanvasClient", _FakeCanvas)
    monkeypatch.setattr(files_api, "settings", _Configured())
    return _FakeCanvas


async def _file(db, canvas_id: int, **kwargs) -> CourseFile:
    course = (
        await db.execute(select(Course).where(Course.canvas_id == 7700))
    ).scalar_one_or_none()
    if course is None:
        course = Course(canvas_id=7700, name="下载测试")
        db.add(course)
        await db.commit()
    f = CourseFile(
        canvas_id=canvas_id,
        course_id=course.id,
        display_name=kwargs.pop("display_name", f"file-{canvas_id}.pdf"),
        **kwargs,
    )
    db.add(f)
    await db.commit()
    return f


async def test_stale_url_is_tried_first(client, db, canvas):
    """链接三天没换过也直接用 —— 不能因为"旧"就先去 Canvas 换一条。"""
    url = "https://canvas.test/files/1/download?verifier=old"
    canvas.routes[url] = (200, b"PDF-BYTES")
    f = await _file(
        db,
        7701,
        download_url=url,
        metadata_fetched_at=datetime.now(UTC) - timedelta(days=3),
    )

    resp = await client.get(f"/api/v1/files/{f.id}/download")

    assert resp.status_code == 200
    assert resp.content == b"PDF-BYTES"
    # 关键：一次 get_file 都没发
    assert not [c for c in canvas.calls if c.startswith("get_file")]


async def test_dead_url_falls_back_to_a_fresh_one(client, db, canvas):
    """链接真过期了才换一条重试，并把新链接存下来 —— 下次就不用再换。"""
    dead = "https://canvas.test/files/2/download?verifier=dead"
    good = "https://canvas.test/files/2/download?verifier=new"
    canvas.routes = {dead: (403, b""), good: (200, b"OK")}
    canvas.refreshed = good
    f = await _file(db, 7702, download_url=dead)
    file_id = f.id

    resp = await client.get(f"/api/v1/files/{file_id}/download")

    assert resp.status_code == 200
    assert resp.content == b"OK"
    assert canvas.calls[:3] == [f"stream:{dead}", "closed", "get_file:7702"]

    db.expire_all()
    again = await db.get(CourseFile, file_id)
    assert again.download_url == good


async def test_both_attempts_failing_reports_the_file_name(client, db, canvas):
    """两次都不行就得报错，而且错误里要有文件名 —— 前端拿它弹右下角提示。"""
    dead = "https://canvas.test/files/3/download?verifier=dead"
    canvas.routes = {dead: (403, b"")}
    canvas.refreshed = None  # Canvas 也换不出新链接
    f = await _file(db, 7703, display_name="讲义第三章.pdf", download_url=dead)

    resp = await client.get(f"/api/v1/files/{f.id}/download")

    assert resp.status_code == 502
    # 报错只给 code 和数据，措辞在前端 —— 但文件名必须带上，
    # 否则批量下载时用户不知道是哪一个没下来
    detail = resp.json()["detail"]
    assert detail["code"] == "download_failed"
    assert detail["params"]["name"] == "讲义第三章.pdf"


async def test_deleted_file_says_so_instead_of_pretending(client, db, canvas):
    f = await _file(
        db,
        7704,
        display_name="没了的.pdf",
        download_url="https://canvas.test/x",
        is_deleted_on_canvas=True,
    )
    resp = await client.get(f"/api/v1/files/{f.id}/download")
    assert resp.status_code == 410
    # 压根不该碰网络
    assert canvas.calls == []


async def test_chinese_filename_survives_the_header(client, db, canvas):
    """裸 filename= 发中文名，浏览器存下来是一串乱码。"""
    url = "https://canvas.test/files/5/download"
    canvas.routes[url] = (200, b"x")
    f = await _file(db, 7705, display_name="第一周 讲义.pdf", download_url=url)

    resp = await client.get(f"/api/v1/files/{f.id}/download")

    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "%E7%AC%AC%E4%B8%80%E5%91%A8" in disposition  # 「第一周」
