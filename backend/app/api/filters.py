"""资源筛选：一份定义，课程页和全局页共用。

面板上那几个轴。**六个接口共用同一份定义**：课程列表、来源树、自动分组、
用户分组，加上全局资源列表和它的课程侧边栏 —— 筛完之后侧边栏的分组和计数
必须跟着变，否则用户会看到"Lectures 28"，点进去却是"没有资源符合筛选条件"，
那个 28 就成了假数字。

**一门课是可选的**：课程页把它钉死（`course_id=`），全局页不钉，
于是"课程"本身变成筛选面板上的一个轴（`course_id` 可重复）。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Query
from sqlalchemy import select

from app.models.file import CourseFile, FileGroup, FileGroupMember, FileSource, Medium
from app.services import boards
from app.services.file_collector import UNGROUPED_PAGES, UNTITLED_MODULE

#: 认不出归属的兜底容器名，永远排在最后
UNIDENTIFIED = (UNTITLED_MODULE, UNGROUPED_PAGES)


@dataclass(frozen=True)
class FileFilters:
    """一次筛选的全部条件。

    **轴之间是并且，轴内部是或者。** 选了「板块=Syllabus」+「类型=PDF」要的是
    两个条件都满足的；而在同一个轴上勾了两个板块，要的显然是"这两个里的任意一个"。

    每个条件问的都是**这份资料的属性**，不是"它某一条来源记录的属性"：一份资料
    同时挂在好几个地方是常态，所以"它出现在大纲里"和"它出现在 Week 3 里"可以
    同时为真、由不同的来源记录支撑 —— 这也正是行上的标签要做成一组而不是一个的原因。
    """

    medium: str | None = None
    boards: tuple[str, ...] = ()
    containers: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()
    date_from: datetime | None = None
    date_to: datetime | None = None
    #: 只有全局页会用。课程页里"哪门课"是既定前提，不是筛选条件
    course_ids: tuple[int, ...] = ()

    @property
    def active(self) -> bool:
        return bool(
            self.medium
            or self.boards
            or self.containers
            or self.kinds
            or self.date_from
            or self.date_to
            or self.course_ids
        )


def file_filters(
    medium: str | None = Query(
        None, description="按载体筛选：file（可下载的文件）/ link（外部链接）"
    ),
    board: list[str] = Query(
        default_factory=list, description="板块 id，可重复：syllabus / modules_pages / …"
    ),
    container: list[str] = Query(
        default_factory=list, description="板块下的容器名，可重复：Week 3 / Lectures / …"
    ),
    kind: list[str] = Query(
        default_factory=list, description="资源类型 key，可重复：pdf / docs / link_video / …"
    ),
    date_from: str | None = Query(None, description="显示日期下界，ISO 时刻（含）"),
    date_to: str | None = Query(None, description="显示日期上界，ISO 时刻（含）"),
    # 参数名**不能叫 course_id**：课程页那几个路由的路径参数就叫这个
    # （`/courses/{course_id}/files`），同名会让 FastAPI 把路径参数当成查询参数
    course: list[int] = Query(
        default_factory=list, description="课程 id，可重复。只有全局资源页会用"
    ),
) -> FileFilters:
    return FileFilters(
        medium=medium if medium in (Medium.FILE, Medium.LINK) else None,
        boards=tuple(board),
        containers=tuple(container),
        kinds=tuple(kind),
        date_from=parse_instant(date_from),
        date_to=parse_instant(date_to),
        course_ids=tuple(course),
    )


def apply_file_filters(stmt, f: FileFilters, course_id: int | None = None):
    """把筛选条件加到一个以 CourseFile 为主表的查询上。

    `course_id` 是"当前只看这一门课"（课程页），和 `f.course_ids` 那个
    可多选的轴（全局页）互不冲突：一个是页面的前提，一个是用户勾的条件。
    """
    if f.medium:
        stmt = stmt.where(CourseFile.medium == f.medium)
    if f.course_ids:
        stmt = stmt.where(CourseFile.course_id.in_(f.course_ids))

    # 用 EXISTS 而不是 join：join 会让一个有多条来源的文件在结果里出现多次，
    # 得再靠 distinct 收回去；而 EXISTS 天然就是"有没有这么一条"
    if f.boards:
        kinds = [k for b in f.boards for k in boards.source_kinds_for_node(b)] or list(f.boards)
        stmt = stmt.where(
            select(FileSource.id)
            .where(FileSource.file_id == CourseFile.id, FileSource.source_kind.in_(kinds))
            .exists()
        )
    if f.containers:
        names = list(f.containers)
        # "认不出归属"在界面上是一个节点，库里是两个容器名
        if UNGROUPED_PAGES in names:
            names += [n for n in UNIDENTIFIED if n not in names]
        stmt = stmt.where(
            select(FileSource.id)
            .where(FileSource.file_id == CourseFile.id, FileSource.container_name.in_(names))
            .exists()
        )
    if f.kinds:
        # 类型是算出来的（按扩展名 / 按域名），SQL 里没有这一列 —— 但自动整理
        # 每轮都会把每个文件放进恰好一个 `type:` 分组，直接查那个就行，
        # 不用把整门课的文件都拉进内存再过一遍
        group_match = [
            FileGroupMember.file_id == CourseFile.id,
            FileGroup.rule_key.in_([f"type:{k}" for k in f.kinds]),
        ]
        if course_id is not None:
            group_match.append(FileGroup.course_id == course_id)
        else:
            # 全局页不钉课程，但分组是每门课各自一套，所以得跟着文件所属的课走 ——
            # 否则一个文件会因为**另一门课**里有个同名的 `type:pdf` 分组而命中
            group_match.append(FileGroup.course_id == CourseFile.course_id)
        stmt = stmt.where(
            select(FileGroupMember.id)
            .join(FileGroup, FileGroup.id == FileGroupMember.group_id)
            .where(*group_match)
            .exists()
        )
    # 日期范围按**显示的那个日期**筛，和列表里看到的、排序用的是同一个值。
    # 没有日期的资源在任何范围里都不算命中 —— 它压根没有日期可比
    if f.date_from is not None:
        stmt = stmt.where(CourseFile.display_date >= f.date_from)
    if f.date_to is not None:
        stmt = stmt.where(CourseFile.display_date <= f.date_to)
    return stmt


def matching_file_ids(f: FileFilters, course_id: int | None = None):
    """命中筛选的文件 id，给侧边栏那几个计数接口当子查询用。"""
    stmt = select(CourseFile.id)
    if course_id is not None:
        stmt = stmt.where(CourseFile.course_id == course_id)
    return apply_file_filters(stmt, f, course_id)


def parse_instant(value: str | None) -> datetime | None:
    """ISO 时刻 -> 可以和 display_date 比大小的 UTC naive datetime。

    库里存的是 naive UTC（SQLite 不保存时区），所以带时区的入参必须先换算到
    UTC 再把 tzinfo 摘掉 —— 否则比的是两串格式不同的字符串，结果毫无意义。

    界面传过来的边界已经是"用户所选时区里那一天的起止"换算出来的时刻，
    所以这里不需要知道用户在哪个时区。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed.tzinfo else parsed
