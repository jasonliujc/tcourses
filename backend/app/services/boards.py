"""课程板块（Canvas 导航栏上的那一列）—— 全局唯一的一份定义。

一门课在 Canvas 上有哪些板块，是"资料会藏在哪儿"的完整答案。之前这份知识
散在三个地方各写一遍（收集器扫什么、设置页显示什么、侧边栏怎么分组），
于是三处的说法互相对不上：设置页说「板块覆盖 4 条 部分覆盖」，侧边栏叫
「Canvas Modules & Pages」，而收集器内部又是另一套 source_kind。

现在只有这一份。命名统一用英文，和文件类型（PDF / Docs / Video）一致。

**认不出的板块一律归到 Others**，并且明确标出来"这个没扫" —— 做不到覆盖
每一种板块可以接受，让用户以为自己看到的是全部不行。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.file import SourceKind

# 板块 id。和 Canvas 的 tab id 对齐，能对齐的就直接用同一个词
HOME = "home"
SYLLABUS = "syllabus"
ANNOUNCEMENTS = "announcements"
FILES = "files"
ASSIGNMENTS = "assignments"
QUIZZES = "quizzes"
MODULES = "modules"
PAGES = "pages"
DISCUSSIONS = "discussions"
OTHERS = "others"


@dataclass(frozen=True)
class Board:
    id: str
    label: str
    #: Canvas 导航栏里对应的 tab id（None 表示没有一一对应的 tab）
    tab: str | None


#: 展示顺序 = 学生在 Canvas 导航栏上看到的大致顺序。Others 永远最后
BOARDS: list[Board] = [
    Board(HOME, "Home", "home"),
    Board(SYLLABUS, "Syllabus", "syllabus"),
    Board(ANNOUNCEMENTS, "Announcements", "announcements"),
    Board(FILES, "Files", "files"),
    Board(ASSIGNMENTS, "Assignments", "assignments"),
    Board(QUIZZES, "Quizzes", "quizzes"),
    Board(MODULES, "Modules", "modules"),
    Board(PAGES, "Pages", "pages"),
    Board(DISCUSSIONS, "Discussions", "discussions"),
    Board(OTHERS, "Others", None),
]

BY_ID = {b.id: b for b in BOARDS}
BY_TAB = {b.tab: b for b in BOARDS if b.tab}

#: 这些板块里没有课程资料 —— 不扫是对的，也不该出现在"没同步"的清单里
NO_CONTENT_TABS = {
    "grades",
    "people",
    "settings",
    "rubrics",
    "outcomes",
    "collaborations",
    "conferences",
    "attendance",
    "commons",
    "search",
    "link_validator",
    "profile",
    "gradebook",
}

#: 资源来源 -> 它属于哪个板块。侧边栏和设置页共用这一张表，
#: 所以两边说的"Pages"一定是同一件事
SOURCE_TO_BOARD = {
    SourceKind.FILES: FILES,
    SourceKind.MODULE: MODULES,
    SourceKind.PAGE: PAGES,
    SourceKind.ANNOUNCEMENT: ANNOUNCEMENTS,
    SourceKind.ASSIGNMENT: ASSIGNMENTS,
    SourceKind.SYLLABUS: SYLLABUS,
    SourceKind.QUIZ: QUIZZES,
    SourceKind.DISCUSSION: DISCUSSIONS,
    # 导航栏上的外部工具不属于任何标准板块
    SourceKind.NAV: OTHERS,
}


#: **侧边栏专用**：Modules 和 Pages 在树上合成一个节点。
#:
#: 两处回答的是不同的问题，所以切法可以不同：
#:
#: - 设置页问"这门课的每个板块扫到了没有" —— Canvas 导航栏上它们是两项，
#:   哪一项被学校关了也要分别说清楚，所以那里必须分开。
#: - 侧边栏问"我去哪儿找" —— 而 Canvas 上页面几乎都挂在模块下，收集时也正是
#:   按所属模块给页面归的组。分开的话同一个 "Week 3" 在两个父节点下各出现一次、
#:   各显示一部分，用户还得自己合并，那正是这个产品该替他做的事。
#:   （实测 7 处同名容器：三门课里分别是 5 个、1 个、1 个）
MODULES_PAGES = "modules_pages"
_TREE_MERGE = {MODULES: MODULES_PAGES, PAGES: MODULES_PAGES}
_TREE_LABELS = {MODULES_PAGES: "Modules & Pages"}


def board_of(source_kind: str) -> str:
    return SOURCE_TO_BOARD.get(source_kind, OTHERS)


def tree_node_of(board_id: str) -> str:
    """板块 -> 侧边栏上的节点 id。"""
    return _TREE_MERGE.get(board_id, board_id)


def tree_label_of(node_id: str) -> str:
    return _TREE_LABELS.get(node_id) or label_of(node_id)


def source_kinds_for_node(node_id: str) -> list[str]:
    """侧边栏节点 -> 它包含哪些来源类型。按板块筛文件时用。"""
    return [k for k, b in SOURCE_TO_BOARD.items() if tree_node_of(b) == node_id]


#: 侧边栏节点的展示顺序：按板块顺序，合并掉的那些只占第一次出现的位置
TREE_ORDER: list[str] = list(dict.fromkeys(tree_node_of(b.id) for b in BOARDS))


def label_of(board_id: str) -> str:
    board = BY_ID.get(board_id)
    return board.label if board else board_id


# 板块的扫描结果。给用户看的就这几种，不再混着"正常 / 部分覆盖 / 学校未开放"
SCANNED = "scanned"  # 扫过了
UNAVAILABLE = "unavailable"  # 学校把这个板块关了（403/404）
DISABLED = "disabled"  # 用户自己在设置里关掉了
UNSUPPORTED = "unsupported"  # 这门课有这个板块，但我们还不会扫 —— 唯一需要用户注意的
