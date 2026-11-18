"""日历与待办。

钉住四件事：

1. **Planner 的数据原样落库**：类型归一、对本人生效的截止、提交状态、Canvas 里的完成标记。
2. **用户的改动和同步数据分开**：同步刷新不冲掉用户改过的日期 / 标题 / 完成；
   条目从 Canvas 上消失又回来，改动还在。
3. **删除只在同步窗口内判定**，而且 Planner 那一路失败时一条都不删。
4. **回写 Canvas 只在开关打开时发生**；写失败了本地改动照常保存。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings as env_settings
from app.integrations.canvas.client import CanvasError
from app.models.canvas_item import CanvasItem
from app.models.course import Course
from app.models.planner import PlannerItem, PlannerOverlay
from app.services import planner, runtime_settings

NOW = datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assignment(pid: int, course: int, title: str, due: datetime, **subs) -> dict:
    return {
        "plannable_id": pid,
        "plannable_type": "assignment",
        "course_id": course,
        "plannable_date": _iso(due),
        "html_url": f"/courses/{course}/assignments/{pid}",
        "new_activity": False,
        "planner_override": None,
        "submissions": {
            "submitted": False,
            "excused": False,
            "graded": False,
            "late": False,
            "missing": False,
            "needs_grading": False,
            "has_feedback": False,
            **subs,
        },
        "plannable": {"id": pid, "title": title, "due_at": _iso(due), "points_possible": 10},
    }


def _quiz(pid: int, course: int, title: str, due: datetime, override=None) -> dict:
    # 不挂作业的测验（问卷）：Planner 里 submissions 是 False，完成只能靠 override
    return {
        "plannable_id": pid,
        "plannable_type": "quiz",
        "course_id": course,
        "plannable_date": _iso(due),
        "html_url": f"/courses/{course}/quizzes/{pid}",
        "planner_override": override,
        "submissions": False,
        "plannable": {"id": pid, "title": title, "due_at": _iso(due), "assignment_id": None},
    }


def _event(pid: int, course: int, title: str, start: datetime) -> dict:
    return {
        "plannable_id": pid,
        "plannable_type": "calendar_event",
        "course_id": course,
        "plannable_date": _iso(start),
        "html_url": f"/calendar?event_id={pid}",
        "submissions": False,
        "plannable": {
            "id": pid,
            "title": title,
            "start_at": _iso(start),
            "end_at": _iso(start + timedelta(hours=2)),
            "all_day": False,
            "location_name": "BA 2230",
        },
    }


class FakeClient:
    base_url = "https://canvas.test"

    def __init__(self, items=None, events=None, colors=None, fail_items=False):
        self.items = items or []
        self.events = events or {}
        self.colors = colors or {}
        self.fail_items = fail_items
        self.planner_calls: list = []
        self.override_calls: list = []

    async def list_planner_items(self, start, end, context_codes=None):
        self.planner_calls.append(context_codes)
        if self.fail_items:
            raise CanvasError("planner 挂了")
        if context_codes:
            wanted = {int(c.removeprefix("course_")) for c in context_codes}
            return [i for i in self.items if i.get("course_id") in wanted]
        return list(self.items)

    async def list_course_events(self, course_canvas_id, start, end):
        return list(self.events.get(course_canvas_id, []))

    async def get_colors(self):
        return self.colors

    async def create_planner_override(self, plannable_type, plannable_id, marked_complete):
        self.override_calls.append(("POST", plannable_type, plannable_id, marked_complete))
        return {"id": 999, "marked_complete": marked_complete}

    async def update_planner_override(self, override_id, marked_complete):
        self.override_calls.append(("PUT", override_id, marked_complete))
        return {"id": override_id, "marked_complete": marked_complete}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


@pytest.fixture
async def courses(db):
    for row in (await db.execute(select(PlannerItem))).scalars().all():
        await db.delete(row)
    for row in (await db.execute(select(PlannerOverlay))).scalars().all():
        await db.delete(row)
    for row in (await db.execute(select(Course))).scalars().all():
        await db.delete(row)
    await db.flush()
    a = Course(canvas_id=7101, name="AAA101", course_code="AAA101", sync_enabled=True)
    b = Course(canvas_id=7102, name="BBB202", course_code="BBB202", sync_enabled=True)
    off = Course(canvas_id=7103, name="OFF303", course_code="OFF303", sync_enabled=False)
    db.add_all([a, b, off])
    await db.commit()
    return {"a": a, "b": b, "off": off}


async def _items(db) -> dict[str, PlannerItem]:
    return {i.item_key: i for i in (await db.execute(select(PlannerItem))).scalars().all()}


# ---------- 落库 ----------


async def test_maps_planner_fields(db, courses):
    due = NOW + timedelta(days=3)
    client = FakeClient(
        items=[
            _assignment(1, 7101, "A1", due, submitted=True, late=True),
            _quiz(2, 7101, "Welcome Survey", due, override={"id": 5, "marked_complete": True}),
            _event(3, 7102, "Office Hours", due),
        ],
        colors={"course_7101": "#254284"},
    )
    result = await planner.sync_planner(db, client)
    await db.commit()
    assert result.items == 3 and result.created == 3

    rows = await _items(db)
    a1 = rows["assignment:1"]
    assert a1.kind == "assignment" and a1.submitted and a1.late
    assert a1.html_url == "https://canvas.test/courses/7101/assignments/1"
    quiz = rows["quiz:2"]
    assert quiz.kind == "quiz" and quiz.canvas_marked_complete and quiz.canvas_override_id == 5
    event = rows["calendar_event:3"]
    assert event.kind == "event" and event.location_name == "BA 2230"
    assert (await db.get(Course, courses["a"].id)).canvas_color == "#254284"


async def test_courses_not_synced_are_skipped(db, courses):
    due = NOW + timedelta(days=1)
    client = FakeClient(items=[_assignment(1, 7103, "不要", due), _assignment(2, 7101, "要", due)])
    await planner.sync_planner(db, client)
    await db.commit()
    assert list(await _items(db)) == ["assignment:2"]


async def test_calendar_events_fill_in_all_day_date_and_series(db, courses):
    """Planner 里的事件没有全天日期和重复系列，按课程查的日历事件里有。"""
    start = NOW + timedelta(days=2)
    client = FakeClient(
        items=[_event(3, 7101, "Midterm", start)],
        events={
            7101: [
                {
                    "id": 3,
                    "title": "Midterm",
                    "start_at": _iso(start),
                    "end_at": _iso(start),
                    "all_day": True,
                    "all_day_date": "2026-10-20",
                    "series_uuid": "s-1",
                    "context_code": "course_7101",
                },
                # Planner 里没有、只在日历里有的事件也要收进来
                {
                    "id": 4,
                    "title": "Review",
                    "start_at": _iso(start),
                    "context_code": "course_7101",
                },
            ]
        },
    )
    result = await planner.sync_planner(db, client)
    await db.commit()
    rows = await _items(db)
    assert rows["calendar_event:3"].all_day_date == "2026-10-20"
    assert rows["calendar_event:3"].series_uuid == "s-1"
    assert "calendar_event:4" in rows
    assert result.events == 2


# ---------- 删除 ----------


async def test_items_gone_from_canvas_are_removed_inside_the_window(db, courses):
    due = NOW + timedelta(days=3)
    await planner.sync_planner(db, FakeClient(items=[_assignment(1, 7101, "A1", due)]))
    await db.commit()
    result = await planner.sync_planner(db, FakeClient(items=[]))
    await db.commit()
    assert result.removed == 1
    assert await _items(db) == {}


async def test_items_outside_the_window_are_left_alone(db, courses):
    """Planner 是按日期范围查的：窗口外的这次没返回，不代表被删了。"""
    far = NOW + planner.WINDOW_AHEAD + timedelta(days=30)
    db.add(
        PlannerItem(
            item_key="assignment:99",
            plannable_type="assignment",
            plannable_id="99",
            kind="assignment",
            course_id=courses["a"].id,
            title="Far",
            plannable_date=far,
        )
    )
    await db.commit()
    await planner.sync_planner(db, FakeClient(items=[]))
    await db.commit()
    assert "assignment:99" in await _items(db)


async def test_nothing_is_deleted_when_the_planner_request_fails(db, courses):
    due = NOW + timedelta(days=3)
    await planner.sync_planner(db, FakeClient(items=[_assignment(1, 7101, "A1", due)]))
    await db.commit()
    result = await planner.sync_planner(db, FakeClient(fail_items=True))
    await db.commit()
    assert result.removed == 0 and result.errors
    assert "assignment:1" in await _items(db)


async def test_single_course_sync_only_touches_that_course(db, courses):
    due = NOW + timedelta(days=3)
    both = [_assignment(1, 7101, "A", due), _assignment(2, 7102, "B", due)]
    await planner.sync_planner(db, FakeClient(items=both))
    await db.commit()

    client = FakeClient(items=[])  # A 那门课这次什么都没返回
    await planner.sync_planner(db, client, courses["a"])
    await db.commit()
    assert client.planner_calls == [["course_7101"]]
    assert list(await _items(db)) == ["assignment:2"]  # B 的那条不受影响


# ---------- 用户改动 ----------


async def _sync_one(db, item: dict) -> PlannerItem:
    await planner.sync_planner(db, FakeClient(items=[item]))
    await db.commit()
    return (await db.execute(select(PlannerItem))).scalars().first()


async def test_overlay_survives_resync(client, db, courses):
    """用户改过的日期和标题，下一轮同步刷新截止时间也不能被冲掉。"""
    due = NOW + timedelta(days=3)
    item = await _sync_one(db, _assignment(1, 7101, "A1", due))
    mine = NOW + timedelta(days=1)
    resp = await client.patch(
        f"/api/v1/planner/items/{item.id}",
        json={"title": "先写 A1 草稿", "due_at": _iso(mine), "note": "找 TA 问 Q3"},
    )
    assert resp.status_code == 200

    # Canvas 那边截止改了一天
    await _sync_one(db, _assignment(1, 7101, "A1", due + timedelta(days=1)))
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["title"] == "先写 A1 草稿" and row["canvas_title"] == "A1"
    assert row["my_due_at"].startswith(_iso(mine)[:16])
    assert row["date"].startswith(_iso(mine)[:16])  # 显示的是自己的截止
    assert row["due_at"].startswith(_iso(due + timedelta(days=1))[:10])  # Canvas 的照常更新
    assert row["note"] == "找 TA 问 Q3"


async def test_overlay_comes_back_with_the_item(client, db, courses):
    """条目从 Canvas 上消失又回来，用户的改动还在 —— 锚点是 key 不是行 id。"""
    due = NOW + timedelta(days=3)
    item = await _sync_one(db, _assignment(1, 7101, "A1", due))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"note": "记得"})
    await planner.sync_planner(db, FakeClient(items=[]))
    await db.commit()
    await _sync_one(db, _assignment(1, 7101, "A1", due))
    assert (await client.get("/api/v1/planner/items")).json()[0]["note"] == "记得"


async def test_completion_follows_canvas_until_the_user_says_otherwise(client, db, courses):
    due = NOW + timedelta(days=3)
    item = await _sync_one(db, _assignment(1, 7101, "A1", due, submitted=True))
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["completed"] is True and row["completed_override"] is None

    # 用户明确取消：交了但还想改，留在待办里
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": False})
    assert (await client.get("/api/v1/planner/items")).json()[0]["completed"] is False

    # 传 null = 恢复跟随 Canvas
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": None})
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["completed"] is True and row["completed_override"] is None


async def test_fields_not_sent_are_not_touched(client, db, courses):
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"note": "留着"})
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"hidden": True})
    rows = (await client.get("/api/v1/planner/items?include_hidden=true")).json()
    assert rows[0]["note"] == "留着" and rows[0]["hidden"] is True


async def test_hidden_and_completed_filters(client, db, courses):
    due = NOW + timedelta(days=3)
    items = [_assignment(1, 7101, "A", due, submitted=True), _assignment(2, 7101, "B", due)]
    await planner.sync_planner(db, FakeClient(items=items))
    await db.commit()
    b = (
        await db.execute(select(PlannerItem).where(PlannerItem.item_key == "assignment:2"))
    ).scalar_one()
    await client.patch(f"/api/v1/planner/items/{b.id}", json={"hidden": True})

    titles = lambda rows: sorted(r["title"] for r in rows)  # noqa: E731
    assert titles((await client.get("/api/v1/planner/items")).json()) == ["A"]
    assert titles((await client.get("/api/v1/planner/items?include_completed=false")).json()) == []
    everything = (await client.get("/api/v1/planner/items?include_hidden=true")).json()
    assert titles(everything) == ["A", "B"]


async def test_date_range_uses_the_users_own_due_date(client, db, courses):
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=10)))
    await client.patch(
        f"/api/v1/planner/items/{item.id}", json={"due_at": _iso(NOW + timedelta(days=1))}
    )
    window = {"start": _iso(NOW), "end": _iso(NOW + timedelta(days=2))}
    rows = (await client.get("/api/v1/planner/items", params=window)).json()
    assert [r["title"] for r in rows] == ["A1"]


async def test_item_due_at_is_attached_as_a_note(client, db, courses):
    """作业列表里那份截止作为备注一起给，和 Planner 的不一样时用户能对照。"""
    due = NOW + timedelta(days=3)
    db.add(
        CanvasItem(
            kind="assignment",
            canvas_id="1",
            course_id=courses["a"].id,
            title="A1",
            due_at=due - timedelta(days=1),
        )
    )
    await db.commit()
    await _sync_one(db, _assignment(1, 7101, "A1", due))
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["item_due_at"].startswith(_iso(due - timedelta(days=1))[:10])


# ---------- 回写 Canvas ----------


@pytest.fixture
def fake_canvas(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(env_settings, "canvas_access_token", "test-token")
    monkeypatch.setattr("app.api.v1.planner.CanvasClient", lambda *a, **k: fake)
    return fake


async def test_no_writeback_when_the_switch_is_off(client, db, courses, fake_canvas):
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))
    resp = await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": True})
    assert resp.json()["writeback"] == "off"
    assert fake_canvas.override_calls == []


async def test_writeback_creates_then_updates_the_override(client, db, courses, fake_canvas):
    await runtime_settings.set_value(db, runtime_settings.PLANNER_WRITEBACK, True)
    await db.commit()
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))

    first = await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": True})
    assert first.json()["writeback"] == "ok"
    second = await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": False})
    assert second.json()["writeback"] == "ok"
    # 第一次新建，第二次用新建出来的 id 去改
    assert fake_canvas.override_calls == [
        ("POST", "assignment", "1", True),
        ("PUT", 999, False),
    ]
    await runtime_settings.set_value(db, runtime_settings.PLANNER_WRITEBACK, False)
    await db.commit()


async def test_writeback_failure_keeps_the_local_change(client, db, courses, monkeypatch):
    class Broken(FakeClient):
        async def create_planner_override(self, *a, **k):
            raise CanvasError("403")

    monkeypatch.setattr(env_settings, "canvas_access_token", "test-token")
    monkeypatch.setattr("app.api.v1.planner.CanvasClient", lambda *a, **k: Broken())
    await runtime_settings.set_value(db, runtime_settings.PLANNER_WRITEBACK, True)
    await db.commit()
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))

    resp = await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": True})
    assert resp.json()["writeback"] == "failed"
    assert resp.json()["item"]["completed"] is True
    await runtime_settings.set_value(db, runtime_settings.PLANNER_WRITEBACK, False)
    await db.commit()


# ---------- 课程颜色 ----------


async def test_course_color_defaults_to_canvas_and_can_be_overridden(client, db, courses):
    await planner.sync_planner(db, FakeClient(colors={"course_7101": "#254284"}))
    await db.commit()
    cid = courses["a"].id

    course = (await client.get(f"/api/v1/courses/{cid}")).json()
    assert course["color"] == "#254284" and course["custom_color"] is False

    await client.patch(f"/api/v1/courses/{cid}", json={"color": "#ff0000"})
    course = (await client.get(f"/api/v1/courses/{cid}")).json()
    assert course["color"] == "#ff0000" and course["custom_color"] is True

    # 下一轮同步 Canvas 颜色变了，用户改过的不受影响
    await planner.sync_planner(db, FakeClient(colors={"course_7101": "#000000"}))
    await db.commit()
    assert (await client.get(f"/api/v1/courses/{cid}")).json()["color"] == "#ff0000"

    # 空串 = 恢复默认
    await client.patch(f"/api/v1/courses/{cid}", json={"color": ""})
    assert (await client.get(f"/api/v1/courses/{cid}")).json()["color"] == "#000000"


async def test_invalid_color_is_rejected(client, db, courses):
    resp = await client.patch(f"/api/v1/courses/{courses['a'].id}", json={"color": "red"})
    assert resp.status_code == 400


# ---------- 取消同步 / 状态 ----------


async def test_unsyncing_a_course_removes_its_items_and_overlays(client, db, courses):
    from app.services import purge

    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"note": "x"})
    course = await db.get(Course, courses["a"].id)
    await purge.purge_course_content(db, course)
    await db.commit()
    assert await _items(db) == {}
    assert (await db.execute(select(PlannerOverlay))).scalars().all() == []


async def test_status_reports_both_sources(client, db, courses):
    due = NOW + timedelta(days=3)
    await planner.sync_planner(
        db,
        FakeClient(
            items=[_assignment(1, 7101, "A1", due)],
            events={
                7101: [
                    {"id": 4, "title": "E", "start_at": _iso(due), "context_code": "course_7101"}
                ]
            },
        ),
    )
    await db.commit()
    status = (await client.get("/api/v1/planner/status")).json()
    assert status["items"]["count"] == 1 and status["items"]["status"] == "ok"
    assert status["events"]["count"] == 1 and status["events"]["status"] == "ok"


# ---------- 改过的条目：留着、时间地点可改、重点 ----------


async def test_modified_item_survives_deletion_on_canvas(client, db, courses):
    """用户改过的条目，Canvas 上删了也留着，打上标记；回来时标记摘掉。"""
    due = NOW + timedelta(days=3)
    item = await _sync_one(db, _assignment(1, 7101, "A1", due))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"title": "期中复习"})

    result = await planner.sync_planner(db, FakeClient(items=[]))
    await db.commit()
    assert result.removed == 0
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["deleted_on_canvas"] is True and row["title"] == "期中复习"

    await _sync_one(db, _assignment(1, 7101, "A1", due))
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["deleted_on_canvas"] is False


async def test_an_overlay_reset_to_nothing_does_not_keep_the_item(client, db, courses):
    """改过又全部清掉 = 没改过。那就和普通条目一样，Canvas 删了就删。"""
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"note": "x"})
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"note": None})
    result = await planner.sync_planner(db, FakeClient(items=[]))
    await db.commit()
    assert result.removed == 1


async def test_completion_still_follows_canvas_on_a_renamed_item(client, db, courses):
    """改名不等于接管完成状态：交了作业，它照样自动算完成。"""
    due = NOW + timedelta(days=3)
    item = await _sync_one(db, _assignment(1, 7101, "A1", due))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"title": "A1 草稿"})
    await _sync_one(db, _assignment(1, 7101, "A1", due, submitted=True))
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["completed"] is True and row["title"] == "A1 草稿"


async def test_event_time_and_location_can_be_edited(client, db, courses):
    start = NOW + timedelta(days=2)
    item = await _sync_one(db, _event(3, 7101, "Office Hours", start))
    new_start, new_end = start + timedelta(hours=1), start + timedelta(hours=2)
    await client.patch(
        f"/api/v1/planner/items/{item.id}",
        json={"start_at": _iso(new_start), "end_at": _iso(new_end), "location": "BA 1130"},
    )
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["start_at"].startswith(_iso(new_start)[:16])
    assert row["canvas_start_at"].startswith(_iso(start)[:16])
    assert row["date"].startswith(_iso(new_start)[:16])  # 日历按改过的时间摆
    assert row["location_name"] == "BA 1130" and row["canvas_location"] == "BA 2230"
    assert row["time_modified"] is True


async def test_important_flag(client, db, courses):
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3)))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"important": True})
    assert (await client.get("/api/v1/planner/items")).json()[0]["important"] is True


async def test_toggling_back_to_canvas_state_does_not_pin(client, db, courses):
    """勾上又取消 = 回到原样，不是接管。否则以后在 Canvas 上交了也不会自动变完成。"""
    due = NOW + timedelta(days=3)
    item = await _sync_one(db, _assignment(1, 7101, "A1", due))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": True})
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": False})
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["completed_override"] is None

    await _sync_one(db, _assignment(1, 7101, "A1", due, submitted=True))
    assert (await client.get("/api/v1/planner/items")).json()[0]["completed"] is True


async def test_marking_a_submitted_item_incomplete_is_kept(client, db, courses):
    """和 Canvas 不一致的才钉住：交了但还想留在待办里。"""
    item = await _sync_one(db, _assignment(1, 7101, "A1", NOW + timedelta(days=3), submitted=True))
    await client.patch(f"/api/v1/planner/items/{item.id}", json={"completed": False})
    row = (await client.get("/api/v1/planner/items")).json()[0]
    assert row["completed_override"] is False and row["completed"] is False
