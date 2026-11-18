"""自建任务。

钉住几件事：

1. 自建任务和 Canvas 条目从同一个接口出来、同一个形状，靠 source 区分。
2. 截止二选一：全天存纯日期，具体时刻存 UTC；设一个就清掉另一个。
3. **重复按 To Do 的方式**：勾掉当前这一次，生成下一次；子步骤带过去但全部重置；
   带时刻的按任务自己的时区算，跨夏令时那一周还是同一个墙上时间。
4. 日历要的往后预览是现算的，不落库；任务页可以不要。
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.tasks import TaskList, UserTask


@pytest.fixture
async def clean(db):
    for model in (UserTask, TaskList, Course):
        for row in (await db.execute(select(model))).scalars().all():
            await db.delete(row)
    await db.commit()
    c = Course(canvas_id=8801, name="AAA101", course_code="AAA101", sync_enabled=True)
    db.add(c)
    await db.commit()
    return c


async def _create(client, **body):
    resp = await client.post("/api/v1/tasks", json={"title": "读 Ch.3", **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _tasks(client, **params):
    rows = (await client.get("/api/v1/planner/items", params=params)).json()
    return [r for r in rows if r["source"] == "user"]


# ---------- 创建与形状 ----------


async def test_created_task_comes_out_of_the_planner_list(client, clean):
    task = await _create(client, due_date="2026-10-20")
    assert task["kind"] == "task" and task["source"] == "user"
    assert task["all_day"] is True and task["all_day_date"] == "2026-10-20"
    rows = await _tasks(client)
    assert [r["item_key"] for r in rows] == [f"task:{task['id']}"]


async def test_title_is_required_and_dates_are_checked(client, clean):
    assert (await client.post("/api/v1/tasks", json={"title": "  "})).status_code == 400
    bad = await client.post("/api/v1/tasks", json={"title": "x", "due_date": "10/20"})
    assert bad.status_code == 400


async def test_color_follows_course_then_list_then_neutral(client, clean, db):
    body = {"name": "生活", "color": "#ff8800"}
    lst = (await client.post("/api/v1/task-lists", json=body)).json()
    in_course = await _create(client, course_id=clean.id)
    in_list = await _create(client, list_id=lst["id"])
    plain = await _create(client)
    assert in_course["color"] not in (None, "#ff8800", "#64748b")
    assert in_course["course_code"] == "AAA101"
    assert in_list["color"] == "#ff8800" and in_list["list_name"] == "生活"
    assert plain["color"] == "#64748b"


async def test_due_date_and_due_at_are_exclusive(client, clean):
    task = await _create(client, due_date="2026-10-20")
    res = await client.patch(
        f"/api/v1/tasks/{task['id']}", json={"due_at": "2026-10-21T13:00:00Z"}
    )
    item = res.json()["item"]
    assert item["all_day"] is False and item["all_day_date"] is None
    assert item["due_at"].startswith("2026-10-21T13:00")

    res = await client.patch(f"/api/v1/tasks/{task['id']}", json={"due_date": "2026-10-22"})
    item = res.json()["item"]
    assert item["all_day_date"] == "2026-10-22" and item["due_at"] is None


async def test_fields_not_sent_are_not_touched(client, clean):
    task = await _create(client, note="带计算器")
    await client.patch(f"/api/v1/tasks/{task['id']}", json={"important": True})
    item = (await _tasks(client))[0]
    assert item["note"] == "带计算器" and item["important"] is True


# ---------- 子步骤 ----------


async def test_steps_are_replaced_as_a_whole(client, clean):
    task = await _create(client, steps=[{"title": "读"}, {"title": "做题"}])
    steps = task["steps"]
    assert [s["title"] for s in steps] == ["读", "做题"]

    # 勾掉第二步、调换顺序、删掉第一步、加一步
    res = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={
            "steps": [
                {"id": steps[1]["id"], "title": "做题", "done": True},
                {"title": "对答案"},
            ]
        },
    )
    after = res.json()["item"]["steps"]
    assert [(s["title"], s["done"]) for s in after] == [("做题", True), ("对答案", False)]
    assert after[0]["id"] == steps[1]["id"]  # 原来那一步，不是新建的


# ---------- 完成与重复 ----------


async def test_complete_and_uncomplete(client, clean):
    task = await _create(client)
    res = await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": True})
    assert res.json()["item"]["completed"] is True and res.json()["spawned"] is None
    res = await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": False})
    assert res.json()["item"]["completed"] is False


async def test_completing_a_daily_task_spawns_the_next_one(client, clean):
    task = await _create(
        client,
        due_date="2026-10-20",
        repeat={"freq": "daily"},
        steps=[{"title": "背单词", "done": True}],
    )
    res = (await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": True})).json()
    nxt = res["spawned"]
    assert nxt["all_day_date"] == "2026-10-21"
    assert nxt["series_id"] == res["item"]["series_id"] is not None
    # 子步骤带过去，但全部重置
    assert [(s["title"], s["done"]) for s in nxt["steps"]] == [("背单词", False)]
    # 已完成的那一条留着
    assert res["item"]["completed"] is True


async def test_weekly_repeat_keeps_the_wall_clock_time_across_dst(client, clean):
    """多伦多 2026-11-01 结束夏令时。10/26 早上九点是 13:00Z，下周一早上九点是 14:00Z ——
    按 UTC 加七天的话会变成早上八点。"""
    task = await _create(
        client,
        due_at="2026-10-26T13:00:00Z",
        tz="America/Toronto",
        repeat={"freq": "weekly"},
    )
    res = (await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": True})).json()
    assert res["spawned"]["due_at"].startswith("2026-11-02T14:00")


async def test_weekdays_rule_skips_the_weekend(client, clean):
    # 2026-10-23 是周五
    task = await _create(
        client, due_date="2026-10-23", repeat={"freq": "weekly", "weekdays": [0, 1, 2, 3, 4]}
    )
    res = (await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": True})).json()
    assert res["spawned"]["all_day_date"] == "2026-10-26"


async def test_repeat_without_a_date_starts_today(client, clean):
    task = await _create(client, repeat={"freq": "daily"}, tz="UTC")
    # 任务的时区是 UTC，"今天"也得按 UTC 算 —— 用本机日期的话每天晚上 7 点以后必挂
    assert task["all_day_date"] == datetime.now(UTC).date().isoformat()


async def test_unknown_repeat_is_dropped(client, clean):
    task = await _create(client, repeat={"freq": "hourly"})
    assert task["repeat"] is None


# ---------- 日历里的预览 ----------


async def test_calendar_gets_future_occurrences_but_task_page_does_not(client, clean):
    start = date.today() + timedelta(days=1)
    task = await _create(client, due_date=start.isoformat(), repeat={"freq": "weekly"})
    with_preview = await _tasks(client)
    future = [r for r in with_preview if r["virtual"]]
    assert future and future[0]["all_day_date"] == (start + timedelta(days=7)).isoformat()
    assert all(r["item_key"].startswith(f"task:{task['id']}@") for r in future)
    assert all(not r["completed"] for r in future)

    without = await _tasks(client, projections="false")
    assert [r["item_key"] for r in without] == [f"task:{task['id']}"]


async def test_completed_series_has_no_preview(client, clean):
    task = await _create(client, due_date="2026-10-20", repeat={"freq": "daily"})
    await client.patch(f"/api/v1/tasks/{task['id']}", json={"completed": True})
    rows = await _tasks(client)
    # 已完成的那条没有预览；新生成的那条有
    previews = {r["item_key"].split("@")[0] for r in rows if r["virtual"]}
    assert previews == {f"task:{r['id']}" for r in rows if not r["virtual"] and not r["completed"]}


# ---------- 筛选 / 清单 / 删除 ----------


async def test_course_and_kind_filters_apply_to_tasks(client, clean):
    await _create(client, course_id=clean.id)
    await _create(client)
    assert len(await _tasks(client, course=clean.id)) == 1
    assert await _tasks(client, kind="assignment") == []


async def test_deleting_a_list_keeps_its_tasks(client, clean):
    lst = (await client.post("/api/v1/task-lists", json={"name": "实习"})).json()
    task = await _create(client, list_id=lst["id"])
    lists = (await client.get("/api/v1/task-lists")).json()
    assert lists[0]["task_count"] == 1

    await client.delete(f"/api/v1/task-lists/{lst['id']}")
    row = next(r for r in await _tasks(client) if r["id"] == task["id"])
    assert row["list_id"] is None


async def test_list_validation(client, clean):
    assert (await client.post("/api/v1/task-lists", json={"name": " "})).status_code == 400
    bad = await client.post("/api/v1/task-lists", json={"name": "x", "color": "red"})
    assert bad.status_code == 400


async def test_delete_task(client, clean):
    task = await _create(client)
    assert (await client.delete(f"/api/v1/tasks/{task['id']}")).status_code == 204
    assert await _tasks(client) == []
    assert (await client.delete(f"/api/v1/tasks/{task['id']}")).status_code == 404
