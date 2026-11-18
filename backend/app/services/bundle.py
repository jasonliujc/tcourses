"""把一批文件打成一个 zip。

用户选中十几个文件想一次拿走，逐个点下载是不现实的。麻烦在于**本地通常没有
文件本体** —— 默认不开自动下载，库里只有元数据和一条会过期的 Canvas 链接，
所以这里得现去把它们抓回来。

三件事决定了实现方式：

- **一个文件一个请求，还是慢的那种**（Canvas 每个请求 2-8 秒），所以并发抓。
- **不能全塞内存**：一门课的录课视频动辄几百 MB。写到临时文件再打包。
- **不能因为一个文件挂掉就整包失败**：链接过期、权限变了、文件被删都很常见。
  失败的跳过，并在 zip 里放一份说明，用户至少知道少了什么、为什么。
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.integrations.canvas.client import CanvasClient, CanvasError
from app.models.file import CourseFile, Medium

logger = logging.getLogger(__name__)


async def fetch_meta(file: CourseFile, client: CanvasClient) -> dict | None:
    """向 Canvas 重新问一遍这个文件的元数据。问不到返回 None。

    这是**兜底**，不是常规路径：多发一个请求要 2-8 秒。库里那条链接先直接用，
    失败了才走到这里。单个下载和批量打包共用。

    要的不只是新链接，还有 `locked_for_user` —— 一份**锁定**的文件（作业答案
    到日子才放）在 Canvas 那边就是 `url: ""`，不看这个字段只能报成
    "取不到链接"，而用户会以为是程序坏了。
    """
    try:
        return await client.get_file(file.canvas_id)
    except CanvasError:
        return None


async def fresh_url(file: CourseFile, client: CanvasClient) -> str | None:
    """向 Canvas 换一条新的下载链接。取不到返回 None。"""
    data = await fetch_meta(file, client)
    return (data or {}).get("url") or None


# 同时抓几个。比同步的并发度高：同步那边一个"单位"是一门课、里面还要串行发
# 几十个请求，这里一个单位就是一个文件下载。
#
# 并发确实值钱：实测 6 个文件串行 97 秒、并发 24 秒 —— 因为耗时几乎全在连接建立
# （每个文件要跟 4 台主机各握一次手），并发把这些握手重叠成了一次。
#
# 但**这个具体的数字调不动**：同一组参数重复测，总耗时在 53-175 秒之间飘，
# 网络抖动比参数的影响大得多。别拿单次测量去改它。
_CONCURRENCY = 8
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: 包里那份说明的文件名。链接和下载失败合并在这一份里
NOTES_ENTRY = "说明.txt"
NOTES_ENTRY_EN = "README.txt"

# 包里那份说明是**给人读的**，所以它也得跟着界面语言走。前端把当前语言当参数
# 传进来 —— 后端没有"当前用户是谁、他选了哪种语言"这个概念，也不该有。
_NOTES_TEXT = {
    "zh": {
        "entry": NOTES_ENTRY,
        "links": "以下是外部链接，没有可下载的文件本体，地址附在后面：",
        "failures": "以下文件没能下载下来：",
    },
    "en": {
        "entry": NOTES_ENTRY_EN,
        "links": "These are external links — there is no file to download, so here are the URLs:",
        "failures": "These files could not be downloaded:",
    },
}


def notes_entry(lang: str) -> str:
    return _NOTES_TEXT.get(lang, _NOTES_TEXT["zh"])["entry"]


def _safe_entry_name(name: str, used: set[str]) -> str:
    """zip 里的条目名：去掉路径分隔符，并保证不重名。

    重名必须处理 —— 同一门课里 `Week 1/notes.pdf` 和 `Week 2/notes.pdf` 都叫
    notes.pdf，同名条目在 zip 里会互相覆盖，用户解压出来少一个文件还不知道。
    """
    cleaned = _UNSAFE.sub("_", name).strip(". ") or "untitled"
    if cleaned not in used:
        used.add(cleaned)
        return cleaned
    stem, dot, ext = cleaned.rpartition(".")
    base, suffix = (stem, f".{ext}") if dot else (cleaned, "")
    n = 2
    while f"{base} ({n}){suffix}" in used:
        n += 1
    final = f"{base} ({n}){suffix}"
    used.add(final)
    return final


def _safe_folder(folders: dict[int, str] | None, course_id: int) -> str:
    """课程目录名。课程代码里有 `/` 是真的（`STAT200H1 S LEC0101/LEC0201`），
    不洗掉的话它会在包里凭空多出一层目录。"""
    if not folders:
        return ""
    raw = folders.get(course_id)
    if not raw:
        return ""
    return _UNSAFE.sub("_", raw).strip(". ") or ""


async def _fetch_one(
    file: CourseFile, client: CanvasClient | None, dest: Path
) -> tuple[CourseFile, Path | None, str | None]:
    """把一个文件弄到 dest。返回 (文件, 路径或 None, 失败原因或 None)。"""
    # 本地已经有副本就直接用，省掉一次网络往返
    if file.local_path:
        local = settings.storage_path / file.local_path
        if local.exists():
            return file, local, None

    if file.is_deleted_on_canvas:
        return file, None, "已从 Canvas 删除"
    if client is None:
        return file, None, "Canvas 未配置，无法下载"

    limit = settings.max_download_mb * 1024 * 1024

    # **无条件先试库里那条链接**，不管它多旧。
    #
    # 换一条新链接要向 Canvas 多发一个请求，在慢链路上就是十几秒，N 个文件就是 N 次。
    # 而"链接会过期"这个前提其实不成立：我们带着 access token 请求，**Canvas 按
    # token 授权，verifier 对不对根本不影响结果**（改成乱码照样返回 200）。
    # 真取不到（文件没了、权限变了）这里会立刻失败，代价只是一次快速失败，
    # 然后走下面的刷新重试。单个文件下载走同一套。
    if file.download_url:
        try:
            await client.download_file(file.download_url, dest, limit)
            return file, dest, None
        except Exception:  # noqa: BLE001  过期/403 都走下面的刷新重试
            logger.debug("file %s 用缓存链接失败，换一条重试", file.canvas_id)

    meta = await fetch_meta(file, client)
    url = (meta or {}).get("url") or file.download_url
    if not url:
        # 锁定的文件在 Canvas 那边就是 `url: ""` —— 这不是故障，是教授还没开放，
        # 得说清楚，否则用户只会以为是下载坏了
        if meta and meta.get("locked_for_user"):
            return file, None, "Canvas 上处于锁定状态，尚未开放"
        return file, None, "取不到可用的下载链接"

    try:
        await client.download_file(url, dest, limit)
    except CanvasError as exc:
        return file, None, str(exc)
    except Exception as exc:  # noqa: BLE001  网络层什么都可能抛，不能让整包挂掉
        return file, None, f"{type(exc).__name__}: {exc}"
    return file, dest, None


@dataclass
class BundleResult:
    written: int = 0
    #: 下载失败的文件，"名字：原因"
    failures: list[str] = field(default_factory=list)
    #: 选中的外部链接。它们没有本体可下，但用户是有意选中的 —— 不能默默丢掉
    links: list[tuple[str, str]] = field(default_factory=list)


def _notes(result: BundleResult, lang: str = "zh") -> str | None:
    """包里那份说明。没什么可说的时候返回 None —— 不往包里塞空文件。

    链接和失败合成**一份**说明：用户解压之后要回答的是同一个问题
    ——"我选了 10 个，为什么只有 6 个"，分成两个文件只会让人多找一次。
    """
    text = _NOTES_TEXT.get(lang, _NOTES_TEXT["zh"])
    blocks: list[str] = []
    if result.links:
        blocks.append(
            text["links"]
            + "\n\n"
            + "\n".join(f"{name}\n  {url}" for name, url in result.links)
        )
    if result.failures:
        blocks.append(text["failures"] + "\n\n" + "\n".join(result.failures))
    return "\n\n\n".join(blocks) + "\n" if blocks else None


async def build_zip(
    files: list[CourseFile],
    zip_path: Path,
    lang: str = "zh",
    folders: dict[int, str] | None = None,
) -> BundleResult:
    """把 files 打包到 zip_path。

    `folders` 是 `{course_id: 目录名}`，只有**跨课程**打包才传（全局资源页）：
    六门课各有一份 `syllabus.pdf` 是常态，平铺在一个包里只会变成
    `syllabus.pdf`、`syllabus (2).pdf`… 用户根本认不出哪个是哪门课的。
    一门课之内不分目录 —— 那时目录名只会是同一个词，白加一层。
    """
    client: CanvasClient | None = None
    if settings.canvas_configured:
        client = CanvasClient(settings.canvas_base_url, settings.canvas_access_token)

    result = BundleResult()
    # 链接不进下载队列：它没有本体可抓，去请求那个 URL 只会拿回一堆 HTML
    links = [f for f in files if f.medium == Medium.LINK]
    result.links = [(f.display_name, f.external_url or "") for f in links]
    downloadable = [f for f in files if f.medium != Medium.LINK]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            sem = asyncio.Semaphore(_CONCURRENCY)

            async def grab(index: int, file: CourseFile):
                async with sem:
                    # 临时文件名用序号，避免同名文件在临时目录里就先撞上
                    return await _fetch_one(file, client, tmpdir / f"{index}.bin")

            results = await asyncio.gather(
                *(grab(i, f) for i, f in enumerate(downloadable)), return_exceptions=False
            )

            # 重名只在**同一个目录里**才算重名，所以每个目录各一份已用名单
            used: dict[str, set[str]] = {}
            # ZIP_STORED 而不是 DEFLATE：里面绝大多数是 PDF / 图片 / 压缩包，
            # 本来就压过了，再压一遍只是白烧 CPU
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
                for file, path, error in results:
                    if error is not None or path is None:
                        result.failures.append(f"{file.display_name}: {error}")
                        continue
                    folder = _safe_folder(folders, file.course_id)
                    name = _safe_entry_name(file.display_name, used.setdefault(folder, set()))
                    zf.write(path, f"{folder}/{name}" if folder else name)
                    result.written += 1

                # 不放说明的话用户解压出来发现少了几个，完全不知道是漏选了、
                # 下载失败了、还是那几个本来就是链接
                if (notes := _notes(result, lang)) is not None:
                    zf.writestr(notes_entry(lang), notes)
    finally:
        if client is not None:
            await client.close()

    logger.info(
        "打包完成：%s 个成功，%s 个失败，%s 个链接",
        result.written,
        len(result.failures),
        len(result.links),
    )
    return result
