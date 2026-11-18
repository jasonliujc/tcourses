"""分组规则与文件四要素。

最重要的不变量：**每个维度是一次完整划分** —— 同一维度下各分组的文件数相加
必须等于文件总数。所以每个分类器对任何输入都必须恰好返回一条归属。

按周次 / 按用途两个维度已经移除，后续由高级筛选功能承担。
"""

from app.models.file import CourseFile, FileSource, SourceKind
from app.services.file_organizer import (
    CLASSIFIERS,
    DIMENSIONS,
    UNKNOWN_KIND,
    FileContext,
    file_kind,
    suggest_groups,
)
from app.services.file_summary import UNKNOWN_NODE, pick_display_date, summarize


def _file(display_name: str, folder: str | None = None, filename: str | None = None) -> CourseFile:
    return CourseFile(
        canvas_id=1,
        course_id=1,
        display_name=display_name,
        filename=filename or display_name,
        canvas_folder_path=folder,
    )


def _ctx(display_name: str, **kw) -> FileContext:
    containers = kw.pop("containers", None) or []
    return FileContext(file=_file(display_name, **kw), containers=containers)


# ---------- 完整划分不变量 ----------


def test_every_file_gets_exactly_one_group_per_dimension():
    for ctx in (
        _ctx("Week 3 Slides.pptx", folder="Lectures"),
        _ctx("ED10.pdf", containers=["Week 9"]),
        _ctx("mystery.xyz"),
        _ctx(""),
        _ctx("随便一个中文文件名.pdf"),
    ):
        suggestions = suggest_groups(ctx)
        assert len(suggestions) == len(CLASSIFIERS)
        dims = [s.rule_key.split(":", 1)[0] for s in suggestions]
        assert sorted(dims) == sorted(DIMENSIONS), f"{ctx.file.display_name}: {dims}"


def test_retired_dimensions_are_gone():
    """按周次 / 按用途已经移除，只剩按文件类型。"""
    assert DIMENSIONS == ["type"]
    ctx = _ctx("Week 3 Problem Set solution.pdf", folder="Assignments")
    dims = {s.rule_key.split(":", 1)[0] for s in suggest_groups(ctx)}
    assert dims == {"type"}


# ---------- 文件类型（含兜底） ----------


def test_file_kind_mapping():
    """分类按用户指定：PDF / Docs / PowerPoint / Data / Pictures，其余归 Code 等。"""
    assert file_kind(_file("syllabus.pdf"))[0] == "pdf"
    # Docs 含 Word 和苹果 Pages
    assert file_kind(_file("essay.docx"))[0] == "docs"
    assert file_kind(_file("essay.pages"))[0] == "docs"
    # PowerPoint 含 ppt 和 Keynote
    assert file_kind(_file("deck.pptx"))[0] == "powerpoint"
    assert file_kind(_file("deck.key"))[0] == "powerpoint"
    # Data 含 csv 和 Excel
    assert file_kind(_file("grades.csv"))[0] == "data"
    assert file_kind(_file("grades.xlsx"))[0] == "data"
    assert file_kind(_file("banner.gif"))[0] == "pictures"
    assert file_kind(_file("starter.ipynb"))[0] == "code"


def test_kind_labels_are_english():
    """和侧边栏的 Canvas Files / Modules / Pages 统一用英文。"""
    from app.services.file_organizer import FILE_KINDS

    for _key, label, _exts in FILE_KINDS:
        assert label.isascii(), label
    assert UNKNOWN_KIND[1] == "Other"


def test_source_tags_are_english():
    from app.services.file_summary import TAG_LABELS

    for label in TAG_LABELS.values():
        assert label.isascii(), label


def test_file_kind_always_has_a_fallback():
    """认不出扩展名也必须有类型 —— 界面上不能出现空白。"""
    for name in ("mystery.xyz", "no-extension", "", "档案"):
        key, label = file_kind(_file(name))
        assert key == UNKNOWN_KIND[0]
        assert label


# ---------- 四要素 ----------


def _source(kind: str, label: str, container: str = "", hint: int = 0) -> FileSource:
    return FileSource(
        file_id=1,
        course_id=1,
        source_kind=kind,
        source_key=f"{kind}:{label}",
        container_name=container or label,
        label=label,
        html_url=f"https://canvas.test/{kind}",
        sort_hint=hint,
    )


def test_summary_picks_most_informative_source():
    """一个文件常有多个来源，主来源要挑信息量最大的那个。"""
    file = _file("slides.pdf")
    sources = [
        _source(SourceKind.ANNOUNCEMENT, "10/16 公告 · 期中成绩"),
        _source(SourceKind.MODULE, "Week 3 · Slides from Class"),
    ]
    s = summarize(file, sources)
    # 标签是**一组板块**，主来源那个排第一 —— 这个文件既在模块里又在公告里，
    # 两个都要说出来，否则筛"公告"时它会出现，行上却写着 Modules & Pages
    assert s.tags == ["modules_pages", "announcements"]
    assert s.source_label == "Week 3 · Slides from Class"
    assert s.html_url  # 详情页要能跳回 Canvas


def test_summary_falls_back_when_no_source():
    """没有任何来源记录时也不能显示空白。"""
    s = summarize(_file("orphan.pdf", folder="Lectures"), [])
    assert s.tags == [UNKNOWN_NODE]
    assert s.source_label == "Lectures"
    assert s.kind_label


def test_summary_always_fills_every_field():
    """四要素任何一项为空，用户就得点开才知道这文件是什么。"""
    for file, sources in (
        (_file("a.pdf"), [_source(SourceKind.FILES, "Lectures/Week 1")]),
        (_file("b.unknownext"), []),
        (_file(""), [_source(SourceKind.PAGE, "Week 2 · 阅读材料")]),
    ):
        s = summarize(file, sources)
        assert s.tags
        assert s.source_label
        assert s.kind and s.kind_label
        # 载体也不能空：界面靠它决定类型标签画成"文件"还是"外部链接"
        assert s.medium in ("file", "link")


def test_display_date_survives_mixed_timezone_sources():
    """回归：一门课整个同步被打断。

    SQLite 读出来的 datetime 没有时区，刚从 Canvas 解析出来的有 —— 回落逻辑里
    把它们放进 max() 比大小，直接抛 TypeError，那门课这一轮什么都没写进去。
    """
    from datetime import UTC, datetime

    naive = _source(SourceKind.PAGE, "Week 1")
    naive.source_date = datetime(2026, 1, 2, 3, 4)  # 库里读出来的样子
    aware = _source(SourceKind.MODULE, "Week 1")
    aware.source_date = None  # 主来源（Modules 优先）没有日期
    later = _source(SourceKind.QUIZ, "Quiz 1")
    later.source_date = datetime(2026, 5, 6, 7, 8, tzinfo=UTC)

    picked = pick_display_date(_file("a.pdf"), [aware, naive, later])
    assert picked == datetime(2026, 5, 6, 7, 8, tzinfo=UTC)
