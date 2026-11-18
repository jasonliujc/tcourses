"""文件库 + 分组整理 API（第一阶段重点功能 1）。"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.api.errors import api_error
from app.api.filters import (
    UNIDENTIFIED,
    FileFilters,
    apply_file_filters,
    file_filters,
    matching_file_ids,
)
from app.core.config import settings
from app.db.session import get_db
from app.integrations.canvas.client import CanvasClient
from app.models.course import Course
from app.models.file import (
    CourseFile,
    FileGroup,
    FileGroupMember,
    FileSource,
    Medium,
    SourceKind,
)
from app.models.userdata import UserFile, UserGroup, UserGroupMember
from app.schemas.files import (
    FileOut,
    GroupMemberRequest,
    GroupOut,
    MatchHit,
    OrganizeResult,
    SourceNode,
    StarRequest,
    TagRef,
    UserActionResult,
    UserGroupCreate,
    UserGroupOut,
    UserGroupRef,
    UserGroupUpdate,
)
from app.services import boards, bundle, search, userdata
from app.services.file_collector import UNGROUPED_PAGES
from app.services.file_organizer import organize_course_files
from app.services.file_summary import UNKNOWN_NODE, canvas_file_url, summarize
from app.services.userdata import STARRED_GROUP_ID

router = APIRouter(tags=["files"])

_SOURCE_LABELS = {
    SourceKind.FILES: "Canvas Files",
    SourceKind.MODULE: "Canvas Modules",
    SourceKind.ANNOUNCEMENT: "Canvas Announcements",
    SourceKind.PAGE: "Canvas Pages",
    SourceKind.ASSIGNMENT: "Canvas Assignments",
    SourceKind.SYLLABUS: "Canvas Syllabus",
    SourceKind.QUIZ: "Canvas Quizzes",
    SourceKind.NAV: "Canvas Nav",
}

# 一次打包的上限。每个文件都要向 Canvas 发一次请求，几百个会让这个接口
# 挂在那里好几分钟，前端也没法给出有意义的进度
_BUNDLE_LIMIT = 200


def to_file_out(
    file: CourseFile,
    group_ids: list[int],
    course_canvas_id: int | None = None,
    marks: dict | None = None,
    hit: search.Match | None = None,
) -> FileOut:
    """带上恒定显示的四要素。sources 必须已经预加载，否则 async 下懒加载会炸。

    `marks` 是用户的整理结果（星标 / 所属分组）。它和四要素一样跟着文件走 ——
    用户在任何视图都要能一眼看出"这个我标过"，而不是切到「我的分组」才知道。
    """
    item = FileOut.model_validate(file)
    item.group_ids = group_ids
    if marks:
        item.is_starred = marks["starred"]
        item.user_groups = [UserGroupRef(id=gid, name=name) for gid, name in marks["groups"]]
    # 链接没有 Canvas 文件地址，它的"点开去哪儿"就是 external_url 本身
    if (
        file.medium != Medium.LINK
        and course_canvas_id is not None
        and settings.canvas_base_url
    ):
        item.canvas_url = canvas_file_url(
            settings.canvas_base_url, course_canvas_id, file.canvas_id
        )
    summary = summarize(file, list(file.sources))
    # 显示的日期就是排序用的那个，两者永远一致
    item.source_date = file.display_date or summary.source_date
    item.tags = [
        TagRef(
            id=node,
            # 一条来源都没有的兜底节点不在板块表里，得自己给个说法
            label="Unknown" if node == UNKNOWN_NODE else boards.tree_label_of(node),
        )
        for node in summary.tags
    ]
    item.source_label = summary.source_label
    item.medium = summary.medium
    item.kind = summary.kind
    item.kind_label = summary.kind_label
    item.html_url = summary.html_url
    if hit is not None:
        item.match_score = hit.score
        # 一行上放不下更多，而"为什么这条在结果里"有一条就说清了
        item.match_hits = [MatchHit(field=h.field, text=h.text) for h in hit.hits[:2]]
    return item


def _now_in(tz: str) -> datetime:
    """当前时刻在指定时区下的样子。时区名不认识就退回 UTC ——
    一个打不开的下载比一个时间戳偏了几小时的下载严重得多。"""
    try:
        return datetime.now(ZoneInfo(tz))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(UTC)


def _attachment(name: str) -> str:
    """Content-Disposition。中文文件名必须走 RFC 5987 的 filename*，
    只给裸 filename= 的话浏览器存下来是一串乱码。"""
    return f"attachment; filename*=UTF-8''{quote(name)}"


def _to_group_out(group: FileGroup, file_count: int) -> GroupOut:
    out = GroupOut.model_validate(group)
    out.file_count = file_count
    out.dimension = group.rule_key.split(":", 1)[0] if group.rule_key else "manual"
    return out


# ---------- 文件 ----------


@router.get("/courses/{course_id}/files", response_model=list[FileOut])
async def list_course_files(
    course_id: int,
    group_id: int | None = Query(None, description="只看某个分组里的文件"),
    source_kind: str | None = Query(
        None,
        description="按侧边栏节点筛选：home / syllabus / announcements / files / "
        "assignments / quizzes / modules_pages / discussions / others",
    ),
    source_key: str | None = Query(
        None, description="来源下的容器：文件夹路径（files）或 module 名"
    ),
    ungrouped: bool = Query(False, description="只看没有任何分组的文件"),
    user_group_id: int | None = Query(
        None, description=f"用户分组；{STARRED_GROUP_ID} 表示星标"
    ),
    q: str | None = Query(
        None,
        description="搜索。容错匹配（大小写、标点、驼峰、数字前导零、拼错一个字母），"
        '范围含文件名、文件夹路径、模块名、页面/公告/作业标题、链接地址。'
        '支持 `"词组"`、`-排除`、`folder:` / `page:` / `module:` / `name:` 限定',
    ),
    sort_by: str = Query("date", description="date / name / relevance（带 q 时才有意义）"),
    order: str = Query("desc", description="asc / desc"),
    filters: FileFilters = Depends(file_filters),
    db: AsyncSession = Depends(get_db),
) -> list[FileOut]:
    # 看用户自己的分组时要连"已从 Canvas 删除"的一起列出来：用户整理过的东西
    # 不能因为 Canvas 那边没了就从他的分组里凭空消失，只是打个标记。
    in_user_view = user_group_id is not None
    stmt = select(CourseFile).where(CourseFile.course_id == course_id)
    if not in_user_view:
        stmt = stmt.where(CourseFile.is_deleted_on_canvas.is_(False))
    if group_id is not None:
        stmt = stmt.join(FileGroupMember, FileGroupMember.file_id == CourseFile.id).where(
            FileGroupMember.group_id == group_id
        )
    if in_user_view:
        uf = aliased(UserFile)
        stmt = stmt.join(uf, uf.file_id == CourseFile.id)
        if user_group_id == STARRED_GROUP_ID:
            stmt = stmt.where(uf.is_starred.is_(True))
        else:
            ugm = aliased(UserGroupMember)
            stmt = stmt.join(ugm, ugm.user_file_id == uf.id).where(
                ugm.group_id == user_group_id
            )
    if source_kind:
        # source_kind 传的是**板块 id**（files / modules / pages / …），
        # 一个板块可能对应多种 source_kind（Others 就包含导航栏工具）
        kinds = boards.source_kinds_for_node(source_kind)
        src = aliased(FileSource)
        stmt = stmt.join(src, src.file_id == CourseFile.id)
        stmt = stmt.where(src.source_kind.in_(kinds or [source_kind]))
        if source_key is not None:
            # "认不出归属"这个节点背后其实是两个容器名，得一起匹配，
            # 否则点进去只看得到一半
            if source_key == UNGROUPED_PAGES:
                stmt = stmt.where(src.container_name.in_(UNIDENTIFIED))
            else:
                stmt = stmt.where(src.container_name == source_key)
    stmt = apply_file_filters(stmt, filters, course_id)
    # 搜索在 Python 里做（见 services/search.py）：要抹平标点/大小写/驼峰、
    # 还要能落在模块名和页面标题上，SQL 的 LIKE 一样都办不到
    terms = search.parse_query(q)

    # 必须按 display_date 排：那才是界面上显示的日期。
    # 按 canvas_updated_at 排会让"看到的日期"和"排列顺序"对不上。
    column = CourseFile.display_name if sort_by == "name" else CourseFile.display_date
    direction = column.asc() if order == "asc" else column.desc()
    # 日期可能为空，统一排到末尾，免得空值挤在最前面
    stmt = stmt.order_by(direction.nullslast(), CourseFile.display_name).distinct()
    # sources 必须预加载：Pydantic 的 from_attributes 会去读这个 relationship，
    # 而 async session 里的懒加载会直接抛 MissingGreenlet
    stmt = stmt.options(selectinload(CourseFile.sources))

    files = list((await db.execute(stmt)).scalars().all())
    hits: dict[int, search.Match] = {}
    if terms:
        matched = []
        for f in files:
            m = search.match(search.fields_of(f, f.sources), terms)
            if m is None:
                continue
            hits[f.id] = m
            matched.append(f)
        files = matched
        if sort_by == "relevance":
            # 稳定排序：分数一样的仍按上面 SQL 定的顺序（日期新的在前），
            # 不然同分的一堆资料每次请求都可能换个次序
            files.sort(key=lambda f: -hits[f.id].score)
    file_ids = [f.id for f in files] or [0]

    members = (
        await db.execute(
            select(FileGroupMember.file_id, FileGroupMember.group_id).where(
                FileGroupMember.file_id.in_(file_ids)
            )
        )
    ).all()
    groups_by_file: dict[int, list[int]] = {}
    for file_id, gid in members:
        groups_by_file.setdefault(file_id, []).append(gid)

    course_canvas_id = (
        await db.execute(select(Course.canvas_id).where(Course.id == course_id))
    ).scalar_one_or_none()
    # 一次查完所有文件的星标/分组，别在循环里逐行查
    marks = await userdata.annotations_for(db, file_ids)

    out: list[FileOut] = []
    for f in files:
        gids = groups_by_file.get(f.id, [])
        if ungrouped and gids:
            continue
        out.append(to_file_out(f, gids, course_canvas_id, marks.get(f.id), hits.get(f.id)))
    return out


#: 展开子节点的节点。其余的"容器"就是每一条公告/作业/测验本身，
#: 几百个节点当不了导航，所以平铺
_EXPANDABLE_NODES = {boards.FILES, boards.MODULES_PAGES}

@router.get("/courses/{course_id}/sources", response_model=list[SourceNode])
async def list_source_tree(
    course_id: int,
    expand: str = Query(
        "default",
        description="default = 只展开 Canvas Files 和 Modules & Pages（其余板块的容器"
        "就是一条条公告/作业，几百个节点当不了导航）；all = 每个板块都展开，筛选面板用",
    ),
    filters: FileFilters = Depends(file_filters),
    db: AsyncSession = Depends(get_db),
) -> list[SourceNode]:
    """来源导航树，**按板块组织** —— 和设置页里那份板块列表是同一套词。

    以前这里自成一套（"Canvas Modules & Pages"、"Canvas 导航栏"），和设置页
    对不上，用户得自己在两套说法之间换算。现在两边都从 `services/boards.py`
    取名字，只有一份定义。

    计数按文件去重：一个 PDF 同时是 module 的 File 条目、又被同模块的页面引用过
    是常态，直接相加会把它算两次。
    """
    # 筛选生效时这棵树只统计命中的资源：数字和点进去看到的东西必须是一回事，
    # 否则"Lectures 28"点进去是"没有资源符合筛选条件"，那个 28 就是个假数字。
    # 一个节点被筛空了就整个不出现 —— 空筐不是导航
    stmt = select(FileSource.source_kind, FileSource.container_name, FileSource.file_id).where(
        FileSource.course_id == course_id
    )
    if filters.active:
        stmt = stmt.where(FileSource.file_id.in_(matching_file_ids(filters, course_id)))
    pairs = (await db.execute(stmt)).all()
    if not pairs:
        return []

    # 节点 -> 容器 -> 文件集合。Modules 和 Pages 在树上是同一个节点，
    # 所以两边的同名容器会自然并成一个筐（见 boards.MODULES_PAGES 的说明）
    grouped: dict[str, dict[str, set[int]]] = {}
    totals: dict[str, set[int]] = {}
    for kind, container, file_id in pairs:
        node = boards.tree_node_of(boards.board_of(kind))
        name = container or "Uncategorized"
        if name in UNIDENTIFIED:
            name = UNGROUPED_PAGES
        grouped.setdefault(node, {}).setdefault(name, set()).add(file_id)
        totals.setdefault(node, set()).add(file_id)

    out: list[SourceNode] = []
    for node in boards.TREE_ORDER:
        if node not in totals:
            continue
        kids: list[SourceNode] = []
        # 侧边栏只展开那两个板块：其余板块的"容器"就是一条条公告、一份份作业，
        # 几百个平铺节点当不了导航。但**筛选面板要全部**：那边是勾选不是导航，
        # 而且实测每门课这些容器加起来最多三十来个（公告标题、工具名、Syllabus）
        if node in _EXPANDABLE_NODES or expand == "all":
            kids = [
                SourceNode(kind=node, key=name, name=name, file_count=len(ids))
                for name, ids in grouped[node].items()
            ]
            # 认不出归属的排最后，别混在模块中间
            kids.sort(key=lambda n: (n.name == UNGROUPED_PAGES, n.name.lower()))
        out.append(
            SourceNode(
                kind=node,
                key="",
                name=boards.tree_label_of(node),
                file_count=len(totals[node]),
                children=kids,
            )
        )
    return out


async def _open_stream(client: CanvasClient, url: str):
    """开一个流式 GET，成功返回 (上下文, 响应)，失败返回 (None, None)。

    只判断"能不能开始"——状态码拿到就够了，正文还没开始收。失败在这里是**预期**
    的（链接过期是常态），所以不抛，交给调用方换一条重试。
    """
    ctx = client.stream(url)
    try:
        resp = await ctx.__aenter__()
        resp.raise_for_status()
        return ctx, resp
    except Exception:  # noqa: BLE001  过期/403/网络抖动都退回去重试
        await ctx.__aexit__(None, None, None)
        return None, None


async def _relay(client: CanvasClient, ctx, resp):
    """把 Canvas 的字节边收边转给浏览器，收完把连接都关掉。"""
    try:
        async for chunk in resp.aiter_bytes(chunk_size=65536):
            yield chunk
    finally:
        await ctx.__aexit__(None, None, None)
        await client.close()


@router.get("/files/{file_id}/download")
async def download_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """有本地副本就直接给，没有就从 Canvas 边收边转发。

    两个决定：

    **直接用库里那条链接，不看它多旧。** Canvas 的链接带 verifier 会过期，但换一条
    要多花 2-8 秒，而实测 37 小时前取的链接照样返回 200 —— 按 TTL 保守判断等于给
    绝大多数下载白加一次往返。真过期了就是一次快速失败，兜底逻辑接住：换一条新的
    重试，顺手存下来（下次就不用换了），还不行才报错。

    **转发而不是 302 重定向。** 重定向会让浏览器跳走，用户要么看到新标签页要么撞上
    Canvas 的报错页，前端既没法转圈也没法提示失败。转发之后下载是一个普通的
    fetch，成功失败都在我们手里。
    """
    file = await db.get(CourseFile, file_id)
    if file is None:
        raise api_error(404, "file_not_found")

    if file.medium == Medium.LINK:
        # 外部链接没有本体可下。界面上这一行本来就给的是「打开」按钮，
        # 走到这里说明是直接打接口的，给个说得清的错误而不是去抓那个 URL
        raise api_error(400, "file_is_link", name=file.display_name)

    if file.local_path:
        path = Path(settings.storage_path) / file.local_path
        if path.exists():
            return FileResponse(path, filename=file.display_name, media_type=file.content_type)

    if file.is_deleted_on_canvas:
        raise api_error(410, "file_deleted", name=file.display_name)
    if not settings.canvas_configured:
        raise api_error(503, "canvas_not_configured")

    client = CanvasClient(settings.canvas_base_url, settings.canvas_access_token)
    ctx = resp = None
    meta: dict | None = None
    try:
        if file.download_url:
            ctx, resp = await _open_stream(client, file.download_url)
        if resp is None:
            meta = await bundle.fetch_meta(file, client)
            if url := (meta or {}).get("url"):
                file.download_url = url
                # 换链接时顺带拿到了完整元数据，算一次核对
                file.metadata_fetched_at = datetime.now(UTC)
                await db.commit()
                ctx, resp = await _open_stream(client, url)
    except Exception:
        await client.close()
        raise

    if resp is None:
        await client.close()
        # 锁定的文件在 Canvas 那边就是 `url: ""`（作业答案到日子才放）。
        # 报成"下载失败"会让用户以为是程序坏了，而这件事他自己解决不了也不用解决
        if meta and meta.get("locked_for_user"):
            raise api_error(423, "file_locked", name=file.display_name)
        raise api_error(502, "download_failed", name=file.display_name)

    headers = {"Content-Disposition": _attachment(file.display_name)}
    # 上游给了长度就透传，浏览器的下载进度条要靠它。但**上游压缩过就不能透传** ——
    # httpx 会替我们解压，转发出去的字节数比这个数字大，浏览器会按长度截断
    if (length := resp.headers.get("content-length")) and not resp.headers.get(
        "content-encoding"
    ):
        headers["Content-Length"] = length
    return StreamingResponse(
        _relay(client, ctx, resp),
        media_type=file.content_type or "application/octet-stream",
        headers=headers,
    )


@router.post("/courses/{course_id}/files/bundle")
async def download_bundle(
    course_id: int,
    payload: GroupMemberRequest,
    background: BackgroundTasks,
    # 包里那份说明是给人读的，所以跟着界面语言走。界面知道自己是哪种语言，
    # 后端不知道 —— 让它当参数传进来，比在后端猜一个"当前用户"可靠
    lang: str = Query("zh", description="包内说明的语言：zh / en"),
    # 包名里的时间戳也按用户看到的时区打。默认 UTC 而不是服务器本地时间：
    # 服务器在哪个时区是个和用户无关的事实，拿它命名只会让人对不上表
    tz: str = Query("UTC", description="包名里时间戳用的时区，例如 America/Toronto"),
    db: AsyncSession = Depends(get_db),
):
    """把选中的一批文件打成 zip 下载。

    本地通常没有文件本体（默认不开自动下载），所以要现去 Canvas 抓 ——
    每个文件一次请求、每次 2-8 秒，所以并发抓，并且**单个失败不影响整包**，
    失败清单会放进 zip 里的一个说明文件。

    用 POST 而不是 GET：选中的 id 可能有上百个，塞进 query string 会超长。
    """
    files = await _course_files(db, course_id, payload.file_ids)
    # 时间戳 + 课程 id：同一门课打好几次包时，文件名天然不重复，下载目录里
    # 也不会变成 course (1).zip / course (2).zip 这种认不出来的东西
    return await bundle_response(files, background, lang, tz, suffix=f"-{course_id}")


async def bundle_response(
    files: list[CourseFile],
    background: BackgroundTasks,
    lang: str,
    tz: str,
    *,
    suffix: str = "",
    folders: dict[int, str] | None = None,
):
    """打包 + 拼响应。**课程页和全局资源页共用这一段** ——
    包怎么打、失败怎么报、包名怎么起，两个入口必须完全一致。

    `folders` 只有跨课程打包才传：同名文件在不同课里是常态，平铺会互相覆盖。
    """
    if not files:
        raise api_error(400, "no_files_selected")
    if len(files) > _BUNDLE_LIMIT:
        raise api_error(400, "bundle_limit", limit=_BUNDLE_LIMIT)

    # 打包过程要几十秒，写到临时文件而不是内存 —— 一门课的录课视频动辄几百 MB
    handle, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(handle)
    zip_path = Path(tmp_name)
    try:
        result = await bundle.build_zip(files, zip_path, lang, folders=folders)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    if result.written == 0:
        zip_path.unlink(missing_ok=True)
        # 全选中的都是链接是个很正常的误操作，别报成"下载失败"
        if not result.failures:
            raise api_error(400, "bundle_all_links", n=len(result.links))
        raise api_error(502, "bundle_all_failed", detail="; ".join(result.failures[:3]))

    # 响应发完再删临时文件
    background.add_task(zip_path.unlink, missing_ok=True)
    name = f"{_now_in(tz).strftime('%Y%m%d-%H%M%S')}{suffix}.zip"
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=name,
        # 有东西没进包的时候前端要弹提示。清单在包里的说明文件里，这里只带回
        # 数量 —— header 只能放 ASCII，文件名塞不进来。链接和失败分开报：
        # "3 个链接不能下载"和"3 个文件下载失败"是两件完全不同的事
        headers={
            "X-Bundle-Written": str(result.written),
            "X-Bundle-Failed": str(len(result.failures)),
            "X-Bundle-Links": str(len(result.links)),
        },
        background=background,
    )


@router.post("/files/{file_id}/lock", response_model=FileOut)
async def set_file_lock(
    file_id: int, locked: bool = True, db: AsyncSession = Depends(get_db)
) -> FileOut:
    """锁定后自动分组管道不再改动这个文件的归属 —— 用户整理的结果永远优先。"""
    # 带上 sources：序列化时会读这个 relationship，懒加载在 async 里会炸
    file = (
        await db.execute(
            select(CourseFile)
            .where(CourseFile.id == file_id)
            .options(selectinload(CourseFile.sources))
        )
    ).scalar_one_or_none()
    if file is None:
        raise api_error(404, "file_not_found")
    file.organize_locked = locked
    await db.commit()
    return to_file_out(file, [])


# ---------- 分组 ----------


@router.get("/courses/{course_id}/groups", response_model=list[GroupOut])
async def list_groups(
    course_id: int,
    dimension: str | None = Query(
        None, description="folder / week / lecture / chapter / category / type / manual"
    ),
    filters: FileFilters = Depends(file_filters),
    db: AsyncSession = Depends(get_db),
) -> list[GroupOut]:
    groups = (
        (
            await db.execute(
                select(FileGroup)
                .where(FileGroup.course_id == course_id)
                .order_by(FileGroup.sort_order, FileGroup.name)
            )
        )
        .scalars()
        .all()
    )
    count_stmt = (
        select(FileGroupMember.group_id, func.count())
        .join(FileGroup, FileGroup.id == FileGroupMember.group_id)
        .where(FileGroup.course_id == course_id)
        .group_by(FileGroupMember.group_id)
    )
    if filters.active:
        count_stmt = count_stmt.where(
            FileGroupMember.file_id.in_(matching_file_ids(filters, course_id))
        )
    counts = {gid: n for gid, n in (await db.execute(count_stmt)).all()}

    out = [_to_group_out(g, counts.get(g.id, 0)) for g in groups]
    if dimension:
        out = [g for g in out if g.dimension == dimension]
    # 被筛空的自动分组直接不返回：这些筐本身就是"一堆文件"的意思，空筐没有意义。
    # 用户自己建的分组不一样，那边即使被筛空也照常显示（见 list_user_groups）
    return [g for g in out if g.file_count > 0]


# ---------- 用户分组与星标 ----------
#
# 和上面的自动分组是两套东西，别混：自动分组每轮同步推倒重建，
# 这里是用户自己整理的结果，同步只允许对齐、不允许重建。


async def _course_files(db: AsyncSession, course_id: int, file_ids: list[int]) -> list[CourseFile]:
    """把请求里的 file_id 换成本课程的文件对象，顺便挡掉跨课程的 id。"""
    if not file_ids:
        return []
    files = (
        (
            await db.execute(
                select(CourseFile).where(
                    CourseFile.id.in_(file_ids), CourseFile.course_id == course_id
                )
            )
        )
        .scalars()
        .all()
    )
    missing = set(file_ids) - {f.id for f in files}
    if missing:
        raise api_error(400, "files_not_in_course")
    return list(files)


@router.get("/courses/{course_id}/user-groups", response_model=list[UserGroupOut])
async def list_user_groups(
    course_id: int,
    filters: FileFilters = Depends(file_filters),
    db: AsyncSession = Depends(get_db),
) -> list[UserGroupOut]:
    """用户分组列表。星标永远排第一，且不算在用户建的分组里。

    有筛选生效时计数只数命中的资源，但**分组本身照常列出来**，哪怕被筛成了 0。
    自动分组被筛空了就不显示（那种筐本身就等于"一堆文件"），用户自己建的组不一样 ——
    那是他建的东西，因为一次临时筛选就从边栏上消失，只会让人以为组被删了。
    """
    groups = (
        (
            await db.execute(
                select(UserGroup)
                .where(UserGroup.course_id == course_id)
                .order_by(UserGroup.sort_order, UserGroup.name)
            )
        )
        .scalars()
        .all()
    )
    counts, starred = await userdata.group_counts(
        db, course_id, matching_file_ids(filters, course_id) if filters.active else None
    )
    out = [
        UserGroupOut(
            id=STARRED_GROUP_ID,
            course_id=course_id,
            # 界面显示的是自己词典里的"星标 / Starred"（认 is_starred_group 这个旗标）。
            # 这里给个占位值就够了 —— 后端拼中文的话，英文界面上就只有这一行是中文
            name="starred",
            file_count=starred,
            is_starred_group=True,
        )
    ]
    out += [
        UserGroupOut(
            id=g.id,
            course_id=course_id,
            name=g.name,
            sort_order=g.sort_order,
            file_count=counts.get(g.id, 0),
        )
        for g in groups
    ]
    return out


@router.post("/courses/{course_id}/user-groups", response_model=UserGroupOut, status_code=201)
async def create_user_group(
    course_id: int, payload: UserGroupCreate, db: AsyncSession = Depends(get_db)
) -> UserGroupOut:
    name = payload.name.strip()
    if not name:
        raise api_error(400, "group_name_empty")
    clash = (
        await db.execute(
            select(UserGroup).where(UserGroup.course_id == course_id, UserGroup.name == name)
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise api_error(409, "group_name_taken", name=name)
    group = UserGroup(course_id=course_id, name=name)
    db.add(group)
    await db.commit()
    return UserGroupOut(id=group.id, course_id=course_id, name=group.name, file_count=0)


@router.patch("/user-groups/{group_id}", response_model=UserGroupOut)
async def rename_user_group(
    group_id: int, payload: UserGroupUpdate, db: AsyncSession = Depends(get_db)
) -> UserGroupOut:
    group = await db.get(UserGroup, group_id)
    if group is None:
        raise api_error(404, "group_not_found")
    name = payload.name.strip()
    if not name:
        raise api_error(400, "group_name_empty")
    clash = (
        await db.execute(
            select(UserGroup).where(
                UserGroup.course_id == group.course_id,
                UserGroup.name == name,
                UserGroup.id != group_id,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise api_error(409, "group_name_taken", name=name)
    group.name = name
    await db.commit()
    counts, _ = await userdata.group_counts(db, group.course_id)
    return UserGroupOut(
        id=group.id,
        course_id=group.course_id,
        name=group.name,
        file_count=counts.get(group.id, 0),
    )


@router.delete("/user-groups/{group_id}", status_code=204)
async def delete_user_group(group_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """删组只删这个组本身。组里的文件该在哪还在哪 —— 星标、别的分组都不受影响。"""
    group = await db.get(UserGroup, group_id)
    if group is None:
        raise api_error(404, "group_not_found")
    members = (
        (
            await db.execute(
                select(UserGroupMember).where(UserGroupMember.group_id == group_id)
            )
        )
        .scalars()
        .all()
    )
    entry_ids = [m.user_file_id for m in members]
    await db.delete(group)
    await db.flush()
    # 组没了之后，那些"只因为在这个组里"而存在的记录就该回收
    for entry in (
        (await db.execute(select(UserFile).where(UserFile.id.in_(entry_ids or [0]))))
        .scalars()
        .all()
    ):
        await userdata._drop_if_unused(db, entry)
    await db.commit()


@router.post("/user-groups/{group_id}/files", response_model=UserActionResult)
async def add_files_to_user_group(
    group_id: int, payload: GroupMemberRequest, db: AsyncSession = Depends(get_db)
) -> UserActionResult:
    group = await db.get(UserGroup, group_id)
    if group is None:
        raise api_error(404, "group_not_found")
    files = await _course_files(db, group.course_id, payload.file_ids)
    changed = await userdata.add_to_group(db, group, files)
    await db.commit()
    return UserActionResult(changed=changed)


@router.post("/user-groups/{group_id}/files/remove", response_model=UserActionResult)
async def remove_files_from_user_group(
    group_id: int, payload: GroupMemberRequest, db: AsyncSession = Depends(get_db)
) -> UserActionResult:
    """用 POST 而不是 DELETE：要传一批 file_id，DELETE 带 body 各家实现不一。"""
    group = await db.get(UserGroup, group_id)
    if group is None:
        raise api_error(404, "group_not_found")
    files = await _course_files(db, group.course_id, payload.file_ids)
    changed = await userdata.remove_from_group(db, group, files)
    await db.commit()
    return UserActionResult(changed=changed)


@router.post("/courses/{course_id}/star", response_model=UserActionResult)
async def set_star(
    course_id: int, payload: StarRequest, db: AsyncSession = Depends(get_db)
) -> UserActionResult:
    files = await _course_files(db, course_id, payload.file_ids)
    changed = await userdata.set_starred(db, files, payload.starred)
    await db.commit()
    return UserActionResult(changed=changed)


@router.post("/courses/{course_id}/organize", response_model=OrganizeResult)
async def reorganize(course_id: int, db: AsyncSession = Depends(get_db)) -> OrganizeResult:
    """重跑自动分组规则（不会动用户锁定的文件）。"""
    result = await organize_course_files(db, course_id)
    return OrganizeResult(**result)
