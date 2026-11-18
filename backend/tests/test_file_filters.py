"""文件列表的筛选。

两条规矩：

1. **每个轴各自独立。** 一个文件同时挂在好几个地方是常态，所以"板块=Syllabus"
   和"容器=Week 3"不要求命中同一条来源 —— 要求同一条的话这两个条件一起选
   几乎必然一个都选不出来，而用户的意思显然是两个都关心。
2. **日期按显示的那个日期筛**，和列表里看到的、排序用的是同一个值。没有日期的
   资源在任何范围里都不命中：它压根没有日期可比。
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile, FileSource, Medium, SourceKind
from app.services.file_organizer import organize_course_files


def _file(course_id, key, name, *, medium=Medium.FILE, url=None, date=None):
    return CourseFile(
        course_id=course_id,
        canvas_id=0 if medium == Medium.LINK else abs(hash(key)) % 10**8,
        resource_key=key,
        display_name=name,
        filename=name,
        medium=medium,
        external_url=url,
        display_date=date,
    )


@pytest.fixture
async def course(db):
    for row in (await db.execute(select(Course))).scalars().all():
        await db.delete(row)
    await db.flush()
    c = Course(canvas_id=6100, name="FILTER101", course_code="FILTER101", sync_enabled=True)
    db.add(c)
    await db.flush()

    # 三份资料，位置和类型各不相同
    slides = _file(c.id, "file:1", "week3.pdf", date=datetime(2026, 3, 10, tzinfo=UTC))
    notes = _file(c.id, "file:2", "notes.docx", date=datetime(2026, 5, 20, tzinfo=UTC))
    video = _file(
        c.id,
        "link:3",
        "Lecture recording",
        medium=Medium.LINK,
        url="https://youtu.be/abc",
        date=None,
    )
    db.add_all([slides, notes, video])
    await db.flush()

    db.add_all(
        [
            # 幻灯片同时挂在模块和课程大纲下 —— 两个轴各自独立就是为它设计的
            FileSource(
                file_id=slides.id,
                course_id=c.id,
                source_kind=SourceKind.MODULE,
                source_key="m1",
                container_name="Week 3",
            ),
            FileSource(
                file_id=slides.id,
                course_id=c.id,
                source_kind=SourceKind.SYLLABUS,
                source_key="syl",
                container_name="Syllabus",
            ),
            FileSource(
                file_id=notes.id,
                course_id=c.id,
                source_kind=SourceKind.ANNOUNCEMENT,
                source_key="a1",
                container_name="Welcome",
            ),
            FileSource(
                file_id=video.id,
                course_id=c.id,
                source_kind=SourceKind.MODULE,
                source_key="m1",
                container_name="Week 3",
            ),
        ]
    )
    await db.commit()
    # 类型筛选查的是自动整理建出来的 `type:` 分组，所以得先跑一次
    await organize_course_files(db, c.id)
    return c


async def _names(client, course_id, query=""):
    rows = (await client.get(f"/api/v1/courses/{course_id}/files?{query}")).json()
    return sorted(r["display_name"] for r in rows)


async def test_no_filter_returns_everything(client, course):
    assert await _names(client, course.id) == ["Lecture recording", "notes.docx", "week3.pdf"]


async def test_medium(client, course):
    assert await _names(client, course.id, "medium=link") == ["Lecture recording"]
    assert await _names(client, course.id, "medium=file") == ["notes.docx", "week3.pdf"]


async def test_board_matches_any_source(client, course):
    # 幻灯片的主来源是模块，但它也出现在大纲里 —— 按大纲筛必须找得到
    assert await _names(client, course.id, "board=syllabus") == ["week3.pdf"]
    assert await _names(client, course.id, "board=modules_pages") == [
        "Lecture recording",
        "week3.pdf",
    ]


async def test_multiple_values_on_one_axis_are_or(client, course):
    got = await _names(client, course.id, "board=syllabus&board=announcements")
    assert got == ["notes.docx", "week3.pdf"]


async def test_axes_are_independent_not_same_source(client, course):
    """板块选大纲、容器选 Week 3 —— 幻灯片两条来源各中一个，算命中。

    换成 join 写法的话这里会是空的：没有任何**一条**来源同时满足两个条件。
    """
    got = await _names(client, course.id, "board=syllabus&container=Week 3")
    assert got == ["week3.pdf"]


async def test_container(client, course):
    assert await _names(client, course.id, "container=Welcome") == ["notes.docx"]


async def test_kind(client, course):
    assert await _names(client, course.id, "kind=pdf") == ["week3.pdf"]
    assert await _names(client, course.id, "kind=pdf&kind=docs") == ["notes.docx", "week3.pdf"]
    # 链接按域名分类，和文件的扩展名不共用一套 key
    assert await _names(client, course.id, "kind=link_video") == ["Lecture recording"]


async def test_date_range_uses_display_date_and_drops_undated(client, course):
    both = await _names(client, course.id, "date_from=2026-01-01T00:00:00Z")
    assert both == ["notes.docx", "week3.pdf"]  # 没有日期的链接不在任何范围里

    only_march = await _names(
        client, course.id, "date_from=2026-03-01T00:00:00Z&date_to=2026-03-31T23:59:59Z"
    )
    assert only_march == ["week3.pdf"]


async def test_date_bounds_are_instants_not_local_dates(client, course):
    """边界是时刻。界面把"用户所选时区里那一天的起止"换算好再传，
    所以同一个日期在不同时区下会框住不同的资源 —— 这里验证换算过的时刻生效。"""
    # 3/10 00:00 UTC 那一条：上界卡在它之前一秒就不该出现
    assert await _names(client, course.id, "date_to=2026-03-09T23:59:59Z") == []
    assert await _names(client, course.id, "date_to=2026-03-10T00:00:00Z") == ["week3.pdf"]


# ---------- 侧边栏跟着筛选走 ----------
#
# 计数和点进去看到的东西必须是一回事。边栏写着"Lectures 28"、点进去是
# "没有资源符合筛选条件"，那个 28 就是个假数字，整个边栏都没法信了。


async def test_source_tree_follows_the_filter(client, course):
    full = (await client.get(f"/api/v1/courses/{course.id}/sources")).json()
    assert {n["name"]: n["file_count"] for n in full} == {
        "Announcements": 1,
        "Syllabus": 1,
        "Modules & Pages": 2,
    }

    # 只看链接：模块下只剩那条录播，另外两个板块整个消失（空筐不是导航）
    only_links = (await client.get(f"/api/v1/courses/{course.id}/sources?medium=link")).json()
    assert {n["name"]: n["file_count"] for n in only_links} == {"Modules & Pages": 1}


async def test_type_groups_follow_the_filter(client, course):
    url = f"/api/v1/courses/{course.id}/groups?dimension=type"
    full = {g["name"]: g["file_count"] for g in (await client.get(url)).json()}
    assert full == {"PDF": 1, "Docs": 1, "Video": 1}

    # 按板块筛之后，只剩那个板块里有的类型
    scoped = {
        g["name"]: g["file_count"]
        for g in (await client.get(f"{url}&board=syllabus")).json()
    }
    assert scoped == {"PDF": 1}


async def test_user_groups_keep_showing_but_counts_follow_the_filter(client, db, course):
    """自动分组被筛空就不显示，用户自己建的组照常列出来。

    区别在于那是谁建的东西：自动分组就是"一堆文件"，空筐没有意义；
    用户建的组因为一次临时筛选从边栏消失，只会让人以为组被删了。
    """
    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    pdf = next(f for f in files if f["display_name"] == "week3.pdf")
    group = (
        await client.post(f"/api/v1/courses/{course.id}/user-groups", json={"name": "考试"})
    ).json()
    await client.post(f"/api/v1/user-groups/{group['id']}/files", json={"file_ids": [pdf["id"]]})

    listed = (await client.get(f"/api/v1/courses/{course.id}/user-groups")).json()
    assert {g["name"]: g["file_count"] for g in listed if not g["is_starred_group"]} == {"考试": 1}

    # 筛成链接之后这个组里一个都不剩 —— 组还在，数字变成 0
    filtered = (
        await client.get(f"/api/v1/courses/{course.id}/user-groups?medium=link")
    ).json()
    assert {g["name"]: g["file_count"] for g in filtered if not g["is_starred_group"]} == {
        "考试": 0
    }


async def test_filter_panel_can_expand_every_board(client, course):
    """筛选面板要能按容器筛**每一个板块**，不只是侧边栏展开的那两个。

    侧边栏只展开 Canvas Files 和 Modules & Pages：其余板块的"容器"就是一条条
    公告、一份份作业，几百个平铺节点当不了导航。但筛选面板是勾选不是导航，
    而且实测每门课这些容器加起来最多三十来个 —— 不给的话，资料全在课程大纲里的
    课（资料全在大纲里的那种）打开面板就是"来源：没有可选项"。
    """
    default = (await client.get(f"/api/v1/courses/{course.id}/sources")).json()
    assert {n["name"]: [c["name"] for c in n["children"]] for n in default} == {
        "Announcements": [],
        "Syllabus": [],
        "Modules & Pages": ["Week 3"],
    }

    expanded = (await client.get(f"/api/v1/courses/{course.id}/sources?expand=all")).json()
    assert {n["name"]: [c["name"] for c in n["children"]] for n in expanded} == {
        "Announcements": ["Welcome"],
        "Syllabus": ["Syllabus"],
        "Modules & Pages": ["Week 3"],
    }

    # 展开出来的容器拿去筛必须真的能用
    hit = (await client.get(f"/api/v1/courses/{course.id}/files?container=Welcome")).json()
    assert [f["display_name"] for f in hit] == ["notes.docx"]
