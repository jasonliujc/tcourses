"""外部链接：抽取、身份、分类、以及"链接被换掉了"的判定。

教授贴资料不一定上传文件 —— 录播挂 YouTube、讲义放 Google Docs。这些东西
对学生和 PDF 一样重要，但文件收集器完全看不见它们。

这个文件盯住四条：

- **只收页面和作业，不收公告。** 实测公告贡献 74% 的链接，压倒性地是招聘会
  报名、问卷这类行政通知 —— 收进来会把文件库淹掉。
- **身份是规范化之后的 URL。** 同一个链接贴三次是一行加三条来源，和文件一样。
- **锚文本相同、URL 变了 = 链接被换掉了**，这是唯一能识别出"更新"的形状：
  URL 本身就是身份，换一个 URL 技术上等于"删一条 + 加一条"。
- **用户整理过的链接不会因为教授删了一行 HTML 就消失。**
"""

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile, FileSource, Medium, SourceKind
from app.models.system import EventKind, NotificationEvent
from app.services import links, userdata
from app.services.file_collector import (
    CollectedLink,
    CollectedSource,
    link_source_key,
    split_item_ref,
)

# ---------- 规范化与身份 ----------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # 同一个资源的不同写法
        ("http://example.com/a", "https://example.com/a"),
        ("https://EXAMPLE.com/a", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com/a?utm_source=canvas", "https://example.com/a"),
        ("https://example.com/a?x=1&fbclid=zz", "https://example.com/a?x=1"),
        # 视频站的 #t= 是播放位置，不是另一个视频
        ("https://youtu.be/abc#t=120", "https://youtu.be/abc"),
    ],
)
def test_these_are_the_same_link(a, b):
    assert links.normalize(a) == links.normalize(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # 别的站 fragment 可能是真实路由；Google Sheets 的 #gid= 是另一张表
        (
            "https://docs.google.com/spreadsheets/d/1#gid=0",
            "https://docs.google.com/spreadsheets/d/1#gid=7",
        ),
        ("https://example.com/a?x=1", "https://example.com/a?x=2"),
        ("https://example.com/a", "https://example.com/b"),
    ],
)
def test_these_are_different_links(a, b):
    """合并错了是永久把两个资源当成一个，比多留一条重复记录严重得多。"""
    assert links.normalize(a) != links.normalize(b)


@pytest.mark.parametrize(
    "raw",
    ["mailto:prof@example.com", "#section-2", "javascript:void(0)", "/courses/1/pages/x", ""],
)
def test_non_http_links_are_dropped(raw):
    assert links.normalize(raw) is None


def test_canvas_own_links_are_not_collected():
    """指回 Canvas 的链接不收 —— 那些页面和文件本来就从别的路径收进来了，
    收进来只会让同一个东西在界面上出现两次。"""
    for url in (
        "https://canvas.example.edu/courses/1/pages/week-1",
        "https://school.instructure.com/courses/2",
        "https://a1234.canvas-user-content.com/files/9",
    ):
        assert links.is_canvas_own(links.normalize(url), "https://canvas.example.edu")
    assert not links.is_canvas_own(
        links.normalize("https://youtube.com/watch?v=x"), "https://canvas.example.edu"
    )


@pytest.mark.parametrize(
    ("url", "kind", "label"),
    [
        ("https://www.youtube.com/watch?v=x", "link_video", "Video"),
        ("https://youtu.be/x", "link_video", "Video"),
        ("https://example.zoom.us/j/123", "link_video", "Video"),
        ("https://docs.google.com/document/d/1", "link_docs", "Docs"),
        ("https://www.overleaf.com/read/abc", "link_docs", "Docs"),
        # 94% 的链接落在这里 —— 350 个域名的长尾，细分下去没有意义
        ("https://github.com/user/repo", "link_other", "Others"),
        ("https://forms.office.com/r/abc", "link_other", "Others"),
        (None, "link_other", "Others"),
    ],
)
def test_link_kinds(url, kind, label):
    assert links.link_kind(url) == (kind, label)


# ---------- 从正文里抽 ----------


def test_extract_takes_the_anchor_text_as_the_title():
    body = '<p>看这个 <a href="https://youtu.be/abc">Lecture 3 录播</a></p>'
    (link,) = links.extract(body, "https://canvas.example.edu")
    assert link.title == "Lecture 3 录播"
    assert link.url == "https://youtu.be/abc"
    assert link.resource_key.startswith("link:")


def test_anchor_text_falls_back_to_the_url():
    """锚文本是图片或者空的时候也得有个能认的标题，不能显示成空白。"""
    body = '<a href="https://example.com/files/slides.pdf"><img src="x.png"></a>'
    (link,) = links.extract(body, "")
    assert link.title == "example.com/files/slides.pdf"


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        # 教授把地址直接粘进正文，Canvas 自动变成 <a href="X">X</a>。
        # 实测 817 条链接里 206 条（25%）是这样 —— 原样显示满屏都是 https://
        ("https://example.zoom.us/j/8123456", "example.zoom.us/j/8123456"),
        ("http://www.arts.example.edu/advising", "arts.example.edu/advising"),
        ("https://example.com/", "example.com"),
        # 富文本里 URL 常被标签切开，去掉标签之后成了带空格的一串
        ("https:// example .com/ x", "example.com/x"),
        # 有真文字的时候原样保留，别自作聪明
        ("Lecture 3 录播", "Lecture 3 录播"),
        ("Click here for the syllabus", "Click here for the syllabus"),
    ],
)
def test_bare_url_anchor_text_is_tidied_not_invented(anchor, expected):
    """没有文字可用时，地址本身就是唯一的信息 —— 不编标题，只把它排版干净。

    尤其不能截断尾巴：`.../j/8223655` 里的会议号才是区分它和另外五条
    zoom 链接的唯一依据。"""
    body = f'<a href="https://example.com/x">{anchor}</a>'
    (link,) = links.extract(body, "")
    assert link.title == expected


def test_same_url_twice_in_one_body_is_one_link():
    body = (
        '<a href="https://youtu.be/abc">录播</a>'
        '<a href="https://youtu.be/abc?utm_source=canvas">同一个录播</a>'
    )
    assert len(links.extract(body, "")) == 1


def test_nested_tags_in_anchor_text_are_stripped():
    body = '<a href="https://example.com/x"><strong>重要</strong> 阅读</a>'
    (link,) = links.extract(body, "")
    assert link.title == "重要 阅读"


def test_source_key_round_trips_the_item():
    """判定"链接被换掉"要能从 key 切回条目，所以这个形状是硬约定。"""
    key = link_source_key("page:week-1", "link:abcdef")
    assert split_item_ref(key) == "page:week-1"


# ---------- 落库与变更判定 ----------


async def _course(db, canvas_id: int) -> Course:
    existing = (
        await db.execute(select(Course).where(Course.canvas_id == canvas_id))
    ).scalar_one_or_none()
    if existing:
        return existing
    course = Course(canvas_id=canvas_id, name=f"链接测试 {canvas_id}")
    db.add(course)
    await db.commit()
    return course


def _link(url: str, title: str, item_ref: str = "page:week-1") -> CollectedLink:
    normalized = links.normalize(url)
    key = links.resource_key(normalized)
    return CollectedLink(
        resource_key=key,
        url=url,
        title=title,
        sources=[
            CollectedSource(
                source_kind=SourceKind.PAGE,
                source_key=link_source_key(item_ref, key),
                container_name="Week 1",
                item_title=title,
                label="Week 1 · 课程主页",
            )
        ],
    )


class _Collector:
    """`_sync_course_links` 只用到这几个字段，不必造一个真的收集器。"""

    def __init__(self, links_by_key):
        self.links = links_by_key
        self.truncated: list[str] = []
        self.skip_endpoints: set[str] = set()


async def _sync(db, course, *collected, cold_start=False):
    from app.integrations.canvas.sync import CanvasSyncService

    service = CanvasSyncService(db, client=None)
    await service._sync_course_links(
        course, _Collector({link.resource_key: link for link in collected}), cold_start
    )
    return service


async def _link_rows(db, course) -> list[CourseFile]:
    return list(
        (
            await db.execute(
                select(CourseFile)
                .where(CourseFile.course_id == course.id, CourseFile.medium == Medium.LINK)
                .order_by(CourseFile.id)
            )
        )
        .scalars()
        .all()
    )


async def _events(db, kind: EventKind, course: Course) -> list[NotificationEvent]:
    """按课程过滤 —— 测试库是共用的，不限定就会看到别的用例造的事件。"""
    return list(
        (
            await db.execute(
                select(NotificationEvent).where(
                    NotificationEvent.kind == kind,
                    NotificationEvent.course_id == course.id,
                )
            )
        )
        .scalars()
        .all()
    )


async def test_links_land_as_rows_with_medium_link(client, db):
    course = await _course(db, 8001)
    await _sync(db, course, _link("https://youtu.be/abc", "Lecture 3 录播"), cold_start=True)

    (row,) = await _link_rows(db, course)
    assert row.medium == Medium.LINK
    assert row.display_name == "Lecture 3 录播"
    assert row.external_url == "https://youtu.be/abc"
    # 身份不靠 canvas_id —— 链接压根没有
    assert row.resource_key.startswith("link:")


async def test_replacing_the_target_reports_an_update_not_a_new_link(client, db):
    """**核心用例。** 教授把「Lecture 3 录播」指到了另一个地址。

    URL 就是身份，所以技术上这是"删一条 + 加一条"。但用户要看到的是
    "这个链接更新了" —— 靠同一个条目里锚文本没变来认出这一点。
    """
    course = await _course(db, 8002)
    await _sync(db, course, _link("https://youtu.be/old", "Lecture 3 录播"), cold_start=True)

    await _sync(db, course, _link("https://youtu.be/new", "Lecture 3 录播"))

    updates = await _events(db, EventKind.UPDATED_LINK, course)
    assert [e.title for e in updates] == ["Lecture 3 录播"]
    assert updates[0].payload["url"] == "https://youtu.be/new"
    # 换了目标就不该再报一条"新链接"：同一件事只说一遍
    assert await _events(db, EventKind.NEW_LINK, course) == []


async def test_a_genuinely_new_link_is_reported_as_new(client, db):
    course = await _course(db, 8003)
    await _sync(db, course, _link("https://youtu.be/a", "第一讲"), cold_start=True)

    await _sync(
        db, course, _link("https://youtu.be/a", "第一讲"), _link("https://youtu.be/b", "第二讲")
    )

    assert [e.title for e in await _events(db, EventKind.NEW_LINK, course)] == ["第二讲"]
    assert await _events(db, EventKind.UPDATED_LINK, course) == []


async def test_same_anchor_in_a_different_item_is_not_an_update(client, db):
    """两个页面各有一个「课程大纲」，指向不同 —— 那是两个链接，不是谁换了谁。"""
    course = await _course(db, 8004)
    await _sync(
        db, course, _link("https://a.com/x", "课程大纲", "page:week-1"), cold_start=True
    )

    await _sync(
        db,
        course,
        _link("https://a.com/x", "课程大纲", "page:week-1"),
        _link("https://b.com/y", "课程大纲", "page:week-2"),
    )

    assert await _events(db, EventKind.UPDATED_LINK, course) == []
    assert len(await _link_rows(db, course)) == 2


async def test_first_sync_stays_quiet(client, db):
    """冷启动只入库不通知，否则用户一进来就被几百条淹没。"""
    course = await _course(db, 8005)
    await _sync(db, course, _link("https://a.com/x", "大纲"), cold_start=True)
    assert await _events(db, EventKind.NEW_LINK, course) == []


async def test_link_removed_from_the_body_disappears(client, db):
    course = await _course(db, 8006)
    await _sync(db, course, _link("https://a.com/x", "旧链接"), cold_start=True)
    assert len(await _link_rows(db, course)) == 1

    await _sync(db, course)  # 教授把它从正文里删了
    assert await _link_rows(db, course) == []


async def test_a_starred_link_is_kept_and_marked_instead_of_deleted(client, db):
    """用户整理过的东西不因为教授改了一行 HTML 就消失 —— 和文件同一条规矩。"""
    course = await _course(db, 8007)
    await _sync(db, course, _link("https://a.com/x", "我要留着的"), cold_start=True)
    (row,) = await _link_rows(db, course)
    await userdata.set_starred(db, [row], True)
    await db.commit()

    await _sync(db, course)

    (still,) = await _link_rows(db, course)
    assert still.is_deleted_on_canvas is True


async def test_a_truncated_round_never_deletes_links(client, db):
    """页面抓取有预算上限。被截断的那轮里"没扫到"和"不存在了"分不开，
    照删会把好好的链接抹掉。"""
    from app.integrations.canvas.sync import CanvasSyncService

    course = await _course(db, 8008)
    await _sync(db, course, _link("https://a.com/x", "还在的"), cold_start=True)

    collector = _Collector({})
    collector.truncated = ["模块页面有 900 个需要抓取，只扫了前 300 个"]
    service = CanvasSyncService(db, client=None)
    await service._sync_course_links(course, collector, cold_start=False)

    assert len(await _link_rows(db, course)) == 1


async def test_the_same_link_in_two_places_is_one_row_with_two_sources(client, db):
    course = await _course(db, 8009)
    both = _link("https://a.com/x", "大纲", "page:week-1")
    both.sources.append(
        CollectedSource(
            source_kind=SourceKind.ASSIGNMENT,
            source_key=link_source_key("item:99", both.resource_key),
            container_name="作业 1",
            item_title="大纲",
            label="09/01 · 作业 1",
        )
    )
    await _sync(db, course, both, cold_start=True)

    (row,) = await _link_rows(db, course)
    sources = (
        (await db.execute(select(FileSource).where(FileSource.file_id == row.id)))
        .scalars()
        .all()
    )
    assert len(sources) == 2


async def test_links_show_up_in_the_file_list_with_their_type(client, db):
    course = await _course(db, 8010)
    await _sync(db, course, _link("https://youtu.be/abc", "Lecture 3 录播"), cold_start=True)

    (row,) = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    assert row["medium"] == "link"
    assert row["kind_label"] == "Video"
    assert row["external_url"] == "https://youtu.be/abc"
    # 链接没有 Canvas 文件地址，别拿 canvas_id=0 拼一个出来骗人
    assert row["canvas_url"] is None


async def test_downloading_a_link_says_so_instead_of_failing_weirdly(client, db):
    course = await _course(db, 8011)
    await _sync(db, course, _link("https://a.com/x", "外部资料"), cold_start=True)
    (row,) = await _link_rows(db, course)

    resp = await client.get(f"/api/v1/files/{row.id}/download")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "file_is_link"


async def test_duplicate_anchor_text_never_reports_a_fake_update(client, db):
    """**回归。** 一个页面上出现两次「Click here」指向不同地址是常态，不是例外
    （真实数据里一门课就有十几处：here / LinkedIn / academic unit …）。

    锚文本重复时根本分不清"谁换成了谁"，硬猜的结果是每轮同步都报一条假的
    "链接已更新" —— 那正是这个功能最该避免的噪声。分不清就别猜。
    """
    course = await _course(db, 8012)
    a = _link("https://a.com/one", "Click here")
    b = _link("https://b.com/two", "Click here")
    await _sync(db, course, a, b, cold_start=True)

    # 第二轮：两条都还在，只是收集顺序反了 —— 不该报任何更新
    await _sync(db, course, b, a)
    assert await _events(db, EventKind.UPDATED_LINK, course) == []

    # 就算其中一条真换了地址，也依然分不清是谁换的，仍然不猜
    await _sync(db, course, a, _link("https://c.com/three", "Click here"))
    assert await _events(db, EventKind.UPDATED_LINK, course) == []


async def test_a_unique_anchor_still_reports_the_update(client, db):
    """去歧义不能把正常情况也一起挡掉。"""
    course = await _course(db, 8013)
    await _sync(
        db,
        course,
        _link("https://a.com/one", "Click here"),
        _link("https://b.com/two", "Click here"),
        _link("https://youtu.be/old", "Lecture 3 录播"),
        cold_start=True,
    )

    await _sync(
        db,
        course,
        _link("https://a.com/one", "Click here"),
        _link("https://b.com/two", "Click here"),
        _link("https://youtu.be/new", "Lecture 3 录播"),
    )

    assert [e.title for e in await _events(db, EventKind.UPDATED_LINK, course)] == [
        "Lecture 3 录播"
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # SharePoint 把资源类型编在路径里，光看域名会把一门课的录播全归成 Docs ——
        # 而录播恰恰是学生最常回去点的东西（实测讲课录像就是这个形状）
        ("https://example.sharepoint.com/:v:/s/artsci/abc", "Video"),
        ("https://example.sharepoint.com/:w:/s/artsci/abc", "Docs"),
        ("https://example.sharepoint.com/sites/artsci/x", "Docs"),
    ],
)
def test_sharepoint_video_is_not_filed_as_docs(url, expected):
    assert links.link_kind(url)[1] == expected


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Arts &amp; Science website", "Arts & Science website"),
        ("final exam schedule&nbsp;", "final exam schedule"),
        ("Rules &gt; Term Work", "Rules > Term Work"),
    ],
)
def test_html_entities_in_anchor_text_are_decoded(raw, clean):
    """不解实体的话标题直接显示成「Arts &amp; Science website」。真实数据里 28 条。"""
    (link,) = links.extract(f'<a href="https://example.com/x">{raw}</a>', "")
    assert link.title == clean


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # 实测：同一个讲课录像在库里存了两行，教授一处贴短链一处贴长链
        ("https://youtu.be/rmlCupDR4bg?si=0k4pd", "https://www.youtube.com/watch?v=rmlCupDR4bg"),
        ("https://youtube.com/watch?v=X", "https://www.youtube.com/watch?v=X"),
        ("https://m.youtube.com/watch?v=X", "https://www.youtube.com/watch?v=X"),
    ],
)
def test_youtube_short_and_long_form_are_the_same_video(a, b):
    assert links.normalize(a) == links.normalize(b)


def test_youtube_canonicalisation_does_not_leak_to_other_hosts():
    """短链改写只对 YouTube 做 —— 它的格式是公开明确的，别的站没这个保证。"""
    assert links.normalize("https://example.com/abc?si=1") != links.normalize(
        "https://example.com/abc"
    )
    assert links.normalize("https://youtu.be/A") != links.normalize("https://youtu.be/B")


def test_entities_in_the_href_are_decoded():
    """Canvas 存的是 HTML，`?v=X&amp;list=Y` 遍地都是。不解的话拿到手的 URL 打不开。"""
    body = '<a href="https://www.youtube.com/watch?v=X&amp;list=Y">录播</a>'
    (link,) = links.extract(body, "")
    assert link.url == "https://www.youtube.com/watch?v=X&list=Y"
    assert "&amp;" not in link.normalized


async def test_sweep_still_runs_when_files_endpoint_is_disabled(client, db):
    """回归：很多学校关掉了 Files 标签页，那个端点会被长期跳过。

    如果把"跳过了某个端点"当成"这轮没扫全"，这些课就**永远**不会清理链接 ——
    改过规范化规则之后旧行全留在库里，同一个视频在界面上出现两次。
    而链接是从页面/作业的富文本里扒的，和 files / modules 端点没有关系。
    """
    from app.integrations.canvas.sync import CanvasSyncService

    course = await _course(db, 8014)
    await _sync(db, course, _link("https://a.com/x", "旧的"), cold_start=True)

    collector = _Collector({})
    collector.skip_endpoints = {"files", "modules"}
    service = CanvasSyncService(db, client=None)
    await service._sync_course_links(course, collector, cold_start=False)

    assert await _link_rows(db, course) == []
