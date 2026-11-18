"""把一个文件压成界面上恒定显示的四要素：来源类型、来源路径、时间、文件类型。

不管用户在哪个视图（默认 / 按类型 / 自己的分组），每个文件都显示同样这四项，
不用点进去才知道这文件是什么、从哪来的。

两个兜底不能省：来源类型和文件类型都必须有值。一个文件如果显示不出"我是什么"，
用户就得点开才知道，那这几个字段就白加了。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.file import CourseFile, FileSource, Medium, SourceKind
from app.services import boards
from app.services.file_organizer import file_kind

# 一个文件可能同时来自多个地方，挑哪个当主来源。
# 排序依据是"信息量"：Modules 带模块名（最贴近课程结构），Files 带文件夹路径，
# 页面/公告/作业带标题和时间，都比"不知道从哪来"强。
_TAG_PRIORITY = [
    SourceKind.MODULE,
    SourceKind.FILES,
    SourceKind.SYLLABUS,
    SourceKind.QUIZ,
    SourceKind.PAGE,
    SourceKind.ANNOUNCEMENT,
    SourceKind.ASSIGNMENT,
]

# 和侧边栏的 Canvas Files / Modules / Pages 用同一套词。
# 只在详情页按来源逐条显示时用；行上的标签走 board_tags（板块，不是 source_kind）
TAG_LABELS = {
    SourceKind.FILES: "Files",
    SourceKind.MODULE: "Modules",
    SourceKind.PAGE: "Pages",
    SourceKind.ANNOUNCEMENT: "Announcements",
    SourceKind.ASSIGNMENT: "Assignments",
    SourceKind.SYLLABUS: "Syllabus",
    SourceKind.QUIZ: "Quizzes",
    SourceKind.DISCUSSION: "Discussions",
    SourceKind.NAV: "Course Nav",
}

#: 一条来源记录都没有时的兜底。理论上不该发生，但界面上不能出现空白标签。
#: 不用 Others：那是一个真实的板块（导航栏上的外部工具），两者混起来会让
#: "Others 有几个"这个数字变得没法解释
UNKNOWN_NODE = "unknown"


def _rank(source: FileSource) -> tuple[int, int]:
    return (
        _TAG_PRIORITY.index(source.source_kind)
        if source.source_kind in _TAG_PRIORITY
        else len(_TAG_PRIORITY),
        source.sort_hint,
    )


def board_tags(sources: list[FileSource]) -> list[str]:
    """这份资料出现在**哪几个板块**（侧边栏那套节点 id），主来源那个排最前。

    **一个文件可以有多个标签。** 只显示主来源那一个的话，行上写着"Pages"的资料
    会出现在"板块 = Syllabus"的筛选结果里 —— 显示说它在一个地方、筛选按它在
    所有地方来匹配，用户只能理解成出了 bug。让标签本身变成一个集合，
    显示和筛选说的就是同一件事了。

    实测：1233 份资料里 1222 份只属于一个板块，8 份两个，3 份三个 ——
    所以行上全画出来不会挤。
    """
    out: list[str] = []
    for source in sorted(sources, key=_rank):
        node = boards.tree_node_of(boards.board_of(source.source_kind))
        if node not in out:
            out.append(node)
    return out or [UNKNOWN_NODE]


@dataclass
class FileSummary:
    #: 板块节点 id，主来源的排第一。**恒非空**
    tags: list[str]
    source_label: str  # 完整路径，例如 "Week 3 · Slides from Class"
    source_date: datetime | None
    medium: str  # file / link —— 界面靠它决定类型标签画成文件还是外部链接
    kind: str
    kind_label: str
    html_url: str | None  # 点进去能跳回 Canvas 原处


def canvas_file_url(base_url: str, course_canvas_id: int, file_canvas_id: int) -> str:
    """文件在 Canvas 上的固定地址。

    并非每条来源都带 html_url（富文本里扒出来的链接就常常没有），
    但这个地址永远能构造出来 —— 详情页至少要有一个能点回 Canvas 的入口。
    """
    return f"{base_url.rstrip('/')}/courses/{course_canvas_id}/files/{file_canvas_id}"


def _pick_primary(sources: list[FileSource]) -> FileSource | None:
    """按来源类型的信息量挑主来源；同类型里挑排序靠前的。

    主来源只决定"路径显示哪一条"和"日期取哪一条"。**标签不由它决定** ——
    见 board_tags。"""
    if not sources:
        return None
    return sorted(sources, key=_rank)[0]


def _as_utc(value: datetime) -> datetime:
    """SQLite 读出来的 datetime 没有时区，刚从 Canvas 解析出来的有 ——
    两者放在一起比大小会直接抛 TypeError，把整门课的同步打断。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def pick_display_date(file: CourseFile, sources: list[FileSource]) -> datetime | None:
    """界面显示、同时也用来排序的那个日期。**它是什么**：

    | 这份资料是 | 日期取自 |
    |---|---|
    | Canvas 文件 | 文件本身在 Canvas 上的 `updated_at` |
    | 页面里的链接/文件 | **那个页面**最后修改的时间 |
    | 公告里的 | 公告的发布时间 |
    | 作业说明里的 | 作业最后更新的时间 |
    | 测验说明里的 | 测验的截止时间（Canvas 不给测验的修改时间） |
    | 导航栏上的工具 | 工具最后配置的时间 |
    | 课程大纲里的 / 模块条目 | **没有** —— Canvas 这两处不提供任何时间字段 |

    一句话：**"承载它的那个东西最后一次变动的时间"**，而不是"我们什么时候
    发现它的"。所以取不到就显示空，不拿别的时间冒充。

    两者必须是同一个值 —— 否则用户看到的日期和排列顺序对不上，
    列表看着就是乱的（曾经就是：显示主来源日期，却按文件更新时间排序）。

    主来源没日期时**回落到任何一条有日期的来源**。这一步不是锦上添花：
    主来源是按"信息量"挑的（Modules 排第一），而 Modules 里的 ExternalUrl 条目
    在 Canvas 上根本没有时间字段 —— 于是一个既挂在模块里、又出现在某个页面上的
    链接会挑中没日期的那条，界面上就是一个空的日期，尽管页面那条明明有。
    文件不会暴露这个问题：它们还能回落到自己的 canvas_updated_at，链接没有。
    """
    primary = _pick_primary(sources)
    if primary is not None and primary.source_date is not None:
        return primary.source_date
    dated = [_as_utc(s.source_date) for s in sources if s.source_date is not None]
    if dated:
        return max(dated)
    return file.canvas_updated_at


def summarize(file: CourseFile, sources: list[FileSource]) -> FileSummary:
    kind, kind_label = file_kind(file)
    primary = _pick_primary(sources)

    if primary is None:
        # 兜底：文件在库里但没有任何来源记录
        return FileSummary(
            tags=[UNKNOWN_NODE],
            source_label=file.canvas_folder_path or "—",
            source_date=file.canvas_updated_at,
            medium=file.medium or Medium.FILE,
            kind=kind,
            kind_label=kind_label,
            html_url=None,
        )

    return FileSummary(
        tags=board_tags(sources),
        source_label=primary.label or primary.container_name or "—",
        # 和排序共用同一个值，见 pick_display_date
        source_date=primary.source_date or file.canvas_updated_at,
        medium=file.medium or Medium.FILE,
        kind=kind,
        kind_label=kind_label,
        html_url=primary.html_url,
    )
