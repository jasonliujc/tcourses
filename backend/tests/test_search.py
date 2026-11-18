"""资源搜索。

两件事要成立：

1. **用户不该被迫猜标点。** 每个教授的命名习惯都不一样（`PS1.pdf`、
   `Problem Set 1.pdf`、`ps-1.pdf`、`PSet01.pdf` 是同一份东西），所以大小写、
   标点、全角、重音、驼峰、数字前导零全部抹平之后再比，还容许拼错一个字母。
2. **命中可以落在文件名以外**，并且要说得出落在哪儿 —— 搜 "week 3" 出来一个
   叫 `l3.pdf` 的文件，没有解释就像是搜错了。
"""

from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile, FileSource, Medium, SourceKind
from app.services import search

# ---------- 匹配本身（纯函数，不碰库） ----------


class _Src:
    def __init__(self, kind, container=None, title=None):
        self.source_kind = kind
        self.container_name = container
        self.item_title = title


class _File:
    def __init__(self, name, filename=None, folder=None, url=None, sources=()):
        self.display_name = name
        self.filename = filename
        self.canvas_folder_path = folder
        self.external_url = url
        self.sources = list(sources)


def _match(file: _File, q: str):
    return search.match(search.fields_of(file, file.sources), search.parse_query(q))


@pytest.mark.parametrize(
    "q",
    [
        "PS1",  # 原样
        "ps1",  # 大小写
        "ps 1",  # 字母数字之间断开
        "ps-1",  # 标点
        "p.s.1",  # 更多标点
        "ｐｓ１",  # 全角
        "  ps1  ",  # 两头空白
    ],
)
async def test_punctuation_and_case_are_ignored(q):
    assert _match(_File("PS1.pdf"), q) is not None


@pytest.mark.parametrize(
    ("name", "q"),
    [
        ("Week03_Slides.pdf", "week 3"),  # 前导零
        ("week3.pdf", "Week 03"),  # 反过来也要成立
        ("MidtermReview.pdf", "midterm review"),  # 驼峰
        ("midterm review.pdf", "MidtermReview"),  # 反过来
        ("Résumé Template.docx", "resume"),  # 重音
        ("Problem Set 1.pdf", "problemset1"),  # 用户不打空格
        ("ProblemSet1.pdf", "problem set 1"),
        ("第三周讲义.pdf", "第三周"),  # 中文没有空格，靠去空格的那一路
    ],
)
async def test_naming_habits_do_not_matter(name, q):
    assert _match(_File(name), q) is not None


@pytest.mark.parametrize("q", ["lectrue 1", "lecutre 1", "lecture1"])
async def test_one_typo_still_matches(q):
    """换位（`lectrue`）算一个错，不是两个 —— 那是最常见的手误。"""
    assert _match(_File("Lecture 1 Intro.pdf"), q) is not None


async def test_short_words_are_not_fuzzy():
    """`ps` 和 `cs` 只差一个字母，但它们是两门完全不同的课。"""
    assert _match(_File("CS 1 notes.pdf"), "ps") is None


async def test_short_words_only_match_at_a_word_start():
    """一两个字母出现在别的词中间基本都是噪声。"""
    assert _match(_File("HW A1.pdf"), "a1") is not None
    assert _match(_File("2013 archive.pdf"), "1") is None  # 不是 "2013" 里的那个 1
    assert _match(_File("Week 03 notes.pdf"), "3") is not None  # 抹掉前导零之后在词首


async def test_short_chinese_words_are_not_restricted_to_word_starts():
    """中文词之间没有空格，"讲义"出现在"第三周讲义"中间正是该被找到的样子。"""
    assert _match(_File("第三周讲义.pdf"), "讲义") is not None


async def test_link_urls_only_match_at_a_word_start():
    """URL 里有大段随机串，在里面找两三个字母必然撞上。"""
    video = _File("Lecture 7", url="https://play.library.mediahub.edu/watch/6f3a1b2c")
    assert _match(video, "a1") is None  # 哈希中间的 a1 不算
    assert _match(video, "mediahub") is not None
    assert _match(video, "play.library") is not None
    # 链接那一路也不做模糊：随机串里总能找到"差一个字母"的东西
    assert _match(video, "mediahob") is None


async def test_link_url_matches_words_as_they_appear_in_the_url():
    """URL 那一路按"地址里就这么写的词"来匹配。

    `cs101` 在 URL 里是一个词（没拆它），所以敲 `cs101` 能中；分开敲
    `cs 101` 里的 `101` 落在词中间，按上面那条规矩不算 —— 这是为了挡住
    随机串付的代价，而真要找这门课的东西，资源名和模块名那几路都在。
    """
    page = _File("Syllabus", url="https://canvas.example.edu/courses/cs101/syllabus")
    assert _match(page, "cs101") is not None
    assert _match(page, "example syllabus") is not None
    assert _match(page, "cs 101") is None


async def test_every_term_must_land_somewhere():
    """项之间是并且。少一项都不算命中 —— 否则搜两个词会出来一大堆只中一个的。"""
    f = _File("Week 3 Slides.pdf")
    assert _match(f, "week slides") is not None
    assert _match(f, "week 3 quiz") is None


async def test_terms_may_land_on_different_fields():
    """"week 3" 中模块名、"slides" 中文件名 —— 这是最常见的情形，
    要求两项落在同一个字段上反而会把它排除掉。"""
    f = _File("slides.pdf", sources=[_Src(SourceKind.MODULE, container="Week 3")])
    assert _match(f, "week 3 slides") is not None


async def test_quoted_phrase_turns_off_fuzzy():
    f = _File("Problem Set 1.pdf")
    assert _match(f, "problem sett") is not None  # 不加引号容错
    assert _match(f, '"problem sett"') is None  # 加了引号就要精确


async def test_negation_excludes():
    solution = _File("PS1 solutions.pdf")
    handout = _File("PS1 handout.pdf")
    assert _match(solution, "ps1 -solutions") is None
    assert _match(handout, "ps1 -solutions") is not None


async def test_field_prefix_limits_where_to_look():
    f = _File("l3.pdf", folder="course files/Lectures/Week 3")
    assert _match(f, "folder:lectures") is not None
    # 限定了字段就不再看别处：文件名里有 l3，但文件夹里没有 "handout"
    assert _match(f, "folder:handout") is None
    assert _match(f, "name:lectures") is None


async def test_unknown_prefix_is_searched_literally():
    """`10:30` 不是字段限定，是用户真的在搜这个。认不出的前缀不能报错。"""
    f = _File("Office hours 10:30.pdf")
    assert _match(f, "10:30") is not None


async def test_hits_explain_matches_outside_the_name():
    f = _File(
        "l3.pdf",
        folder="course files/Lectures/Week 3",
        sources=[_Src(SourceKind.PAGE, container="Week 3", title="Lecture Notes")],
    )
    m = _match(f, "lecture notes")
    assert m is not None
    assert (search.PAGE, "Lecture Notes") in [(h.field, h.text) for h in m.hits]

    # 名字上的命中不返回说明：名字本来就在行上摆着
    assert _match(_File("Lecture Notes.pdf"), "lecture notes").hits == []


async def test_name_match_scores_higher_than_module_match():
    """搜 "week 3" 时，叫 `Week 3 Slides.pdf` 的必须排在"只是挂在 Week 3 下"的前面。"""
    named = _File("Week 3 Slides.pdf")
    inside = _File("random.pdf", sources=[_Src(SourceKind.MODULE, container="Week 3")])
    assert _match(named, "week 3").score > _match(inside, "week 3").score


async def test_repeated_source_does_not_inflate_score():
    """同一个页面被多个模块引用时，库里是好几条一模一样的来源。
    去重之后分数不会被"挂得多"这件事带偏。"""
    once = _File("a.pdf", sources=[_Src(SourceKind.PAGE, "Week 3", "Lecture Notes")])
    many = _File("a.pdf", sources=[_Src(SourceKind.PAGE, "Week 3", "Lecture Notes")] * 10)
    assert _match(once, "week 3").score == _match(many, "week 3").score


async def test_blank_query_means_no_filtering():
    assert search.parse_query("") == []
    assert search.parse_query("   ") == []
    # 只敲标点搜不出任何东西来，那就当没搜 —— 总比一条不给好
    assert search.parse_query("???") == []


# ---------- 接口 ----------


@pytest.fixture
async def course(db):
    for row in (await db.execute(select(Course))).scalars().all():
        await db.delete(row)
    await db.flush()
    c = Course(canvas_id=6200, name="SEARCH101", course_code="SEARCH101", sync_enabled=True)
    db.add(c)
    await db.flush()

    def _f(key, name, **kw):
        # 链接没有 canvas file id，用 0 —— 身份是 resource_key
        kw.setdefault("canvas_id", abs(hash(key)) % 10**8)
        return CourseFile(
            course_id=c.id,
            resource_key=key,
            display_name=name,
            filename=name,
            display_date=datetime(2026, 3, 10, tzinfo=UTC),
            **kw,
        )

    # 教授把第三周的东西挂在一个页面里，文件本身只叫 l3.pdf
    cryptic = _f("file:1", "l3.pdf")
    named = _f("file:2", "Week 3 Slides.pdf")
    other = _f("file:3", "Syllabus.pdf")
    recording = _f(
        "link:4", "Recording", medium=Medium.LINK, canvas_id=0, external_url="https://youtu.be/x"
    )
    db.add_all([cryptic, named, other, recording])
    await db.flush()
    db.add_all(
        [
            FileSource(
                file_id=cryptic.id,
                course_id=c.id,
                source_kind=SourceKind.PAGE,
                source_key="p1",
                container_name="Week 3",
                item_title="Lecture Notes",
            ),
            FileSource(
                file_id=named.id,
                course_id=c.id,
                source_kind=SourceKind.FILES,
                source_key="f1",
                container_name="course files/Lectures",
            ),
            FileSource(
                file_id=other.id,
                course_id=c.id,
                source_kind=SourceKind.SYLLABUS,
                source_key="syl",
                container_name="Syllabus",
            ),
        ]
    )
    await db.commit()
    return c


async def _search(client, course_id, q, extra=""):
    url = f"/api/v1/courses/{course_id}/files?{urlencode({'q': q})}"
    rows = (await client.get(f"{url}&{extra}" if extra else url)).json()
    return [r["display_name"] for r in rows]


async def test_search_reaches_page_title(client, course):
    got = await _search(client, course.id, "lecture notes")
    assert got == ["l3.pdf"]


async def test_search_reaches_folder_path(client, course):
    assert await _search(client, course.id, "folder:lectures") == ["Week 3 Slides.pdf"]


async def test_search_reaches_link_url(client, course):
    assert await _search(client, course.id, "youtu.be") == ["Recording"]


async def test_relevance_sort_puts_name_match_first(client, course):
    got = await _search(client, course.id, "week 3", extra="sort_by=relevance")
    assert got == ["Week 3 Slides.pdf", "l3.pdf"]


async def test_hits_come_back_with_the_row(client, course):
    rows = (
        await client.get(f"/api/v1/courses/{course.id}/files", params={"q": "lecture notes"})
    ).json()
    assert rows[0]["match_hits"] == [{"field": "page", "text": "Lecture Notes"}]
    assert rows[0]["match_score"] > 0


async def test_search_combines_with_filters(client, course):
    # 搜索和筛选叠加，不是互相覆盖
    assert await _search(client, course.id, "week 3", extra="medium=link") == []
    assert await _search(client, course.id, "recording", extra="medium=link") == ["Recording"]
