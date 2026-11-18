"""全局资源：跨课程的一份列表 + 星标 + 「最新」。

和课程页**故意不一样**：

- **没有分组。** 跨课程的分组没有意义（"Week 3"在六门课里各指一件事），
  所以这里只有星标 —— 它是用户对单份资料的判断，跟哪门课无关。
- **先给"最新"和"星标"，再给"查找"。** 打开全局页的人想知道的是"有什么新东西"
  和"我收的那几份在哪"；按课程翻资料是课程页的事，只有明确要找什么的时候
  才需要那套列表。

筛选、搜索、序列化全部复用课程页那一套（`api/filters.py`、`services/search.py`、
`api/v1/files.py:to_file_out`）—— 两个页面对"什么是一份资料"必须说同一套话。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.errors import api_error
from app.api.filters import FileFilters, apply_file_filters, file_filters, parse_instant
from app.api.v1.files import bundle_response, to_file_out
from app.db.session import get_db
from app.models.course import Course
from app.models.file import CourseFile, FileGroupMember, FileSource
from app.models.userdata import CourseViewState, UserFile
from app.schemas.files import (
    BoardOption,
    CourseBucket,
    FileOut,
    GlobalFilterOptions,
    GroupMemberRequest,
    ResourceSummary,
    SeenRequest,
    StarRequest,
    UserActionResult,
)
from app.services import boards, search, userdata

router = APIRouter(tags=["resources"])

#: 比时刻用的兜底值。SQLite 的 max() 只要有一个参数是 NULL 就返回 NULL，
#: 所以两边都得先 coalesce 掉 —— 而"没有任何时间"的资料应该排在最后，
#: 不是消失，所以兜底值取最小而不是跳过这一行
_EPOCH = datetime(1970, 1, 1)


def _first_synced():
    """这门课第一次同步完的时刻。没有水位线行 = 还没同步完过一轮 → NULL；
    有行但没记（迁移之前的老行）→ 当作很早，"第一次收到"照旧算数。"""
    return (
        select(func.coalesce(CourseViewState.first_synced_at, _EPOCH))
        .where(CourseViewState.course_id == CourseFile.course_id)
        .scalar_subquery()
    )


def _changed_at():
    """"这份资料最后一次变动"的 SQL 表达式 = max(第一次被收到, Canvas 更新时间)。

    两个都要：一个**新**文件的 canvas_updated_at 可能是三年前的（教授把旧讲义
    挂进来），而一个**被改过**的老文件 first_seen_at 停在上个月。只看任何一个
    都会漏掉一半"最新"。

    但**第一次同步收进来的不算"收到"**：那一轮的 first_seen_at 全是同步那一刻，
    算进去的话装好软件当天「今天」「本周」会把整个学期列出来。所以 first_seen_at
    只有晚于这门课第一次同步完的时刻才算数 —— 和"首次同步不产生提醒"同一条规则。

    `func.max` 在 SQLite 里是标量 max（两个参数）。换 Postgres 要改成 GREATEST。
    """
    received = case(
        (CourseFile.first_seen_at > _first_synced(), CourseFile.first_seen_at),
        else_=_EPOCH,
    )
    return func.max(received, func.coalesce(CourseFile.canvas_updated_at, _EPOCH))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


#: 课程没有水位线行（还没同步完过）。和"有行但没记时间"要分开
_NO_BASELINE = object()


def _changed_at_of(file: CourseFile, first_synced) -> datetime | None:
    """同一件事在 Python 侧的版本，给响应里的 changed_at 用。
    `first_synced` 是这门课的 first_synced_at，没有水位线行时传 _NO_BASELINE。"""
    stamps = [file.canvas_updated_at] if file.canvas_updated_at is not None else []
    if file.first_seen_at is not None and first_synced is not _NO_BASELINE:
        if first_synced is None or _utc(file.first_seen_at) > _utc(first_synced):
            stamps.append(file.first_seen_at)
    return max(stamps, key=_utc) if stamps else None


def _unseen_clause():
    """"我还没看过的"：这份资料的变动时间晚于它所属课程的水位线，
    而且用户没有专门点开过它（点开之后又被改了就重新算没看过）。

    **没有水位线的课一律算"没有更新"。** 那意味着这门课还没同步完过一轮，
    宁可少报，也不能把整个库报成新的 —— NULL 的比较天然为假，正好是这个语义。
    """
    changed = _changed_at()
    watermark = (
        select(CourseViewState.seen_at)
        .where(CourseViewState.course_id == CourseFile.course_id)
        .scalar_subquery()
    )
    opened = (
        select(UserFile.id)
        .where(
            UserFile.course_id == CourseFile.course_id,
            UserFile.resource_key == CourseFile.resource_key,
            UserFile.seen_at.is_not(None),
            UserFile.seen_at >= changed,
        )
        .exists()
    )
    return (changed > watermark) & ~opened


def _base_query(
    filters: FileFilters,
    *,
    starred: bool = False,
    unseen: bool = False,
    since: datetime | None = None,
) -> Select:
    """全局列表的骨架。只同步中的课程参与 —— 没在同步的课在界面上压根不存在。

    星标视图要连"已从 Canvas 删除"的一起列出来（用户收藏过的东西不能凭空消失，
    只是打个标记不给下载）；其他视图不列。这条规则和课程页的「我的分组」一致。
    """
    stmt = (
        select(CourseFile)
        .join(Course, Course.id == CourseFile.course_id)
        .where(Course.sync_enabled.is_(True))
    )
    if starred:
        stmt = stmt.join(
            UserFile,
            (UserFile.file_id == CourseFile.id) & (UserFile.is_starred.is_(True)),
        )
    else:
        stmt = stmt.where(CourseFile.is_deleted_on_canvas.is_(False))
    if unseen:
        stmt = stmt.where(_unseen_clause())
    if since is not None:
        # 时间窗口按"变动时间"算，和「最新」那一栏的排序用的是同一个值
        stmt = stmt.where(_changed_at() >= since)
    return apply_file_filters(stmt, filters)


def _ordered(stmt: Select, sort_by: str, order: str) -> Select:
    """排序。`changed` 是全局页特有的：「最新」问的是"最近有什么变动"，
    而 `date` 那一路给的是资料自己的日期（公告的发布时间可能是去年的）。"""
    column = {
        "name": CourseFile.display_name,
        "changed": _changed_at(),
    }.get(sort_by, CourseFile.display_date)
    direction = column.asc() if order == "asc" else column.desc()
    # 日期可能为空，统一排到末尾，免得空值挤在最前面
    return stmt.order_by(direction.nullslast(), CourseFile.display_name)


async def _serialize(
    db: AsyncSession, stmt: Select, terms: list, sort_by: str, limit: int | None
) -> list[FileOut]:
    """跑查询 + 搜索过滤 + 拼响应。四个列表接口共用这一段。"""
    stmt = stmt.distinct().options(selectinload(CourseFile.sources))
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
            # 稳定排序：同分的仍按 SQL 定的顺序
            files.sort(key=lambda f: -hits[f.id].score)

    # **截断放在搜索之后**：limit 的意思是"给我最新的 N 条命中"，
    # 先截断再过滤会给出一个既不满 N 条、又漏掉更相关结果的列表
    if limit is not None:
        files = files[:limit]
    if not files:
        return []

    file_ids = [f.id for f in files]
    marks = await userdata.annotations_for(db, file_ids)
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

    courses = {
        cid: (canvas_id, name, code)
        for cid, canvas_id, name, code in (
            await db.execute(
                select(Course.id, Course.canvas_id, Course.name, Course.course_code).where(
                    Course.id.in_({f.course_id for f in files})
                )
            )
        ).all()
    }

    baselines = dict(
        (
            await db.execute(
                select(CourseViewState.course_id, CourseViewState.first_synced_at).where(
                    CourseViewState.course_id.in_({f.course_id for f in files})
                )
            )
        ).all()
    )

    out: list[FileOut] = []
    for f in files:
        canvas_id, name, code = courses.get(f.course_id, (None, None, None))
        item = to_file_out(
            f, groups_by_file.get(f.id, []), canvas_id, marks.get(f.id), hits.get(f.id)
        )
        # 全局列表里一行必须自己说清"这是哪门课的"
        item.course_name = name
        item.course_code = code
        item.changed_at = _changed_at_of(f, baselines.get(f.course_id, _NO_BASELINE))
        out.append(item)
    return out


# ---------- 列表 ----------


@router.get("/resources", response_model=list[FileOut])
async def list_resources(
    q: str | None = Query(
        None, description="搜索，语法和课程页完全一致（见 /courses/{id}/files 的 q）"
    ),
    starred: bool = Query(False, description="只看星标"),
    unseen: bool = Query(False, description="只看我还没看过的（按课程水位线判定）"),
    since: str | None = Query(
        None, description="只看这个时刻之后变动过的，ISO 时刻。今天 / 本周就是靠它"
    ),
    sort_by: str = Query("changed", description="changed / date / name / relevance"),
    order: str = Query("desc", description="asc / desc"),
    limit: int | None = Query(None, ge=1, le=500, description="只要前 N 条，首页那两个框用"),
    filters: FileFilters = Depends(file_filters),
    db: AsyncSession = Depends(get_db),
) -> list[FileOut]:
    """跨课程的资源列表。首页的两个框、星标二级页、查找页都是它的不同参数。"""
    stmt = _base_query(filters, starred=starred, unseen=unseen, since=parse_instant(since))
    return await _serialize(
        db, _ordered(stmt, sort_by, order), search.parse_query(q), sort_by, limit
    )


@router.get("/resources/summary", response_model=ResourceSummary)
async def resources_summary(
    today_since: str | None = Query(
        None, description="用户所在时区里「今天」的起点，ISO 时刻。前端算，后端不猜时区"
    ),
    week_since: str | None = Query(None, description="同上，「本周」的起点"),
    db: AsyncSession = Depends(get_db),
) -> ResourceSummary:
    """首页那两个框上的数字。

    时间窗口的边界由前端给：那取决于用户选的时区，后端猜一个只会在跨日的那几个
    小时里给出和列表对不上的数字。没给就退回"不限时间"。
    """
    empty = FileFilters()

    async def count(**kw) -> int:
        stmt = _base_query(empty, **kw).with_only_columns(
            func.count(func.distinct(CourseFile.id))
        )
        return (await db.execute(stmt)).scalar_one()

    return ResourceSummary(
        unseen=await count(unseen=True),
        today=await count(since=parse_instant(today_since)),
        week=await count(since=parse_instant(week_since)),
        starred=await count(starred=True),
        total=await count(),
    )


@router.get("/resources/courses", response_model=list[CourseBucket])
async def list_resource_courses(
    starred: bool = Query(False, description="只数星标，星标页的侧边栏用"),
    filters: FileFilters = Depends(file_filters),
    db: AsyncSession = Depends(get_db),
) -> list[CourseBucket]:
    """查找页左边那一列：按课程分组，计数跟着筛选走。

    顺序和边栏一致（手动顺序 → 名字），用户在两处看到的课程排列必须是同一个 ——
    否则在边栏里排好的顺序在这里又乱了一遍。
    """
    stmt = (
        _base_query(filters, starred=starred)
        .with_only_columns(
            Course.id,
            Course.name,
            Course.course_code,
            Course.is_concluded,
            Course.sidebar_sort_order,
            func.count(func.distinct(CourseFile.id)),
        )
        .group_by(Course.id)
    )
    rows = (await db.execute(stmt)).all()
    # 0 = 从没排过，这些排在排过的后面（和边栏同一条规则）
    rows = sorted(rows, key=lambda r: (r[4] == 0, r[4], (r[2] or r[1]).lower()))
    return [
        CourseBucket(
            course_id=cid,
            name=name,
            course_code=code,
            is_concluded=concluded,
            file_count=count,
        )
        for cid, name, code, concluded, _sort, count in rows
    ]


@router.get("/resources/filters", response_model=GlobalFilterOptions)
async def resource_filter_options(db: AsyncSession = Depends(get_db)) -> GlobalFilterOptions:
    """全局筛选面板的可选项：有哪些课、有哪些 Canvas 来源。

    **不带筛选、不带数量。** 跟着筛选走的话，取消一个条件之后那个选项就从面板上
    消失、再也选不回来；数量则只能是"没筛之前有几个"，勾上「文件」之后旁边
    写着的 5 就成了假数字（课程侧边栏的计数是实时的，那里才是看数量的地方）。
    """
    courses = await list_resource_courses(starred=False, filters=FileFilters(), db=db)
    # 只列**库里真的出现过**的来源。一个勾上去必然筛出空列表的选项不是选项 ——
    # 学校把 Files 标签页关了的人，面板上就不该有 Canvas Files 这一格
    used = set(
        (
            await db.execute(
                select(FileSource.source_kind)
                .join(Course, Course.id == FileSource.course_id)
                .where(Course.sync_enabled.is_(True))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    live_nodes = {boards.tree_node_of(boards.board_of(kind)) for kind in used}
    nodes = [n for n in boards.TREE_ORDER if n in live_nodes]
    return GlobalFilterOptions(
        courses=courses,
        boards=[BoardOption(id=n, label=boards.tree_label_of(n)) for n in nodes],
    )


# ---------- 看过了 ----------


@router.post("/resources/seen", response_model=UserActionResult)
async def mark_resources_seen(
    payload: SeenRequest, db: AsyncSession = Depends(get_db)
) -> UserActionResult:
    """把这几份资料标成"专门看过了"。前端在下载 / 打开链接 / 看详情时调。"""
    files = await _files_by_id(db, payload.file_ids)
    changed = await userdata.mark_files_seen(db, files)
    await db.commit()
    return UserActionResult(changed=changed)


@router.post("/resources/seen-all", response_model=UserActionResult)
async def mark_everything_seen(db: AsyncSession = Depends(get_db)) -> UserActionResult:
    """全部标记为已看：把所有在同步课程的水位线推到现在。"""
    changed = await userdata.mark_all_seen(db)
    await db.commit()
    return UserActionResult(changed=changed)


@router.post("/courses/{course_id}/seen", response_model=UserActionResult)
async def mark_course_seen(
    course_id: int, db: AsyncSession = Depends(get_db)
) -> UserActionResult:
    """这门课的资源页被打开了 —— 把它的水位线推到现在。

    **为什么是打开页面就算，而不是逐个文件判定。** 见
    `models/userdata.py::CourseViewState`：用户常常是在课程页看文件的，
    那里看完的东西不能还堵在全局「更新」里。
    """
    if await db.get(Course, course_id) is None:
        raise api_error(404, "course_not_found")
    await userdata.mark_course_seen(db, course_id)
    await db.commit()
    return UserActionResult(changed=1)


# ---------- 星标 ----------


@router.post("/resources/star", response_model=UserActionResult)
async def set_resource_star(
    payload: StarRequest, db: AsyncSession = Depends(get_db)
) -> UserActionResult:
    """跨课程加 / 取消星标。课程页那个接口按课程收 id，全局页没有"当前这门课"。"""
    files = await _files_by_id(db, payload.file_ids)
    changed = await userdata.set_starred(db, files, payload.starred)
    await db.commit()
    return UserActionResult(changed=changed)


# ---------- 打包下载 ----------


@router.post("/resources/bundle")
async def download_resource_bundle(
    payload: GroupMemberRequest,
    background: BackgroundTasks,
    # 包里那份说明是给人读的，所以跟着界面语言走；包名里的时间戳按用户看到的时区打
    lang: str = Query("zh", description="包内说明的语言：zh / en"),
    tz: str = Query("UTC", description="包名里时间戳用的时区，例如 America/Toronto"),
    db: AsyncSession = Depends(get_db),
):
    """把跨课程选中的一批资料打成 zip。

    **和课程页走同一段实现**（`files.py:bundle_response` → `services/bundle.py`）：
    怎么抓、失败怎么报、包名怎么起必须完全一致，否则同一个按钮在两个页面上
    行为不同。唯一的区别是**按课程分目录** —— 六门课各有一份 `syllabus.pdf`
    是常态，平铺在一个包里用户认不出哪个是哪门课的。
    """
    files = await _files_by_id(db, payload.file_ids)
    folders: dict[int, str] | None = None
    course_ids = {f.course_id for f in files}
    if len(course_ids) > 1:
        folders = {
            cid: (code or name)
            for cid, name, code in (
                await db.execute(
                    select(Course.id, Course.name, Course.course_code).where(
                        Course.id.in_(course_ids)
                    )
                )
            ).all()
        }
    return await bundle_response(files, background, lang, tz, folders=folders)


async def _files_by_id(db: AsyncSession, file_ids: list[int]) -> list[CourseFile]:
    """按 id 取文件，不限课程。一个都没取到就报错 —— 静默成功会让前端
    以为标记成功了，而实际上什么都没发生。"""
    if not file_ids:
        raise api_error(400, "no_files_selected")
    files = list(
        (await db.execute(select(CourseFile).where(CourseFile.id.in_(file_ids))))
        .scalars()
        .all()
    )
    if len(files) != len(set(file_ids)):
        raise api_error(404, "file_not_found")
    return files
