"""把一门课散落在各处的文件全部找出来。

问题：教授把资料放哪儿全凭习惯。有人规规矩矩用 Files 页面，有人只挂 Modules，
有人直接把 PDF 贴在公告正文里。更麻烦的是**很多学校（含 UofT）默认关掉了课程的
Files 标签页**，`/courses/:id/files` 直接返回 403 —— 只扫 Files 会漏掉绝大部分资料。

解法：多路并扫，用 canvas file_id 去重，每个来源单独记一条 FileSource。

    Files 页面        ─┐
    Modules（File 条目）─┤
    Modules（外链条目） ─┤
    课程大纲           ─┼─→ 按 file_id / URL 去重 ─→ CourseFile + 多条 FileSource
    公告附件+正文      ─┤
    页面/作业正文      ─┘

**课程大纲这一路是后来补上的，而它可能是整门课唯一有东西的地方。** 实测有一门课
的 pages 端点 404、没有作业、没有模块，四路全是 0，而大纲正文有 12 万字符、
91 个文件 + 36 个链接。大纲挂在课程对象上（`syllabus_body`），既不是页面
也不是作业，前面几路一条都碰不到它。

关键突破口：即使 `/courses/:id/files` 被禁，`GET /files/:id` 通常仍然可用。
所以只要能从别处扒到 file_id（module 条目的 content_id、富文本里的
`/courses/:cid/files/:fid` 链接），就能把文件元数据取回来。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import settings
from app.integrations.canvas import mapper
from app.integrations.canvas.client import CanvasClient, CanvasError, CanvasNotAvailable
from app.models.file import SourceKind
from app.services import boards, links

logger = logging.getLogger(__name__)

# Canvas 富文本里的文件链接：/courses/123/files/456 或 /files/456
_FILE_LINK_RE = re.compile(r"/(?:courses/\d+/)?files/(\d+)")

# 正文里的 <img> 标签。区分"内联装饰图"和"教授贴出来的资料"全靠它 —— 同一个
# /files/:id，出现在 <img src> 里是页面 banner/logo，出现在 <a href> 里是资料。
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)

# 同时打给 Canvas 的最大请求数。Canvas 按 token 计漏桶配额，开太大会被限流
_CONCURRENCY = 5

# ---------- 覆盖面：这门课有哪些板块，我们扫了几个 ----------
#
# **这是"不漏"的地基。** 原来的做法是列举"我们知道的容器"去扫，于是一门课只要
# 形状不一样就静悄悄地漏掉（实测某门课整门资料都在大纲里，界面上是 0 个资源）。
# 现在改成先问 Canvas「这门课有哪些板块」（课程导航栏），再和我们扫过的对账 ——
# 覆盖不到的会被记下来报给用户，而不是无声无息。

# 各来源的兜底容器名。任何一条来源都必须落进某个容器，否则侧边栏会漏掉文件。
UNGROUPED_PAGES = "Ungrouped"  # 找不到所属模块的页面
ROOT_FOLDER = "Root"  # 确实不在任何文件夹里
UNKNOWN_FOLDER = "Uncategorized"  # 有 folder_id 但解析不出路径（folders 端点被禁）
UNTITLED_MODULE = "Untitled module"


def scan_body_file_ids(body: str) -> tuple[set[int], set[int]]:
    """扫正文里的文件链接，分成 (只以 <img> 形式出现的, 其余的)。

    扫富文本必然连带把页面 banner、logo 收进来。要把它们和"教授贴出来的图片资料"
    分开，唯一可靠的信号是**这个 file_id 出现在什么标签里** —— `<img src>` 是
    渲染出来的装饰图，`<a href>` 是用户要点开的资料。两处都出现（"点开看大图"）
    算资料，不算装饰。

    实测 19 门课 2064 个文件：248 个只出现在 `<img>` 里，且全部是图片；
    反过来 1622 个正文链接文件没有一个被误判成内联图。
    """
    all_ids = {int(m) for m in _FILE_LINK_RE.findall(body)}
    if not all_ids:
        return set(), set()
    outside = {int(m) for m in _FILE_LINK_RE.findall(_IMG_TAG_RE.sub(" ", body))}
    return all_ids - outside, outside


@dataclass
class CollectedSource:
    source_kind: str
    source_key: str
    container_name: str | None = None
    item_title: str | None = None
    label: str | None = None
    html_url: str | None = None
    source_date: datetime | None = None
    sort_hint: int = 0
    canvas_item_canvas_id: str | None = None
    # 这条来源是正文里的 <img>。所有来源都是 <img> 的文件才算内联装饰图 ——
    # 只要它在 Files / Modules / 附件 / 正文超链接里露过一次面就不算。
    via_img: bool = False


@dataclass
class CollectedFile:
    canvas_file_id: int
    raw: dict | None = None
    sources: list[CollectedSource] = field(default_factory=list)


@dataclass
class CollectedLink:
    """教授贴在正文里的一个外部链接。

    身份是规范化之后的 URL（见 services/links.py），**不是**出现位置 ——
    同一个链接在一门课里贴了三次就是一行加三条来源，和文件完全一样。
    """

    resource_key: str
    url: str
    title: str
    sources: list[CollectedSource] = field(default_factory=list)


def link_source_key(item_ref: str, resource_key: str) -> str:
    """链接来源的幂等键。

    `item_ref|resource_key` 这个形状是**故意**的：判断"链接被换掉了"要按
    "同一个条目里、同一段锚文本"去比对上一轮，所以得能从 key 里切回条目。
    item_ref 里不会出现 `|`。
    """
    return f"{item_ref}|{resource_key}"


def split_item_ref(source_key: str) -> str:
    return source_key.split("|", 1)[0]


class FileCollector:
    def __init__(
        self,
        client: CanvasClient,
        course_canvas_id: int,
        course_name: str,
        include_inline_images: bool = True,
        include_links: bool = True,
        include_announcement_links: bool = False,
        include_discussions: bool = False,
    ):
        self.client = client
        self.course_canvas_id = course_canvas_id
        self.course_name = course_name
        # 用户设置："同步链接" / "同步公告中的链接"。关掉时在收集阶段就不产出，
        # 同步器还会顺手把库里已有的删掉
        self.include_links = include_links
        self.include_announcement_links = include_announcement_links
        # 用户设置："同步讨论区"。默认关
        self.include_discussions = include_discussions
        # 用户设置："同步内联图片"。关掉时这些文件在收集阶段就被丢掉，
        # 连补元数据的请求都省了；同步器还会顺手把库里已有的删掉。
        self.include_inline_images = include_inline_images
        # 本轮判定为内联装饰图的 canvas file_id，交给同步器做清理
        self.inline_image_ids: set[int] = set()
        self.files: dict[int, CollectedFile] = {}
        # resource_key -> 链接。只从**页面和作业**里收，不收公告 ——
        # 实测公告贡献了 74% 的链接，而且压倒性地是招聘会报名、问卷这类行政通知
        self.links: dict[str, CollectedLink] = {}
        self._folder_cache: dict[int, str | None] = {}
        self.stats: dict[str, int] = {}
        self.known_file_ids: set[int] = set()
        # url -> 页面 raw（含正文和 updated_at）。命中就不发请求 ——
        # module 里的页面动辄上千个，这是同步最大的开销。
        # **必须存整个 raw**：只存正文的话命中缓存的页面就没有 updated_at 了，
        # 而链接的日期只能从这里来（文件还能回落到自己的更新时间，链接不能）
        self.page_body_cache: dict[str, dict] = {}
        # 本轮该重新抓的页面（缓存里有，但该刷新了）
        self.stale_pages: set[str] = set()
        # 本轮新抓到的页面，交给 sync 落库，下轮就能复用
        self.fetched_pages: dict[str, dict] = {}
        # 本轮确认被学校禁用的端点，交给 sync 记下来，之后一段时间不再白试
        self.unavailable_endpoints: set[str] = set()
        self.skip_endpoints: set[str] = set()
        # 因为上限而没扫全的地方，会一路冒到同步状态里让用户看见
        self.truncated: list[str] = []
        # module 里 type=Page 的条目：(page_url, module 名, 条目标题, 排序)
        self._module_pages: list[tuple[str, str, str, int]] = []
        # page_url -> 它所属的 module 名。页面在侧边栏按模块分组就靠这个 ——
        # 没有它的话几百个页面只能平铺成一坨，完全没法用
        self._page_module: dict[str, str] = {}
        # 这门课有哪些板块（Canvas 导航栏），以及每个板块扫下来是什么结果。
        # 设置页直接渲染 board_status —— 用户看到的和我们扫的是同一份东西
        self.tabs: list[dict] = []
        self.board_status: dict[str, str] = {}
        self.board_counts: dict[str, int] = {}
        # 这门课有、但我们还不会扫的板块（标签名），要明确告诉用户
        self.uncovered_tabs: list[str] = []

    # ---------- 入口 ----------

    async def collect(
        self,
        canvas_items: list,
        known_file_ids: set[int] | None = None,
        page_body_cache: dict[str, dict] | None = None,
        skip_endpoints: set[str] | None = None,
        stale_pages: set[str] | None = None,
    ) -> dict[int, CollectedFile]:
        """canvas_items 是本课已入库的 CanvasItem（公告/页面/作业），
        它们的 .raw 里存着 Canvas 原始返回，扫富文本不需要再发请求。

        known_file_ids 是库里已有的文件。这些文件的元数据不再重新拉 ——
        首次同步之后，一轮同步的网络开销就只剩下"发现新文件"那几个请求。
        """
        self.known_file_ids = known_file_ids or set()
        self.page_body_cache = page_body_cache or {}
        self.skip_endpoints = skip_endpoints or set()
        self.stale_pages = stale_pages or set()
        # 先问清楚这门课有什么，再决定扫什么 —— 而不是照着一张固定清单扫
        await self._discover_tabs()
        await self._from_files()
        await self._from_modules()
        # 这两路会往 _module_pages 里追加页面（导航栏里指向本课页面的工具、
        # 以及首页），所以必须排在 _from_module_pages 之前
        await self._from_nav_tools()
        await self._from_front_page()
        await self._from_module_pages()
        await self._from_syllabus()
        await self._from_quizzes()
        await self._from_discussions()
        self._from_rich_text(canvas_items)
        self._flag_inline_images()

        # 只给"没见过且还没有元数据"的文件发请求
        await self._fetch_missing_metadata()

        # 取不到元数据、且库里也没有的，说明文件被删了或对学生不可见 —— 丢掉
        self.files = {
            fid: f
            for fid, f in self.files.items()
            if f.raw is not None or fid in self.known_file_ids
        }
        self.stats["total"] = len(self.files)
        self.stats.setdefault("link", 0)
        return self.files

    def _flag_inline_images(self) -> None:
        """判定内联装饰图，必要时就地丢掉。

        必须等四路都扫完再判：同一个图片可能在一个页面里是 banner、在另一处是
        Module 条目或正文超链接。只有**每一条**来源都是 `<img>` 才算装饰图。
        这一步要跑在补元数据之前 —— 丢掉的文件连那个请求都不用发。
        """
        self.inline_image_ids = {
            fid
            for fid, entry in self.files.items()
            if entry.sources and all(s.via_img for s in entry.sources)
        }
        self.stats["inline_image"] = len(self.inline_image_ids)
        if not self.include_inline_images:
            for fid in self.inline_image_ids:
                self.files.pop(fid, None)

    def _collect_links(
        self,
        found: list,
        item_ref: str,
        source_kind: str,
        container: str | None,
        label: str | None,
        html_url: str | None,
        date: datetime | None,
        sort_hint: int = 0,
    ) -> int:
        """把一段正文里的外部链接收进来。返回收到几条。

        `found` 是已经抽好的链接。收进来的入口不止富文本一种 —— 模块里的
        `ExternalUrl` 条目就是一个裸 URL，正文里根本没有 `<a>` 标签。

        锚文本存进 `item_title`：它既是显示用的标题，也是判断"这个链接被换掉了"
        的依据 —— 同一个条目里同一段锚文本，指向变了就是教授换了链接。
        """
        if not self.include_links:
            return 0
        if source_kind == SourceKind.ANNOUNCEMENT and not self.include_announcement_links:
            return 0
        added = 0
        for link in found:
            entry = self.links.get(link.resource_key)
            if entry is None:
                entry = CollectedLink(
                    resource_key=link.resource_key, url=link.url, title=link.title
                )
                self.links[link.resource_key] = entry
            entry.sources.append(
                CollectedSource(
                    source_kind=source_kind,
                    source_key=link_source_key(item_ref, link.resource_key),
                    container_name=container,
                    item_title=link.title,
                    label=label,
                    html_url=html_url,
                    source_date=date,
                    sort_hint=sort_hint,
                )
            )
            added += 1
        return added

    def _touch(self, file_id: int, raw: dict | None = None) -> CollectedFile:
        entry = self.files.get(file_id)
        if entry is None:
            entry = CollectedFile(canvas_file_id=file_id)
            self.files[file_id] = entry
        if raw is not None and entry.raw is None:
            entry.raw = raw
        return entry

    # ---------- 来源 1：Files 页面 ----------

    async def _from_files(self) -> None:
        if "files" in self.skip_endpoints:
            self.stats["files"] = 0
            return
        try:
            raw_files = await self.client.list_files(self.course_canvas_id)
        except CanvasNotAvailable:
            # 学校关掉了 Files 标签页 —— 这是常态，不是错误
            logger.info("course %s: Files 页面不可用，走其他来源", self.course_canvas_id)
            self.unavailable_endpoints.add("files")
            self.board_status[boards.FILES] = boards.UNAVAILABLE
            self.stats["files"] = 0
            return
        except CanvasError as exc:
            logger.warning("course %s: Files 拉取失败 %s", self.course_canvas_id, exc)
            self.stats["files"] = 0
            return

        await self._preload_folders()

        for raw in raw_files:
            file_id = int(raw["id"])
            folder_id = raw.get("folder_id")
            path = self._folder_cache.get(int(folder_id)) if folder_id else None
            if path:
                container = path
            elif folder_id:
                # 有 folder_id 却查不到路径 —— folders 端点被禁时会这样。
                # 这和"真的在根目录"是两回事，混为一谈会误导用户
                container = UNKNOWN_FOLDER
            else:
                container = ROOT_FOLDER
            entry = self._touch(file_id, raw)
            entry.sources.append(
                CollectedSource(
                    source_kind=SourceKind.FILES,
                    source_key=path or container,
                    container_name=container,
                    item_title=raw.get("display_name"),
                    label=container,
                    html_url=raw.get("url"),
                    source_date=mapper.parse_dt(raw.get("updated_at") or raw.get("modified_at")),
                )
            )
        self.stats["files"] = len(raw_files)

    async def _preload_folders(self) -> None:
        try:
            folders = await self.client.list_folders(self.course_canvas_id)
        except CanvasError:
            return
        for fid, path in mapper.build_folder_path_map(folders).items():
            self._folder_cache[fid] = path or None

    # ---------- 来源 2：Modules ----------

    async def _from_modules(self) -> None:
        if "modules" in self.skip_endpoints:
            self.stats["module"] = 0
            return
        try:
            modules = await self.client.list_modules_with_items(self.course_canvas_id)
        except CanvasNotAvailable as exc:
            logger.info("course %s: Modules 不可用 %s", self.course_canvas_id, exc)
            self.unavailable_endpoints.add("modules")
            self.board_status[boards.MODULES] = boards.UNAVAILABLE
            self.stats["module"] = 0
            return
        except CanvasError as exc:
            logger.info("course %s: Modules 拉取失败 %s", self.course_canvas_id, exc)
            self.stats["module"] = 0
            return

        count = 0
        external = 0
        for m_pos, module in enumerate(modules):
            module_name = module.get("name") or UNTITLED_MODULE
            for i_pos, item in enumerate(module.get("items") or []):
                sort_hint = (module.get("position") or m_pos) * 1000 + (
                    item.get("position") or i_pos
                )
                # 模块里挂的页面：正文里常有讲义链接，留到 _from_module_pages 去取
                if item.get("type") == "Page" and item.get("page_url"):
                    self._module_pages.append(
                        (item["page_url"], module_name, item.get("title") or "", sort_hint)
                    )
                    self._page_module[item["page_url"]] = module_name
                    continue
                # 教授直接把 Gradescope / Piazza / YouTube 播放列表挂成一个
                # 模块条目 —— 正文里没有任何 <a> 标签，扫富文本永远扫不到。
                # 实测有的课有二十多个这样的条目
                if item.get("type") in ("ExternalUrl", "ExternalTool"):
                    link = links.from_url(
                        item.get("external_url") or "",
                        item.get("title") or "",
                        self.client.base_url,
                    )
                    if link is not None:
                        external += self._collect_links(
                            [link],
                            item_ref=f"moditem:{item.get('id')}",
                            source_kind=SourceKind.MODULE,
                            container=module_name,
                            label=f"{module_name} · {item.get('title') or ''}".strip(" ·"),
                            html_url=item.get("html_url"),
                            date=None,
                            sort_hint=sort_hint,
                        )
                    continue
                if item.get("type") != "File" or not item.get("content_id"):
                    continue
                file_id = int(item["content_id"])
                entry = self._touch(file_id)
                entry.sources.append(
                    CollectedSource(
                        source_kind=SourceKind.MODULE,
                        source_key=str(item.get("id") or f"{m_pos}-{i_pos}"),
                        container_name=module_name,
                        item_title=item.get("title"),
                        label=f"{module_name} · {item.get('title') or ''}".strip(" ·"),
                        html_url=item.get("html_url"),
                        # module 条目没有自己的时间，排序时回落到文件的更新时间
                        sort_hint=sort_hint,
                    )
                )
                count += 1
        self.stats["module"] = count
        self.stats["link"] = self.stats.get("link", 0) + external
        self.stats["module_link"] = external

    # ---------- 发现：这门课有哪些板块 ----------

    async def _discover_tabs(self) -> None:
        """拉课程导航栏，算出哪些板块我们没覆盖。

        取不到就当作"没有额外信息"继续跑 —— 发现失败不该让整轮收集停下来，
        只是这一轮说不出覆盖面而已。
        """
        try:
            self.tabs = await self.client.list_tabs(self.course_canvas_id)
        except CanvasError as exc:
            logger.info("course %s: 导航栏取不到 %s", self.course_canvas_id, exc)
            return

        uncovered = []
        for tab in self.tabs:
            tab_id = str(tab.get("id") or "")
            if tab.get("hidden"):
                continue  # 学生看不见的板块，不算漏
            if tab_id in boards.NO_CONTENT_TABS:
                continue
            if tab_id.startswith("context_external_tool"):
                # 导航栏上的外部工具由 _from_nav_tools 扫，归到 Others
                self.board_status.setdefault(boards.OTHERS, boards.SCANNED)
                continue
            board = boards.BY_TAB.get(tab_id)
            if board is None:
                uncovered.append(str(tab.get("label") or tab_id))
                continue
            self.board_status[board.id] = boards.SCANNED
        self.uncovered_tabs = uncovered
        self.stats["tabs"] = len(self.tabs)
        if uncovered:
            self.board_status[boards.OTHERS] = boards.UNSUPPORTED
            logger.info("course %s: 未覆盖的板块 %s", self.course_canvas_id, uncovered)

    # ---------- 来源：导航栏上的外部工具 ----------

    async def _from_nav_tools(self) -> None:
        """导航栏上挂着的外部工具（Piazza / MarkUs / 教授自建的板块）。

        教授把 Piazza、答疑安排、往年题直接做成导航栏上的一项，正文里没有任何
        `<a>` 标签 —— 扫富文本永远扫不到（实测有一门课导航栏 14 项里 8 项是这个）。

        目的地藏在 `custom_fields.url` 里，工具本身的 `url` 只是跳转器地址。
        两种目的地分开处理：

        - 指向站外 → 收成一个链接
        - **指向本课的 Canvas 页面** → 交给页面那一路去扫。这一条尤其要紧：
          pages 端点被学校禁掉的课，这是发现那些页面的唯一入口。
        """
        tool_tabs = [
            t
            for t in self.tabs
            if str(t.get("id") or "").startswith("context_external_tool") and not t.get("hidden")
        ]
        if not tool_tabs:
            return
        try:
            tools = await self.client.list_external_tools(self.course_canvas_id)
        except CanvasError as exc:
            logger.info("course %s: 外部工具取不到 %s", self.course_canvas_id, exc)
            return

        by_id = {str(tool.get("id")): tool for tool in tools}
        found = 0
        for tab in tool_tabs:
            tool_id = str(tab.get("id")).rsplit("_", 1)[-1]
            tool = by_id.get(tool_id)
            if tool is None:
                continue
            target = (tool.get("custom_fields") or {}).get("url") or ""
            label = str(tab.get("label") or tool.get("name") or "")
            if not target:
                continue

            page_url = self._own_page_url(target)
            if page_url is not None:
                # 本课的页面：交给页面那一路，别当成外部链接
                self._module_pages.append((page_url, label, label, 0))
                self._page_module.setdefault(page_url, label)
                continue

            link = links.from_url(target, label, self.client.base_url)
            if link is None:
                continue
            found += self._collect_links(
                [link],
                item_ref=f"nav:{tool_id}",
                source_kind=SourceKind.NAV,
                container=label,
                label=label,
                html_url=tab.get("full_url") or tab.get("html_url"),
                # 工具本身带 updated_at —— 模块条目和大纲都没有时间字段，
                # 有的时候就别浪费
                date=mapper.parse_dt(tool.get("updated_at") or tool.get("created_at")),
            )
        self.stats["link"] = self.stats.get("link", 0) + found
        self.stats["nav_link"] = found

    def _own_page_url(self, url: str) -> str | None:
        """这个地址是不是"本课的某个页面"。是的话返回 page_url。"""
        match = re.search(
            rf"/courses/{self.course_canvas_id}/pages/([^/?#]+)", url or "", re.I
        )
        return match.group(1) if match else None

    # ---------- 来源：课程首页 ----------

    async def _from_front_page(self) -> None:
        """课程首页。

        它本身就是一个页面，pages 端点通的时候由页面那一路顺带收了；
        但 pages 被学校禁掉时列不出来 —— 而首页恰恰是教授最常放"本周要看什么"
        的地方。一个请求，补上这个洞。
        """
        if not self._has_tab("home"):
            return
        try:
            page = await self.client.get_front_page(self.course_canvas_id)
        except CanvasError:
            return  # 首页没设成页面（默认是模块或作业列表）就是这个结果，属正常
        page_url = page.get("url")
        if not page_url or page_url in self.page_body_cache:
            return
        # 交给页面那一路统一处理，别在这里重复一遍扫描逻辑
        self.fetched_pages.setdefault(page_url, page)
        self.page_body_cache.setdefault(page_url, page)
        self._module_pages.append((page_url, "Home", page.get("title") or "Home", 0))
        self._page_module.setdefault(page_url, "Home")

    # ---------- 来源：测验 ----------

    async def _from_quizzes(self) -> None:
        """测验说明里的附件和链接。

        教授常把考试范围、参考资料、往年题写在测验说明里 ——
        实测有一门课 70 个测验，**每一个都有说明正文**。
        """
        if not self._has_tab("quizzes"):
            self.stats["quiz"] = 0
            return
        try:
            quizzes = await self.client.list_quizzes(self.course_canvas_id)
        except CanvasError as exc:
            logger.info("course %s: 测验取不到 %s", self.course_canvas_id, exc)
            self.stats["quiz"] = 0
            return

        found = 0
        for quiz in quizzes:
            body = quiz.get("description") or ""
            if not body:
                continue
            title = quiz.get("title") or "Quiz"
            date = mapper.parse_dt(quiz.get("due_at") or quiz.get("unlock_at"))
            self.stats["link"] = self.stats.get("link", 0) + self._collect_links(
                links.extract(body, self.client.base_url),
                item_ref=f"quiz:{quiz.get('id')}",
                source_kind=SourceKind.QUIZ,
                container=title,
                label=title,
                html_url=quiz.get("html_url"),
                date=date,
            )
            inline_ids, link_ids = scan_body_file_ids(body)
            for file_id in inline_ids | link_ids:
                entry = self._touch(file_id)
                entry.sources.append(
                    CollectedSource(
                        source_kind=SourceKind.QUIZ,
                        source_key=f"quiz:{quiz.get('id')}:{file_id}",
                        container_name=title,
                        label=title,
                        html_url=quiz.get("html_url"),
                        source_date=date,
                        via_img=file_id in inline_ids,
                    )
                )
                found += 1
        self.stats["quiz"] = found

    # ---------- 来源：普通讨论区 ----------

    async def _from_discussions(self) -> None:
        """讨论区的主题正文（不是公告）。

        只收主题正文：回帖是学生内容，量大且不是课程资料。默认关着 ——
        实测这几门课都是 0 条，UofT 的讨论基本都搬到 Piazza / Ed 了。
        """
        if not self.include_discussions:
            if self._has_tab("discussions"):
                self.board_status[boards.DISCUSSIONS] = boards.DISABLED
            return
        if not self._has_tab("discussions"):
            return
        try:
            topics = await self.client.list_discussions(self.course_canvas_id)
        except CanvasError as exc:
            logger.info("course %s: 讨论区取不到 %s", self.course_canvas_id, exc)
            self.board_status[boards.DISCUSSIONS] = boards.UNAVAILABLE
            return

        found = 0
        for topic in topics:
            body = topic.get("message") or ""
            if not body:
                continue
            title = topic.get("title") or "Discussion"
            date = mapper.parse_dt(topic.get("posted_at") or topic.get("last_reply_at"))
            self.stats["link"] = self.stats.get("link", 0) + self._collect_links(
                links.extract(body, self.client.base_url),
                item_ref=f"discussion:{topic.get('id')}",
                source_kind=SourceKind.DISCUSSION,
                container=title,
                label=title,
                html_url=topic.get("html_url"),
                date=date,
            )
            inline_ids, link_ids = scan_body_file_ids(body)
            for file_id in inline_ids | link_ids:
                entry = self._touch(file_id)
                entry.sources.append(
                    CollectedSource(
                        source_kind=SourceKind.DISCUSSION,
                        source_key=f"discussion:{topic.get('id')}:{file_id}",
                        container_name=title,
                        label=title,
                        html_url=topic.get("html_url"),
                        source_date=date,
                        via_img=file_id in inline_ids,
                    )
                )
                found += 1
        self.stats["discussion"] = found

    def _has_tab(self, tab_id: str) -> bool:
        """导航栏里有这一项吗。拉不到导航栏时一律当"有"，宁可白试一次也不漏。"""
        if not self.tabs:
            return True
        return any(str(t.get("id")) == tab_id and not t.get("hidden") for t in self.tabs)

    # ---------- 来源：课程大纲 ----------

    SYLLABUS_CONTAINER = "Syllabus"

    async def _from_syllabus(self) -> None:
        """扫课程大纲。

        **这一路可能是整门课唯一有东西的地方。** 实测某门课 pages 端点 404、
        没有作业、没有模块，前面几路全是 0，而大纲正文有 12 万字符、
        91 个文件 + 36 个链接 —— 用户在 Canvas 上看得见，我们这里一片空白。

        大纲挂在课程对象上（`syllabus_body`），不是页面也不是作业，
        必须单独取。一门课一个请求。
        """
        try:
            body = await self.client.get_syllabus(self.course_canvas_id)
        except CanvasNotAvailable:
            self.stats["syllabus"] = 0
            return
        except CanvasError as exc:
            logger.info("course %s: 大纲取不到 %s", self.course_canvas_id, exc)
            self.stats["syllabus"] = 0
            return
        if not body:
            self.stats["syllabus"] = 0
            return

        base = self.client.base_url.rstrip("/")
        html_url = f"{base}/courses/{self.course_canvas_id}/assignments/syllabus"
        self.stats["link"] = self.stats.get("link", 0) + self._collect_links(
            links.extract(body, self.client.base_url),
            item_ref="syllabus",
            source_kind=SourceKind.SYLLABUS,
            container=self.SYLLABUS_CONTAINER,
            label=self.SYLLABUS_CONTAINER,
            html_url=html_url,
            date=None,
        )

        inline_ids, link_ids = scan_body_file_ids(body)
        for file_id in inline_ids | link_ids:
            entry = self._touch(file_id)
            entry.sources.append(
                CollectedSource(
                    source_kind=SourceKind.SYLLABUS,
                    source_key=f"syllabus:{file_id}",
                    container_name=self.SYLLABUS_CONTAINER,
                    label=self.SYLLABUS_CONTAINER,
                    html_url=html_url,
                    via_img=file_id in inline_ids,
                )
            )
        self.stats["syllabus"] = len(inline_ids | link_ids)

    def _page_url(self, page_url: str) -> str:
        """页面在 Canvas 上的固定地址。不依赖接口返回，永远拼得出来。"""
        base = self.client.base_url.rstrip("/")
        return f"{base}/courses/{self.course_canvas_id}/pages/{page_url}"

    async def _from_module_pages(self) -> None:
        """扫模块里挂着的页面正文。

        和 files 一样的套路：课程的 pages 列表常被学校关掉（403），但**单个页面
        通常仍能取到**。模块里既然列出了 page_url，就能逐个把正文拉回来扫链接。
        有的课（比如纯资讯类课程）模块里全是 Page，不扫就一个文件都找不到。
        """
        if not self._module_pages:
            self.stats["module_page"] = 0
            return

        sem = asyncio.Semaphore(_CONCURRENCY)
        found = 0
        cached = 0

        need_fetch = [
            p
            for p in self._module_pages
            if p[0] not in self.page_body_cache or p[0] in self.stale_pages
        ]
        budget = settings.max_page_fetch
        if len(need_fetch) > budget:
            self.truncated.append(
                f"模块页面有 {len(need_fetch)} 个需要抓取，只扫了前 {budget} 个"
            )
        allowed = {p[0] for p in need_fetch[:budget]}

        async def scan(page_url: str, module_name: str, title: str, sort_hint: int) -> None:
            nonlocal found, cached
            page: dict
            fresh_enough = page_url in self.page_body_cache and page_url not in self.stale_pages
            if fresh_enough:
                page = self.page_body_cache[page_url]
                cached += 1
            elif page_url in allowed:
                async with sem:
                    try:
                        page = await self.client.get_page_body(self.course_canvas_id, page_url)
                    except CanvasError:
                        return
                self.fetched_pages[page_url] = page
            else:
                return
            body = page.get("body") or ""
            date = mapper.parse_dt(page.get("updated_at"))
            page_html = page.get("html_url") or self._page_url(page_url)
            # 页面正文里的外部链接和文件同等对待。这里的 item_ref 用 page_url，
            # 和 _from_rich_text 那边的公告/作业 id 不会撞
            self.stats["link"] = self.stats.get("link", 0) + self._collect_links(
                links.extract(body, self.client.base_url),
                item_ref=f"page:{page_url}",
                source_kind=SourceKind.PAGE,
                container=module_name,
                label=f"{module_name} · {title or page.get('title') or page_url}",
                html_url=page_html,
                date=date,
                sort_hint=sort_hint,
            )
            inline_ids, link_ids = scan_body_file_ids(body)
            for file_id in inline_ids | link_ids:
                entry = self._touch(file_id)
                entry.sources.append(
                    CollectedSource(
                        source_kind=SourceKind.PAGE,
                        # 同一个页面可能被多个 module 引用，module 名必须进 key，
                        # 否则两条合法的来源会撞成同一个唯一键
                        source_key=f"modpage:{module_name}:{page_url}:{file_id}",
                        container_name=module_name,
                        item_title=title or page.get("title"),
                        label=f"{module_name} · {title or page.get('title') or page_url}",
                        # 缓存命中时 page 里只有 body，没有 Canvas 返回的 html_url ——
                        # 但页面地址本来就能拼出来，别让详情页少一个可点的链接
                        html_url=page.get("html_url") or self._page_url(page_url),
                        source_date=date,
                        sort_hint=sort_hint,
                        via_img=file_id in inline_ids,
                    )
                )
                found += 1

        await asyncio.gather(*(scan(*p) for p in self._module_pages))
        self.stats["module_page"] = found
        self.stats["page_cached"] = cached
        self.stats["page_fetched"] = len(self.fetched_pages)

    # ---------- 来源 3/4：公告、页面、作业的富文本 ----------

    def _from_rich_text(self, canvas_items: list) -> None:
        counts = {SourceKind.ANNOUNCEMENT: 0, SourceKind.PAGE: 0, SourceKind.ASSIGNMENT: 0}
        kind_map = {
            "announcement": SourceKind.ANNOUNCEMENT,
            "page": SourceKind.PAGE,
            "assignment": SourceKind.ASSIGNMENT,
        }

        # 全部扫，不设上限。解析是纯本地正则，几百条公告也就毫秒级；
        # 真正花钱的是给新文件补元数据那几个请求，那一步另有去重和并发控制。
        # 之前这里按时间截断到最近 300 条，直接后果是老公告里的文件永远扫不到。
        scannable = [i for i in canvas_items if i.kind in kind_map]
        scannable.sort(
            key=lambda i: i.posted_at or i.canvas_updated_at or datetime.min, reverse=True
        )

        for item in scannable:
            source_kind = kind_map[item.kind]
            raw = item.raw or {}
            date = item.posted_at or item.canvas_updated_at
            date_label = date.strftime("%m/%d") if date else ""

            if source_kind == SourceKind.PAGE:
                # 页面按所属模块归类；找不到模块的进"未分组"，而不是平铺成几百条
                module_name = self._page_module.get(raw.get("url") or "")
                container = module_name or UNGROUPED_PAGES
                label = f"{container} · {item.title}"
            else:
                # 不再往 label 里塞"公告"/"作业"这类词：来源类型已经由行首的
                # tag（Announcements / Assignments）表达了，重复一遍只是噪声
                container = item.title
                label = f"{date_label} · {item.title}".strip(" ·")

            # 公告的 attachments 直接带完整元数据，不用再请求
            for att in raw.get("attachments") or []:
                try:
                    file_id = int(att["id"])
                except (KeyError, ValueError, TypeError):
                    continue
                entry = self._touch(file_id, att)
                entry.sources.append(
                    CollectedSource(
                        source_kind=source_kind,
                        source_key=f"{item.canvas_id}:att:{file_id}",
                        container_name=container,
                        item_title=att.get("display_name"),
                        label=label,
                        html_url=item.html_url,
                        source_date=date,
                        canvas_item_canvas_id=item.canvas_id,
                    )
                )
                counts[source_kind] += 1

            # 正文里嵌的文件链接
            body = raw.get("message") or raw.get("body") or raw.get("description") or ""

            # 公告里的链接由「同步公告中的链接」开关决定收不收（默认关）——
            # 实测公告贡献了 74% 的链接，内容压倒性是招聘会报名、问卷、活动通知。
            # 判断在 _collect_links 里，这里只管把上下文备好
            item_html = item.html_url or (
                self._page_url(raw.get("url", ""))
                if source_kind == SourceKind.PAGE and raw.get("url")
                else None
            )
            # 页面走 page_url 做 item_ref，和 _from_module_pages 那边对齐 ——
            # 同一个页面从两条路扫到时，"链接是否被换过"才不会误判成两个条目
            item_ref = (
                f"page:{raw['url']}"
                if source_kind == SourceKind.PAGE and raw.get("url")
                else f"item:{item.canvas_id}"
            )
            self.stats["link"] = self.stats.get("link", 0) + self._collect_links(
                links.extract(body, self.client.base_url),
                item_ref=item_ref,
                source_kind=source_kind,
                container=container,
                label=label,
                html_url=item_html,
                date=date,
            )

            inline_ids, link_ids = scan_body_file_ids(body)
            for file_id in inline_ids | link_ids:
                entry = self._touch(file_id)
                entry.sources.append(
                    CollectedSource(
                        source_kind=source_kind,
                        source_key=f"{item.canvas_id}:link:{file_id}",
                        container_name=container,
                        label=label,
                        html_url=item.html_url
                        or (
                            self._page_url(raw.get("url", ""))
                            if source_kind == SourceKind.PAGE and raw.get("url")
                            else None
                        ),
                        source_date=date,
                        canvas_item_canvas_id=item.canvas_id,
                        via_img=file_id in inline_ids,
                    )
                )
                counts[source_kind] += 1

        for kind, n in counts.items():
            self.stats[kind] = n

    # ---------- 补齐元数据 ----------

    async def _fetch_missing_metadata(self) -> None:
        missing = [
            fid
            for fid, f in self.files.items()
            if f.raw is None and fid not in self.known_file_ids
        ]
        self.stats["reused"] = sum(1 for fid in self.files if fid in self.known_file_ids)
        if not missing:
            self.stats["fetched"] = 0
            return

        sem = asyncio.Semaphore(_CONCURRENCY)

        async def fetch(file_id: int) -> None:
            async with sem:
                try:
                    self.files[file_id].raw = await self.client.get_file(file_id)
                except CanvasError as exc:
                    # 文件被删了、或对学生不可见 —— 正常情况，跳过即可
                    logger.debug("file %s 取不到: %s", file_id, exc)

        await asyncio.gather(*(fetch(fid) for fid in missing))
        self.stats["fetched"] = sum(1 for fid in missing if self.files[fid].raw is not None)
        self.stats["unreachable"] = len(missing) - self.stats["fetched"]

    # ---------- 文件夹路径 ----------

    async def resolve_folder_path(self, folder_id: int | None) -> str | None:
        """Files 列表不可用时，仍可按 folder_id 单个查回路径（带缓存）。"""
        if folder_id is None:
            return None
        folder_id = int(folder_id)
        if folder_id in self._folder_cache:
            return self._folder_cache[folder_id]
        try:
            folder = await self.client.get_folder(folder_id)
            full = (folder.get("full_name") or "").strip()
            path = full.removeprefix("course files").strip("/") or None
        except CanvasError:
            path = None
        self._folder_cache[folder_id] = path
        return path
