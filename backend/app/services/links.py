"""外部链接：从富文本里扒出来、规范化、分类。

教授贴资料不一定上传文件 —— 录播挂在 YouTube、讲义放在 Google Docs、报名走
Microsoft Forms。这些东西对学生和 PDF 一样重要，但文件收集器完全看不见它们。

三个决定，都是先扫了真实数据（823 条富文本、4892 个 `<a href>`）才定的：

**只收页面和作业里的链接，不收公告。** 实测公告贡献了 74% 的链接，而内容
压倒性地是招聘会报名、问卷、活动通知 —— 行政噪声，不是课程资料。只收页面和
作业，写入量从 24 倍降到接近 1 倍，丢掉的全是学生不会去找的东西。

**噪声过滤必须有。** 4892 个链接里 403 个是 mailto/锚点/js，1251 个指回 Canvas
自己（那些页面和文件本来就从别的路径收进来了）。这两项就砍掉 34%。

**分三类就够。** 按域名细分的话 350 个不同域名、前 10 个只覆盖 54%，长尾极长，
细分类没有价值。Video / Docs 覆盖真正会被反复打开的那两类，其余一律 Others。

不做的事：**不检测链接是否还能打开。** 把教授贴出来的最新链接给用户就够了，
打不开是教授那边的事 —— 而探活要为每个链接发一次请求，代价和收益完全不成比例。
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 富文本里的超链接。要连锚文本一起拿到 —— 链接的标题靠它，
# "锚文本没变但 URL 变了"这种"链接被换掉"的判断也靠它。
_A_TAG_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 只影响追踪、不改变"指向哪个资源"的查询参数，规范化时剥掉。
# 保守起见只列公认的追踪参数：拿不准就不剥 —— 剥错了会把两个不同的资源合成一个。
_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "ref_src",
}

# 只有视频站的 fragment 是装饰性的（#t=120 是播放位置，不是另一个视频）。
# 别的站不能剥：`#/path` 可能是真实路由，Google Sheets 的 `#gid=` 是另一张表。
_DECORATIVE_FRAGMENT_HOSTS = {"www.youtube.com", "youtube.com", "youtu.be", "vimeo.com"}

# 指回 Canvas 自己的链接不收：那些页面、作业、文件本来就由别的路径收进来了，
# 收进来只会让同一个东西在界面上出现两次。
_CANVAS_HOST_MARKERS = ("instructure.com", "canvaslms.com", "canvas-user-content.com")

# 链接类型。命名和文件类型一样用英文；顺序即匹配顺序。
LINK_KINDS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "link_video",
        "Video",
        (
            "youtube.com",
            "youtu.be",
            "vimeo.com",
            "echo360",
            "panopto",
            "kaltura",
            "microsoftstream.com",
            "stream.microsoft.com",
            "teams.microsoft.com",
            "zoom.us",
            "play.library",  # 大学自建的流媒体（UofT MyMedia 就是这个）
            "mymedia",
            "twitch.tv",
            "bilibili.com",
        ),
    ),
    (
        "link_docs",
        "Docs",
        (
            "docs.google.com",
            "drive.google.com",
            "sheets.google.com",
            "slides.google.com",
            "onedrive.live.com",
            "1drv.ms",
            "sharepoint.com",
            "overleaf.com",
            "notion.so",
            "notion.site",
            "dropbox.com",
            "box.com",
            "etherpad",
            "hackmd.io",
        ),
    ),
]

#: 认不出就是 Others。**94% 的链接会落在这里**，这不是兜底失败，
#: 而是真实分布就是这样：350 个域名的长尾，细分下去没有意义。
LINK_OTHER = ("link_other", "Others")

_KIND_LABELS = {key: label for key, label, _ in LINK_KINDS}
_KIND_LABELS[LINK_OTHER[0]] = LINK_OTHER[1]


#: SharePoint / OneDrive 把资源类型编在路径里：`/:v:/` 是视频、`/:w:/` 是 Word…
#: 光看域名的话一门课的录播会全被归成 Docs —— 而录播恰恰是学生最常回去点的东西。
_SHAREPOINT_HOSTS = ("sharepoint.com", "onedrive.live.com", "1drv.ms")
_SHAREPOINT_VIDEO_RE = re.compile(r"/:v:/", re.I)


def link_kind(url: str | None) -> tuple[str, str]:
    """(类型 key, 展示名)。认不出就是 Others —— 永远不会返回空。"""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    if not host:
        return LINK_OTHER
    if any(h in host for h in _SHAREPOINT_HOSTS) and _SHAREPOINT_VIDEO_RE.search(parts.path):
        return LINK_KINDS[0][0], LINK_KINDS[0][1]  # Video
    for key, label, markers in LINK_KINDS:
        if any(m in host for m in markers):
            return key, label
    return LINK_OTHER


def kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind, LINK_OTHER[1])


#: YouTube 的分享短链带的追踪参数。只在 YouTube 上剥 —— `si` 在别的站可能有意义
_YOUTUBE_HOSTS = {"youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com"}


def _canonical_youtube(
    host: str, path: str, params: list[tuple[str, str]]
) -> tuple[str, str, list[tuple[str, str]]]:
    """把 YouTube 的几种写法折成一种。

    实测同一个讲课录像在库里存了两行：教授在一处贴了 `youtu.be/X?si=…`，
    另一处贴了 `youtube.com/watch?v=X`。这两个确定是同一个视频，而录播又是
    学生最常回去点的东西 —— 分成两行等于让人自己去认。

    只对 YouTube 做这种改写：它的短链格式是公开且明确的，别的站没这个保证。
    """
    if host not in _YOUTUBE_HOSTS:
        return host, path, params
    params = [(k, v) for k, v in params if k.lower() != "si"]
    if host == "youtu.be" and path not in ("", "/"):
        # youtu.be/<id> ≡ youtube.com/watch?v=<id>
        video_id = path.lstrip("/").split("/", 1)[0]
        return "www.youtube.com", "/watch", [("v", video_id), *params]
    return ("www.youtube.com" if host in ("youtube.com", "m.youtube.com") else host), path, params


def normalize(raw: str) -> str | None:
    """把一个 URL 压成身份标识用的形式。不是 http(s) 就返回 None。

    **保守优先**：合并错了是永久把两个不同的资源当成一个，比多留一条重复记录
    严重得多。所以只做几件确定安全的事：统一大小写和 scheme、去掉尾斜杠、
    剥掉公认的追踪参数、只对视频站剥 fragment。
    """
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    host = parts.netloc.lower()
    if not host:
        return None
    host = re.sub(r":(?:80|443)$", "", host)
    path = re.sub(r"/+$", "", parts.path) or "/"
    params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    host, path, params = _canonical_youtube(host, path, params)
    query = urlencode(params)
    fragment = "" if host in _DECORATIVE_FRAGMENT_HOSTS else parts.fragment
    # scheme 统一成 https：同一个资源的 http/https 两种写法是同一个东西
    return urlunsplit(("https", host, path, query, fragment))


def is_canvas_own(normalized: str, canvas_base_url: str = "") -> bool:
    """指回 Canvas 自己的链接。这些资源已经由文件/页面/作业那几路收进来了。"""
    host = urlsplit(normalized).netloc.lower()
    if any(marker in host for marker in _CANVAS_HOST_MARKERS):
        return True
    own = urlsplit(canvas_base_url or "").netloc.lower()
    return bool(own) and host == own


def resource_key(normalized: str) -> str:
    """链接在一门课里的稳定身份。

    用哈希而不是 URL 原文：URL 能长到两千字符，而这一列还要建唯一索引。
    前缀留着 `link:`，一眼看得出这行是什么，也和文件的 `file:<id>` 对齐。
    """
    return "link:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:40]


def file_resource_key(canvas_file_id: int) -> str:
    """文件在一门课里的稳定身份。

    以前身份就是 `course_files.canvas_id`，而且是**全局唯一**的 —— 同一个
    Canvas 文件被两门课引用时，两门课会抢同一行，文件在界面上"跳课"。
    改成按 (课程, resource_key) 唯一之后这个问题不存在了，后续做全局文件管理时
    也才有一个跨文件和链接统一的身份可用。
    """
    return f"file:{canvas_file_id}"


def clean_text(fragment: str) -> str:
    """锚文本：去标签、解 HTML 实体、合并空白。

    实体必须解 —— Canvas 的富文本里 `&amp;` `&nbsp;` 遍地都是，不解的话标题
    直接显示成「Arts &amp; Science website」。`&nbsp;` 解出来是不换行空格，
    再交给空白合并顺手清掉。
    """
    text = html.unescape(_TAG_RE.sub(" ", fragment)).replace("\u00a0", " ")
    return _WS_RE.sub(" ", text).strip()


_URLISH_RE = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I)


def pretty_url(url: str) -> str:
    """把一条 URL 变成能当标题读的样子：去掉 scheme、www.、尾斜杠。

    浏览器地址栏就是这么显示的。`https://example.zoom.us/j/8223655` 里
    前面那 8 个字符对读者没有任何信息量，而后面的会议号才是区分它和另外
    五条 zoom 链接的唯一依据 —— 所以不能截断尾巴，只能砍掉头。
    """
    parts = urlsplit(url.strip())
    host = parts.netloc.removeprefix("www.")
    rest = parts.path.rstrip("/")
    if parts.query:
        rest += f"?{parts.query}"
    if parts.fragment:
        rest += f"#{parts.fragment}"
    return f"{host}{rest}" or url.strip()


def title_for(anchor_text: str, normalized: str) -> str:
    """链接显示成什么。

    教授**经常不给链接写文字**，直接把地址粘进正文，Canvas 自动把它变成
    `<a href="X">X</a>` —— 实测 817 条链接里有 206 条（25%）是这样。这时候
    锚文本就是那条 URL 本身，原样显示的话满屏都是 `https://`。

    没有文字可用时，地址本身就是唯一的信息，所以不编标题，只把它排版干净。
    """
    text = anchor_text.strip()
    if not text or _URLISH_RE.match(text):
        return pretty_url(text or normalized)
    # 富文本里 URL 常被标签切开（`https://<span>markus</span>.teach…`），
    # 去掉标签之后就成了带空格的一串。把空白全抹掉再和目标比一次，
    # 一样就说明这段"文字"其实还是那条地址
    if _WS_RE.sub("", text).rstrip("/") == _WS_RE.sub("", normalized).rstrip("/"):
        return pretty_url(normalized)
    return text


@dataclass(frozen=True)
class ExtractedLink:
    url: str  # 原样的 URL —— 用户点的是这个
    normalized: str  # 身份
    resource_key: str
    title: str  # 锚文本，同时也是"链接被换掉"的比对依据


def from_url(raw: str, title: str, canvas_base_url: str = "") -> ExtractedLink | None:
    """把一个**裸 URL**（不是从 HTML 里扒的）过一遍同样的规范化和噪声过滤。

    模块里的 `ExternalUrl` 条目就是这个形状：教授直接把 Gradescope、Piazza、
    YouTube 播放列表挂成一个模块条目，正文里根本没有 `<a>` 标签。
    """
    normalized = normalize(raw or "")
    if normalized is None or is_canvas_own(normalized, canvas_base_url):
        return None
    return ExtractedLink(
        url=html.unescape((raw or "").strip()),
        normalized=normalized,
        resource_key=resource_key(normalized),
        title=title_for(clean_text(title), normalized)[:400],
    )


def extract(body: str, canvas_base_url: str = "") -> list[ExtractedLink]:
    """从一段富文本里扒出所有值得收的外部链接，按出现顺序、URL 去重。"""
    out: list[ExtractedLink] = []
    seen: set[str] = set()
    for attrs, inner in _A_TAG_RE.findall(body or ""):
        match = _HREF_RE.search(attrs)
        if not match:
            continue
        # href 里的实体也要解：Canvas 存的是 HTML，`?v=X&amp;list=Y` 这种写法
        # 遍地都是，不解的话拿到手的是一个打不开的 URL
        raw_href = html.unescape(match.group(1).strip())
        normalized = normalize(raw_href)
        if normalized is None or is_canvas_own(normalized, canvas_base_url):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        title = title_for(clean_text(inner), normalized)
        out.append(
            ExtractedLink(
                url=raw_href,
                normalized=normalized,
                resource_key=resource_key(normalized),
                title=title[:400],
            )
        )
    return out
