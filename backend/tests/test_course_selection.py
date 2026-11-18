"""「选择同步课程」的全部使用场景。

设计的两条主线：

1. **`courses` 表是目录，不是"已同步的东西"。** Canvas 上能看到的课全部入表，
   `sync_enabled` 决定哪些真正拉内容。取消勾选删的是**内容**，目录行留着 ——
   不然用户没法把这门课再勾回来。
2. **结课只是一个标签。** 一门课结课不改变它同不同步：资料往往还要用很久，
   替用户关掉是自作主张。
"""

import asyncio

import pytest
from sqlalchemy import select

from app.api.v1 import settings as settings_api
from app.core.config import settings as real_settings
from app.models.canvas_item import CanvasItem, ItemKind
from app.models.course import Course
from app.models.file import CourseFile, FileGroup
from app.models.system import EventKind, NotificationEvent, SyncState
from app.workers.scheduler import sync_manager


class _Configured:
    canvas_configured = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


@pytest.fixture
def sync_spy(monkeypatch):
    started: list[bool] = []

    async def fake_run_sync(*_args, **_kwargs):
        started.append(True)
        return {}

    monkeypatch.setattr(settings_api, "env_settings", _Configured())
    monkeypatch.setattr(sync_manager, "mark_started", lambda: None)
    monkeypatch.setattr(sync_manager, "run_sync", fake_run_sync)
    return started


async def _courses(db, *specs) -> dict[int, Course]:
    """建一批课，specs 是 (canvas_id, name, is_concluded, sync_enabled)。

    先清空 courses：这个文件里的库是共用的，而保存选择是**整份覆盖**语义 ——
    别的用例留下的课会被当成"用户取消了它们"，把计数搅乱。
    """
    for course in (await db.execute(select(Course))).scalars().all():
        await db.delete(course)
    await db.flush()

    out = {}
    for canvas_id, name, concluded, enabled in specs:
        course = Course(
            canvas_id=canvas_id, name=name, is_concluded=concluded, sync_enabled=enabled
        )
        db.add(course)
        out[canvas_id] = course
    await db.commit()
    return out


async def _fresh(db) -> None:
    """丢掉这个 session 的快照和身份映射，重新从库里读。

    接口是在**另一个 session** 里写的库；不清的话这里查回来的还是测试自己
    建对象时的那份内存状态，改没改都看不出来。
    """
    await db.rollback()
    db.expire_all()


async def _save(client, ids: list[int]) -> dict:
    resp = await client.put("/api/v1/settings/courses", json={"course_ids": ids})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 列出候选 ----------


async def test_picker_lists_every_course_not_just_synced_ones(client, db):
    """对话框必须列出没在同步的课，否则用户永远勾不回来。"""
    made = await _courses(
        db,
        (7001, "在读且同步", False, True),
        (7002, "在读没同步", False, False),
        (7003, "已结课且同步", True, True),
        (7004, "已结课没同步", True, False),
    )
    rows = (await client.get("/api/v1/settings/courses")).json()
    by_canvas = {r["canvas_id"]: r for r in rows}
    for canvas_id in made:
        assert canvas_id in by_canvas, f"{canvas_id} 应该出现在候选列表里"

    assert by_canvas[7002]["sync_enabled"] is False
    assert by_canvas[7004]["is_concluded"] is True
    # 结课和同不同步是两件独立的事，四种组合都合法
    assert by_canvas[7003]["is_concluded"] is True
    assert by_canvas[7003]["sync_enabled"] is True


# ---------- 取消勾选 ----------


async def test_unselecting_a_course_wipes_its_content_but_keeps_the_catalog_row(
    client, db, sync_spy
):
    """取消勾选：内容全删、边栏和课程同步状态跟着更新、触发同步，但目录行留着。"""
    made = await _courses(db, (7101, "要取消的课", False, True), (7102, "留着的课", False, True))
    drop, keep = made[7101], made[7102]

    db.add(CourseFile(canvas_id=71010, course_id=drop.id, display_name="讲义.pdf"))
    db.add(
        CanvasItem(kind=ItemKind.ANNOUNCEMENT, canvas_id="a1", course_id=drop.id, title="公告")
    )
    db.add(FileGroup(course_id=drop.id, name="PDF", rule_key="type:pdf"))
    db.add(
        NotificationEvent(
            kind=EventKind.NEW_FILE, course_id=drop.id, title="提醒", dedup_key="k:7101"
        )
    )
    db.add(SyncState(resource_key="course:7101:files", status="ok"))
    # 留着的那门课也造点数据，确认不受牵连
    db.add(CourseFile(canvas_id=71020, course_id=keep.id, display_name="别动我.pdf"))
    await db.commit()
    keep_id = keep.id

    res = await _save(client, [keep_id])
    assert res["disabled"] == 1
    assert res["removed_files"] == 1
    assert res["sync_started"] is True
    await asyncio.sleep(0)
    assert len(sync_spy) == 1

    await _fresh(db)
    # 1. 目录行留着，但标记为不同步
    row = (await db.execute(select(Course).where(Course.canvas_id == 7101))).scalar_one()
    assert row.sync_enabled is False
    assert row.last_synced_at is None, "同步状态要归零，否则界面还显示上次同步成功"

    # 2. 内容全没了
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 71010))
    ).scalar_one_or_none() is None
    assert (
        await db.execute(select(CanvasItem).where(CanvasItem.canvas_id == "a1"))
    ).scalar_one_or_none() is None
    assert (
        await db.execute(select(FileGroup).where(FileGroup.course_id == row.id))
    ).scalar_one_or_none() is None
    assert (
        await db.execute(select(NotificationEvent).where(NotificationEvent.dedup_key == "k:7101"))
    ).scalar_one_or_none() is None

    # 3. 边栏和课程同步状态都不再有它
    sidebar = {c["canvas_id"] for c in (await client.get("/api/v1/courses")).json()}
    assert 7101 not in sidebar
    assert 7102 in sidebar
    status = {c["name"] for c in (await client.get("/api/v1/sync/courses")).json()}
    assert "要取消的课" not in status

    # 4. 没被取消的课一点没动
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 71020))
    ).scalar_one_or_none() is not None


async def test_unselecting_clears_sync_states_so_reselecting_is_a_cold_start(client, db, sync_spy):
    """重新勾选回来时必须算首次同步，否则库里每个文件都会发一条"新文件"提醒。

    判断首次同步靠 `last_success_at is None`，所以取消勾选时水位线必须一起删。
    """
    from datetime import UTC, datetime

    made = await _courses(db, (7201, "来回勾选的课", False, True))
    course_id = made[7201].id
    # 造一条"同步成功过"的水位线 —— 冷启动判断看的就是 last_success_at
    db.add(
        SyncState(
            resource_key="course:7201:files", status="ok", last_success_at=datetime.now(UTC)
        )
    )
    await db.commit()

    await _save(client, [])  # 取消所有课

    await _fresh(db)
    assert (
        await db.execute(
            select(SyncState).where(SyncState.resource_key == "course:7201:files")
        )
    ).scalar_one_or_none() is None, "水位线没删，重新勾选时就不算冷启动了"

    # 再勾回来
    res = await _save(client, [course_id])
    assert res["enabled"] == 1
    assert any(n["key"] == "courses_enabled" for n in res["notes"])

    await _fresh(db)
    row = (await db.execute(select(Course).where(Course.canvas_id == 7201))).scalar_one()
    assert row.sync_enabled is True


# ---------- 新勾选 ----------


async def test_selecting_a_new_course_triggers_sync(client, db, sync_spy):
    made = await _courses(db, (7301, "新勾的课", True, False), (7302, "已经在同步", False, True))
    res = await _save(client, [made[7301].id, made[7302].id])
    assert res["enabled"] == 1
    assert res["disabled"] == 0
    assert res["sync_started"] is True
    await asyncio.sleep(0)
    assert len(sync_spy) == 1

    await _fresh(db)
    row = (await db.execute(select(Course).where(Course.canvas_id == 7301))).scalar_one()
    assert row.sync_enabled is True
    # 已结课的课照样能勾选 —— 结课不是"不能同步"
    assert row.is_concluded is True


# ---------- 没变化 / 非法输入 ----------


async def test_saving_the_same_selection_does_nothing(client, db, sync_spy):
    made = await _courses(db, (7401, "不动的课", False, True))
    res = await _save(client, [made[7401].id])
    assert (res["enabled"], res["disabled"]) == (0, 0)
    assert res["sync_started"] is False
    await asyncio.sleep(0)
    assert len(sync_spy) == 0


async def test_unknown_course_id_is_rejected(client, db, sync_spy):
    await _courses(db, (7501, "存在的课", False, True))
    resp = await client.put("/api/v1/settings/courses", json={"course_ids": [999999]})
    assert resp.status_code == 400


async def test_cannot_change_selection_while_syncing(client, db, monkeypatch, sync_spy):
    """同步进行中改选择会当场删数据，而同步可能正在往那些课里写。"""
    made = await _courses(db, (7601, "同步中的课", False, True))
    monkeypatch.setattr(type(sync_manager), "is_busy", property(lambda _self: True))
    resp = await client.put("/api/v1/settings/courses", json={"course_ids": [made[7601].id]})
    assert resp.status_code == 409


# ---------- 结课不改变选择 ----------


class _FakeClient:
    """只实现 sync_courses 用到的那一个方法。"""

    def __init__(self, active: list[dict], completed: list[dict]):
        self._batches = {"active": active, "completed": completed}

    async def list_courses(self, enrollment_state: str = "active") -> list[dict]:
        return self._batches.get(enrollment_state, [])


async def test_becoming_concluded_keeps_the_course_synced(db):
    """一门课结课了只加标签，不取消同步、不删数据 —— 资料往往还要用很久。"""
    from app.integrations.canvas.sync import CanvasSyncService

    await _courses(db, (7701, "本来在读", False, True))

    # Canvas 这一轮把它归到了 completed
    service = CanvasSyncService(
        db, _FakeClient(active=[], completed=[{"id": 7701, "name": "本来在读"}])
    )
    synced = await service.sync_courses()
    await db.commit()

    await _fresh(db)
    row = (await db.execute(select(Course).where(Course.canvas_id == 7701))).scalar_one()
    assert row.is_concluded is True, "要打上已结课标签"
    assert row.sync_enabled is True, "但不能替用户把同步关掉"
    assert 7701 in {c.canvas_id for c in synced}, "结课的课照样要继续同步"


async def test_new_courses_default_to_active_only(db):
    """初始默认：在读的自动同步，已结课的不自动同步。"""
    from app.integrations.canvas.sync import CanvasSyncService

    service = CanvasSyncService(
        db,
        _FakeClient(
            active=[{"id": 7801, "name": "新的在读课"}],
            completed=[{"id": 7802, "name": "新的旧课"}],
        ),
    )
    synced = await service.sync_courses()
    await db.commit()

    await _fresh(db)
    fresh = (await db.execute(select(Course).where(Course.canvas_id == 7801))).scalar_one()
    old = (await db.execute(select(Course).where(Course.canvas_id == 7802))).scalar_one()
    assert fresh.sync_enabled is True
    assert old.sync_enabled is False
    # 两门都进目录（对话框要能列出来），但只有在读的进本轮同步
    assert {c.canvas_id for c in synced} == {7801}


async def test_disabled_courses_are_not_synced_but_stay_in_the_catalog(db):
    from app.integrations.canvas.sync import CanvasSyncService

    await _courses(db, (7901, "用户取消了的课", False, False))
    client = _FakeClient(active=[{"id": 7901, "name": "用户取消了的课"}], completed=[])
    service = CanvasSyncService(db, client)
    synced = await service.sync_courses()
    await db.commit()

    assert 7901 not in {c.canvas_id for c in synced}, "没勾选的课不该被同步"
    await _fresh(db)
    assert (
        await db.execute(select(Course).where(Course.canvas_id == 7901))
    ).scalar_one_or_none() is not None, "但要留在目录里"
