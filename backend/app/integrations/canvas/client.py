"""Canvas LMS REST API 客户端。

认证用个人访问令牌（Canvas → Account → Settings → New Access Token）。
之所以不用 OAuth2：Canvas 的 OAuth 需要学校管理员在实例上创建 Developer Key，
demo 阶段拿不到；架构上 `CanvasClient` 只依赖一个 token 字符串，将来换 OAuth
只需替换 token 的来源。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_LINK_RE = re.compile(r'<(?P<url>[^>]+)>;\s*rel="(?P<rel>[^"]+)"')


class CanvasError(Exception):
    pass


class CanvasAuthError(CanvasError):
    """token 无效或过期 —— 重试没有意义。"""


class CanvasRateLimited(CanvasError):
    """值得退避重试。"""


class CanvasNotAvailable(CanvasError):
    """该端点被学校关闭（常见于 files），调用方应走降级路径。"""


class CanvasUnreachable(CanvasError):
    """地址连不上，或者连上的不是 Canvas。"""


def _is_transient(exc: BaseException) -> bool:
    """这个异常值不值得重试。

    一轮同步要发几百个请求，网络层抖动是常态：超时、连接被拒、
    **服务端不返回响应就掐断连接**（RemoteProtocolError）都会遇到。
    这些全是 httpx.TransportError 的子类 —— 逐个往列表里补会补一个漏一个，
    所以直接覆盖整类。

    两个例外不重试：LocalProtocolError 和 UnsupportedProtocol 是
    我们自己把请求构造错了，重试多少次结果都一样。
    """
    if isinstance(exc, CanvasRateLimited):
        return True
    if isinstance(exc, httpx.LocalProtocolError | httpx.UnsupportedProtocol):
        return False
    return isinstance(exc, httpx.TransportError)


def _parse_next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for m in _LINK_RE.finditer(link_header):
        if m.group("rel") == "next":
            return m.group("url")
    return None


#: 演示模式装进来的模拟 Canvas（见 app/demo）。装了之后所有 CanvasClient 都走它，
#: 构造客户端的那几处地方不用知道演示模式的存在
_transport: httpx.AsyncBaseTransport | None = None


def install_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


class CanvasClient:
    def __init__(self, base_url: str, access_token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self._client = httpx.AsyncClient(
            transport=_transport,
            timeout=timeout,
            # 默认允许 100 个并发连接，对单个 Canvas 账号太激进 ——
            # 服务端掐连接正是 RemoteProtocolError 的常见来源
            limits=httpx.Limits(
                max_connections=10, max_keepalive_connections=5, keepalive_expiry=15.0
            ),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json+canvas-string-ids, application/json",
            },
            follow_redirects=True,
        )

    async def __aenter__(self) -> CanvasClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ---------- 底层请求 ----------

    @retry(
        retry=retry_if_exception(lambda exc: _is_transient(exc)),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if not url.startswith("http"):
            url = f"{self.base_url}/api/v1{url}"

        resp = await self._client.request(method, url, **kwargs)

        if resp.status_code == 401:
            raise CanvasAuthError("Canvas 拒绝了访问令牌（401），请在设置里更新令牌")
        if resp.status_code == 403:
            body = resp.text[:300]
            if "Rate Limit Exceeded" in body:
                raise CanvasRateLimited(body)
            # Canvas 用 403 表示"这个功能对你不开放"，例如学校关掉了 Files 标签页
            raise CanvasNotAvailable(f"403 {url}: {body}")
        if resp.status_code == 404:
            raise CanvasNotAvailable(f"404 {url}")
        if resp.status_code >= 500:
            raise CanvasRateLimited(f"{resp.status_code} from Canvas, will retry")

        resp.raise_for_status()

        # Canvas 给每个 token 一个漏桶配额；快见底时主动让路，避免被硬限流
        remaining = resp.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None:
            try:
                if float(remaining) < 100:
                    logger.warning("Canvas 配额剩余 %s，退避 2s", remaining)
                    await asyncio.sleep(2)
            except ValueError:
                pass

        return resp

    async def get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 50,
        stop_after_page: Callable[[list[dict]], bool] | None = None,
    ) -> list[dict]:
        """跟随 Link header 的 rel="next" 把所有页拉完。

        `stop_after_page` 用来做增量翻页：Canvas 按时间倒序返回，所以一旦某一页
        全是库里已有且没变的内容，更老的页面必然也没变，翻下去纯属浪费。
        一门 638 条公告的课，全量要 7 个请求，增量只要 1 个 —— 每个请求 2-8 秒。
        """
        params = {"per_page": 100, **(params or {})}
        results: list[dict] = []
        url: str | None = path
        pages = 0

        while url and pages < max_pages:
            resp = await self._request("GET", url, params=params if pages == 0 else None)
            data = resp.json()
            page_items = data if isinstance(data, list) else [data]
            results.extend(page_items)
            pages += 1

            if stop_after_page is not None and page_items and stop_after_page(page_items):
                break

            url = _parse_next_link(resp.headers.get("Link"))

        return results

    async def get_one(self, path: str, params: dict[str, Any] | None = None) -> dict:
        resp = await self._request("GET", path, params=params)
        return resp.json()

    # ---------- 业务端点 ----------

    async def get_self(self) -> dict:
        return await self.get_one("/users/self/profile")

    async def probe(self, timeout: float = 15.0) -> dict:
        """验证地址和令牌：只发一次请求，不重试。

        `_request` 会对网络错误退避重试四次 —— 地址填错时用户要干等一分钟才知道。
        """
        url = f"{self.base_url}/api/v1/users/self/profile"
        try:
            resp = await self._client.get(url, timeout=timeout)
        except httpx.HTTPError as exc:
            raise CanvasUnreachable(str(exc)) from exc
        if resp.status_code == 401:
            raise CanvasAuthError("401")
        # 填成别的网站时多半是 404 或一张 HTML 页面
        if resp.status_code != 200 or "json" not in resp.headers.get("content-type", ""):
            raise CanvasUnreachable(f"{resp.status_code} {url}")
        return resp.json()

    async def list_courses(self, enrollment_state: str = "active") -> list[dict]:
        """enrollment_state: active（在读）/ completed（已结课）/ invited_or_pending。"""
        params: dict[str, Any] = {
            "enrollment_state": enrollment_state,
            "include[]": ["term", "favorites"],
        }
        if enrollment_state == "active":
            params["state[]"] = ["available"]
        return await self.get_paginated("/courses", params)

    async def list_tabs(self, course_id: int) -> list[dict]:
        """课程左边那一列导航 —— **这门课到底有哪些板块**。

        这是"不漏"的地基：不再列举我们知道的容器，而是问 Canvas 这门课有什么，
        再拿它和我们扫过的对账。形状不一样的课我们至少能**看见**自己漏了什么。
        """
        return await self.get_paginated(f"/courses/{course_id}/tabs")

    async def list_external_tools(self, course_id: int) -> list[dict]:
        """挂在课程上的 LTI 工具。导航栏里的 Piazza / MarkUs / 各种自定义板块
        都是它 —— 目的地藏在 `custom_fields.url` 里，`url` 字段只是跳转器地址。"""
        return await self.get_paginated(f"/courses/{course_id}/external_tools")

    async def list_quizzes(self, course_id: int) -> list[dict]:
        """测验。`description` 是富文本，教授常把范围、参考资料写在里面。"""
        return await self.get_paginated(f"/courses/{course_id}/quizzes")

    async def get_front_page(self, course_id: int) -> dict:
        """课程首页。它本身是一个页面，但 pages 端点被禁时列不出来。"""
        return await self.get_one(f"/courses/{course_id}/front_page")

    async def get_syllabus(self, course_id: int) -> str:
        """课程大纲正文。

        大纲挂在课程对象上，不是页面也不是作业 —— 必须显式 include 才拿得到，
        而这一路以前完全没扫。一门课就一个请求，相对整轮同步可以忽略。
        """
        data = await self.get_one(f"/courses/{course_id}", {"include[]": "syllabus_body"})
        return data.get("syllabus_body") or ""

    async def list_assignments(self, course_id: int) -> list[dict]:
        return await self.get_paginated(
            f"/courses/{course_id}/assignments",
            {"include[]": ["submission"], "order_by": "due_at"},
        )

    async def list_announcements(
        self, course_id: int, stop_after_page: Callable[[list[dict]], bool] | None = None
    ) -> list[dict]:
        return await self.get_paginated(
            f"/courses/{course_id}/discussion_topics",
            {"only_announcements": True},
            stop_after_page=stop_after_page,
        )

    async def list_discussions(self, course_id: int) -> list[dict]:
        """普通讨论区（不含公告）。

        `discussion_topics` 一张表装两种东西 —— 公告走 `only_announcements=true`
        单独拉，这里拿到的是师生都能回帖的讨论区，用 `is_announcement` 再滤一遍。
        只看主题正文：回帖是学生内容，量大且不是课程资料。
        """
        topics = await self.get_paginated(f"/courses/{course_id}/discussion_topics")
        return [t for t in topics if not t.get("is_announcement")]

    async def list_pages(self, course_id: int) -> list[dict]:
        return await self.get_paginated(f"/courses/{course_id}/pages", {"sort": "updated_at"})

    async def list_files(self, course_id: int) -> list[dict]:
        """课程文件。注意：不少学校在课程里隐藏了 Files 标签页，这时会抛 CanvasNotAvailable。"""
        return await self.get_paginated(f"/courses/{course_id}/files", {"sort": "updated_at"})

    async def list_folders(self, course_id: int) -> list[dict]:
        """拿文件夹树是为了把 file.folder_id 还原成人类可读的路径。"""
        return await self.get_paginated(f"/courses/{course_id}/folders")

    async def list_modules_with_items(self, course_id: int) -> list[dict]:
        """课程模块（含条目）。很多教授只挂 Modules 不用 Files，这是主要的文件来源之一。"""
        return await self.get_paginated(
            f"/courses/{course_id}/modules", {"include[]": ["items"]}
        )

    async def get_file(self, file_id: int | str) -> dict:
        """单个文件的元数据。

        关键：即使学校把 /courses/:id/files 列表关了（403），这个端点通常仍然可用 ——
        只要你能从别处（module 条目、公告正文链接）拿到 file_id，就能取回文件。
        """
        return await self.get_one(f"/files/{file_id}")

    async def get_folder(self, folder_id: int | str) -> dict:
        return await self.get_one(f"/folders/{folder_id}")

    async def get_page_body(self, course_id: int, page_url: str) -> dict:
        """页面列表不返回 body，要正文得单独取。"""
        return await self.get_one(f"/courses/{course_id}/pages/{page_url}")

    async def list_conversations(self) -> list[dict]:
        """Canvas 收件箱：教授私信 / 群发都在这里。"""
        return await self.get_paginated("/conversations", {"scope": "inbox"})

    async def get_conversation(self, conversation_id: str) -> dict:
        return await self.get_one(f"/conversations/{conversation_id}")

    # ---------- 日历与待办 ----------

    async def list_planner_items(
        self, start_date: str, end_date: str, context_codes: list[str] | None = None
    ) -> list[dict]:
        """Canvas To Do / 日历截止 / 事件的统一来源。返回的截止时间是**对本人生效**的
        那一份（分组、section 的差异已算好），还带本人的提交状态和完成标记。"""
        params: dict[str, Any] = {"start_date": start_date, "end_date": end_date}
        if context_codes:
            params["context_codes[]"] = context_codes
        return await self.get_paginated("/planner/items", params)

    async def list_course_events(
        self, course_canvas_id: int, start_date: str, end_date: str
    ) -> list[dict]:
        """一门课的日历事件。比 Planner 里那份多出全天日期、重复系列这些字段。

        **只能一门一门问**：context_codes 里带上 `user_self`，UofT 直接 403。
        """
        return await self.get_paginated(
            "/calendar_events",
            {
                "type": "event",
                "context_codes[]": f"course_{course_canvas_id}",
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    async def get_colors(self) -> dict[str, str]:
        """用户在 Canvas 里给每门课设的颜色：`{"course_123": "#254284"}`。"""
        data = await self.get_one("/users/self/colors")
        return data.get("custom_colors") or {}

    async def create_planner_override(
        self, plannable_type: str, plannable_id: str, marked_complete: bool
    ) -> dict:
        """写回 Canvas：把一条标记为完成 / 未完成。只在用户打开了回写开关时才会调。"""
        resp = await self._request(
            "POST",
            "/planner/overrides",
            json={
                "plannable_type": plannable_type,
                "plannable_id": plannable_id,
                "marked_complete": marked_complete,
            },
        )
        return resp.json()

    async def update_planner_override(self, override_id: int, marked_complete: bool) -> dict:
        resp = await self._request(
            "PUT", f"/planner/overrides/{override_id}", json={"marked_complete": marked_complete}
        )
        return resp.json()

    def stream(self, url: str):
        """裸的流式 GET，上下文交给调用方管。

        单个文件下载要**边收边转发给浏览器**：先把整个文件落到本地临时文件再发出去
        会让用户白等一倍时间。调用方需要在 `__aenter__` 之后自己看状态码决定要不要
        换一条链接重试，所以这里不能像 `download_file` 那样把整件事包起来。
        """
        return self._client.stream("GET", url)

    async def download_file(self, url: str, dest: Path, max_bytes: int) -> int:
        """流式下载到 dest，超过 max_bytes 直接放弃（避免几百 MB 的录课视频占满磁盘）。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        written = 0

        async with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    written += len(chunk)
                    if written > max_bytes:
                        fh.close()
                        tmp.unlink(missing_ok=True)
                        # 这条会原样进 last_sync_error 显示给用户，而那一列没有翻译层 ——
                        # 诊断文本（HTTP 码、URL）本来也全是英文，保持一致
                        raise CanvasError(f"file exceeds the {max_bytes} byte limit, skipped")
                    fh.write(chunk)

        tmp.replace(dest)
        return written
