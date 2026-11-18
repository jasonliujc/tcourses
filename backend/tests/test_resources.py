"""全局资源页：跨课程列表、星标、「最新」。

这里钉住的核心是**「我还没看过」的判定**，因为它是唯一一处凭空多出来的状态：

1. 按课程记水位线 —— 用户常常是在课程页看文件的，那里看完的东西不能还堵在
   全局「更新」里。
2. 没有水位线的课一律算"没有更新" —— 那说明它还没同步完过一轮，宁可少报，
   也不能把整个库报成新的。
3. 专门点开过某一份（下载 / 打开 / 看详情）就单独算它看过；**点开之后它又被
   Canvas 改了，就重新算没看过** —— 判定比的是时刻，不是布尔值。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile, FileSource, Medium, SourceKind
from app.models.userdata import CourseViewState, UserFile
from app.services import userdata

# 以**真实的现在**为基准，而不是一个写死的日期：标记"看过"用的是服务器当下的
# 时刻，写死的日期会让所有 seen_at 都比库里的时间新，"看过之后又被改了"
# 这条规则就永远测不出来
NOW = datetime.now(UTC) - timedelta(minutes=1)
LAST_WEEK = NOW - timedelta(days=7)
YESTERDAY = NOW - timedelta(days=1)
TOMORROW = NOW + timedelta(days=1)


def _iso(when: datetime) -> str:
    return when.astimezone(UTC).isoformat().replace("+00:00", "Z")


@pytest.fixture
async def library(db):
    """两门在同步的课 + 一门没在同步的。

    A 有水位线（昨天看过），B 的水位线是"上周"，C 压根没有水位线 ——
    三种情况正好覆盖判定的三条规则。
    """
    for row in (await db.execute(select(Course))).scalars().all():
        await db.delete(row)
    await db.flush()

    a = Course(canvas_id=9001, name="AAA101", course_code="AAA101", sync_enabled=True)
    b = Course(canvas_id=9002, name="BBB202", course_code="BBB202", sync_enabled=True)
    off = Course(canvas_id=9003, name="OFF303", course_code="OFF303", sync_enabled=False)
    db.add_all([a, b, off])
    await db.flush()

    def _f(course, key, name, *, first_seen, updated=None, medium=Medium.FILE, **kw):
        return CourseFile(
            course_id=course.id,
            canvas_id=0 if medium == Medium.LINK else abs(hash(key)) % 10**8,
            resource_key=key,
            display_name=name,
            filename=name,
            medium=medium,
            first_seen_at=first_seen,
            canvas_updated_at=updated,
            display_date=updated or first_seen,
            **kw,
        )

    files = {
        # A：一份老的（水位线之前）、一份今天新收的、一份今天被改过的老文件
        "a_old": _f(a, "file:1", "a-old.pdf", first_seen=LAST_WEEK, updated=LAST_WEEK),
        "a_new": _f(a, "file:2", "a-new.pdf", first_seen=NOW, updated=None),
        "a_changed": _f(a, "file:3", "a-changed.pdf", first_seen=LAST_WEEK, updated=NOW),
        # B：水位线在上周，所以昨天这份也算没看过
        "b_yesterday": _f(b, "file:4", "b-yesterday.pdf", first_seen=YESTERDAY),
        # B：一个外部链接，载体轴用它
        "b_link": _f(
            b,
            "link:5",
            "B recording",
            first_seen=YESTERDAY,
            medium=Medium.LINK,
            external_url="https://youtu.be/b",
        ),
        # C 没在同步 —— 它的东西在任何全局列表里都不该出现
        "off": _f(off, "file:6", "off-limits.pdf", first_seen=NOW),
    }
    db.add_all(list(files.values()))
    await db.flush()

    db.add_all(
        [
            FileSource(
                file_id=files["a_new"].id,
                course_id=a.id,
                source_kind=SourceKind.PAGE,
                source_key="p1",
                container_name="Week 3",
                item_title="Lecture Notes",
            ),
            FileSource(
                file_id=files["b_yesterday"].id,
                course_id=b.id,
                source_kind=SourceKind.SYLLABUS,
                source_key="syl",
                container_name="Syllabus",
            ),
        ]
    )
    # A 昨天看过，B 上周看过，C（没在同步）没有水位线
    db.add_all(
        [
            CourseViewState(course_id=a.id, seen_at=YESTERDAY),
            CourseViewState(course_id=b.id, seen_at=LAST_WEEK),
        ]
    )
    await db.commit()
    return {"a": a, "b": b, "off": off, "files": files}


async def _names(client, query=""):
    rows = (await client.get(f"/api/v1/resources?{query}")).json()
    return sorted(r["display_name"] for r in rows)


# ---------- 列表本身 ----------


async def test_lists_across_courses_and_says_which_course(client, library):
    rows = (await client.get("/api/v1/resources")).json()
    by_name = {r["display_name"]: r for r in rows}
    assert by_name["a-new.pdf"]["course_code"] == "AAA101"
    assert by_name["b-yesterday.pdf"]["course_code"] == "BBB202"
    # changed_at = max(第一次收到, Canvas 更新时间)：这一份上周才第一次收到，
    # 但今天被改过，所以它的"最后变动"是今天那个时刻
    assert by_name["a-changed.pdf"]["changed_at"][:16] == _iso(NOW)[:16]
    # 反过来：新收到的那份没有 Canvas 更新时间，取的就是"第一次收到"
    assert by_name["a-new.pdf"]["changed_at"][:16] == _iso(NOW)[:16]


async def test_courses_not_being_synced_are_invisible(client, library):
    assert "off-limits.pdf" not in await _names(client)
    buckets = (await client.get("/api/v1/resources/courses")).json()
    assert [b["course_code"] for b in buckets] == ["AAA101", "BBB202"]


async def test_sorts_by_change_time_by_default(client, library):
    rows = (await client.get("/api/v1/resources?limit=2")).json()
    # 今天变动的两份排在最前（一份新收的、一份被改的），顺序之外先确认就是这两份
    assert sorted(r["display_name"] for r in rows) == ["a-changed.pdf", "a-new.pdf"]


# ---------- 「我还没看过」 ----------


async def test_unseen_respects_the_per_course_watermark(client, library):
    """A 昨天看过 → 只有今天变动的两份算没看过；B 上周看过 → 昨天那两份都算。"""
    assert await _names(client, "unseen=true") == [
        "B recording",
        "a-changed.pdf",
        "a-new.pdf",
        "b-yesterday.pdf",
    ]


async def test_a_course_without_a_watermark_reports_nothing(client, library, db):
    """没有水位线 = 还没同步完过一轮。宁可少报，不能把整个库报成新的。"""
    await db.execute(
        CourseViewState.__table__.delete().where(CourseViewState.course_id == library["a"].id)
    )
    await db.commit()
    got = await _names(client, "unseen=true")
    assert "a-new.pdf" not in got and "a-changed.pdf" not in got


async def test_opening_a_course_clears_its_updates(client, library):
    """在课程页看文件也算看过 —— 这正是水位线按课程记的理由。"""
    await client.post(f"/api/v1/courses/{library['a'].id}/seen")
    got = await _names(client, "unseen=true")
    assert got == ["B recording", "b-yesterday.pdf"]  # A 的全清了，B 的还在


async def test_mark_all_seen_clears_everything(client, library):
    await client.post("/api/v1/resources/seen-all")
    assert await _names(client, "unseen=true") == []


async def test_opening_one_file_marks_only_that_one(client, library):
    file_id = library["files"]["a_new"].id
    await client.post("/api/v1/resources/seen", json={"file_ids": [file_id]})
    got = await _names(client, "unseen=true")
    assert "a-new.pdf" not in got
    assert "a-changed.pdf" in got  # 同一门课里别的还在


async def test_a_file_changed_after_being_opened_counts_again(client, library, db):
    """点开过、之后 Canvas 上又改了 → 重新算没看过。判定比的是时刻。"""
    file = library["files"]["a_new"]
    await client.post("/api/v1/resources/seen", json={"file_ids": [file.id]})
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == file.resource_key))
    ).scalar_one()
    assert entry.seen_at is not None

    row = await db.get(CourseFile, file.id)
    row.canvas_updated_at = TOMORROW
    await db.commit()
    assert "a-new.pdf" in await _names(client, "unseen=true")


async def test_seen_marks_survive_the_unused_entry_cleanup(client, library, db):
    """标记看过会新建一条用户记录。它既没星标也没分组，但不能被当成空行回收 ——
    否则"看过"这件事下一次同步就没了。"""
    file = library["files"]["a_old"]
    await client.post("/api/v1/resources/seen", json={"file_ids": [file.id]})
    entry = (
        await db.execute(select(UserFile).where(UserFile.resource_key == file.resource_key))
    ).scalar_one()
    await userdata.set_starred(db, [await db.get(CourseFile, file.id)], True)
    await userdata.set_starred(db, [await db.get(CourseFile, file.id)], False)
    await db.commit()
    assert (await db.get(UserFile, entry.id)) is not None


# ---------- 时间窗口 ----------


async def test_time_window_ignores_whether_it_was_seen(client, library):
    """「今天 / 本周」问的是"有什么新东西"，不是"有什么我没看"。"""
    await client.post("/api/v1/resources/seen-all")
    today = await _names(client, f"since={_iso(NOW)}")
    assert today == ["a-changed.pdf", "a-new.pdf"]
    week = await _names(client, f"since={_iso(LAST_WEEK + timedelta(hours=1))}")
    assert "b-yesterday.pdf" in week and "a-old.pdf" not in week


async def test_summary_counts_match_the_lists(client, library):
    summary = (
        await client.get(
            "/api/v1/resources/summary",
            params={"today_since": _iso(NOW), "week_since": _iso(LAST_WEEK + timedelta(hours=1))},
        )
    ).json()
    assert summary["unseen"] == 4
    assert summary["today"] == 2
    assert summary["week"] == 4
    assert summary["starred"] == 0
    # 总数只数在同步的课程 —— 没在同步的那门课在界面上压根不存在
    assert summary["total"] == 5


# ---------- 星标 ----------


async def test_star_works_across_courses(client, library):
    ids = [library["files"]["a_old"].id, library["files"]["b_link"].id]
    res = await client.post("/api/v1/resources/star", json={"file_ids": ids, "starred": True})
    assert res.json()["changed"] == 2
    assert await _names(client, "starred=true") == ["B recording", "a-old.pdf"]

    await client.post("/api/v1/resources/star", json={"file_ids": ids[:1], "starred": False})
    assert await _names(client, "starred=true") == ["B recording"]


async def test_starred_keeps_files_deleted_on_canvas(client, library, db):
    """用户收藏过的东西不能因为 Canvas 那边没了就凭空消失，只是不给下载。"""
    file = library["files"]["a_old"]
    await client.post("/api/v1/resources/star", json={"file_ids": [file.id], "starred": True})
    row = await db.get(CourseFile, file.id)
    row.is_deleted_on_canvas = True
    await db.commit()

    assert await _names(client, "starred=true") == ["a-old.pdf"]
    assert "a-old.pdf" not in await _names(client)  # 普通列表里不出现


# ---------- 筛选与搜索 ----------


async def test_course_axis(client, library):
    got = await _names(client, f"course={library['b'].id}")
    assert got == ["B recording", "b-yesterday.pdf"]


async def test_medium_and_board_axes(client, library):
    assert await _names(client, "medium=link") == ["B recording"]
    assert await _names(client, "board=syllabus") == ["b-yesterday.pdf"]


async def test_search_reaches_page_titles_globally(client, library):
    """搜索用的是课程页同一套实现，所以页面标题照样能命中。"""
    assert await _names(client, "q=lecture+notes") == ["a-new.pdf"]


async def test_limit_applies_after_the_search(client, library):
    """limit 的意思是"给我最新的 N 条命中"。先截断再过滤会给出一个既不满 N 条、
    又漏掉更相关结果的列表。"""
    rows = (await client.get("/api/v1/resources?q=a&limit=2")).json()
    assert len(rows) == 2
    assert all("a" in r["display_name"].lower() for r in rows)


async def test_course_buckets_follow_the_filters_but_options_do_not(client, library):
    """侧边栏计数跟着筛选走（写着 3 点进去是 1 的话那个 3 就是假数字），
    筛选面板的可选项不跟 —— 否则取消一个条件之后它就再也选不回来。"""
    buckets = (await client.get("/api/v1/resources/courses?medium=link")).json()
    assert [(b["course_code"], b["file_count"]) for b in buckets] == [("BBB202", 1)]

    options = (await client.get("/api/v1/resources/filters")).json()
    assert [c["course_code"] for c in options["courses"]] == ["AAA101", "BBB202"]
    # 只列库里真的出现过的来源：一个必然筛出空列表的选项不是选项
    assert [b["id"] for b in options["boards"]] == ["syllabus", "modules_pages"]
