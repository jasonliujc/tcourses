"""两个同步开关（自动下载 / 内联图片）的行为。

语义和「选择同步课程」完全一致，测试也就按同一套标准来：

- **改动（两个方向）→ 触发一次「同步所有课程」**
- **关闭 → 当场把对应的数据删干净**

最后一条是最重要的：**开关关着，它对应的数据就不该留在库里。** 做不到就会出现
"这门课明明没在同步，边栏里还挂着"这种状态 —— 用户没法判断哪个是真的，
整个设置面板就不可信了。所以除了界面上那次删，每轮同步还要兜底扫一遍
（直接改库、升级带来的默认值变化都不经过界面，而且没勾选的课根本不会再被同步，
清理永远轮不到它们）。

课程选择本身的用例在 test_course_selection.py。
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.v1 import settings as settings_api
from app.core.config import settings as real_settings
from app.models.course import Course
from app.models.file import CourseFile
from app.services import runtime_settings
from app.workers.scheduler import sync_manager


class _Configured:
    """真实配置的代理，只把 canvas_configured 顶成 True。

    没配 token 时设置接口不会去触发同步（正确行为），但那样就测不到"改开关会同步"。
    """

    canvas_configured = True

    def __getattr__(self, name):
        return getattr(real_settings, name)


@pytest.fixture
def sync_spy(monkeypatch):
    """挡住真正的同步，只记录"有没有被触发"。"""
    started: list[bool] = []

    async def fake_run_sync(*_args, **_kwargs):
        started.append(True)
        return {}

    monkeypatch.setattr(settings_api, "env_settings", _Configured())
    # mark_started 会把 is_running 置真，真正的 run_sync 结束时才置回；
    # 这里 run_sync 是假的，不挡住的话下一次 PATCH 会被 409 挡掉
    monkeypatch.setattr(sync_manager, "mark_started", lambda: None)
    monkeypatch.setattr(sync_manager, "run_sync", fake_run_sync)
    return started


async def _set(client, key: str, value: bool) -> dict:
    resp = await client.patch("/api/v1/settings", json={key: value})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _reset(db):
    """把两个开关都清回默认（不写库 = 用默认值）。"""
    from app.models.setting import AppSetting

    for key in (
        runtime_settings.AUTO_DOWNLOAD_FILES,
        runtime_settings.SYNC_INLINE_IMAGES,
    ):
        row = await db.get(AppSetting, key)
        if row is not None:
            await db.delete(row)
    await db.commit()


# ---------- 没变化就不该有动静 ----------


async def test_setting_same_value_does_nothing(client, db, sync_spy):
    """把已经关着的开关再关一次：不触发同步，也不删任何东西。"""
    await _reset(db)
    res = await _set(client, "sync_inline_images", False)
    assert res["sync_started"] is False
    await asyncio.sleep(0)
    assert len(sync_spy) == 0


# ---------- 自动下载文件到本地 ----------


async def test_closing_auto_download_syncs_and_deletes_local_copies(client, db, sync_spy):
    """关掉「自动下载」：触发同步，删掉本地副本，但文件记录本身留着 ——
    关掉的只是"另存一份到本地"，元数据始终同步。"""
    await _reset(db)
    course = Course(canvas_id=9201, name="下载测试")
    db.add(course)
    await db.flush()

    from app.services import storage

    local = storage.settings.storage_path / "9201_test" / "copy.pdf"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("x")
    db.add(
        CourseFile(
            canvas_id=92010,
            course_id=course.id,
            display_name="copy.pdf",
            local_path=storage.relative_to_root(local),
        )
    )
    await db.commit()

    await _set(client, "auto_download_files", True)
    await asyncio.sleep(0)
    assert len(sync_spy) == 1  # 开启也触发同步

    res = await _set(client, "auto_download_files", False)
    assert res["sync_started"] is True
    await asyncio.sleep(0)
    assert len(sync_spy) == 2

    await db.rollback()
    row = (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 92010))
    ).scalar_one_or_none()
    assert row is not None, "文件记录不该被删"
    assert row.local_path is None
    assert row.downloaded_at is None
    assert not local.exists(), "磁盘上的副本要真的删掉"


# ---------- 同步内联图片 ----------


async def test_closing_inline_images_syncs_and_deletes_them(client, db, sync_spy):
    await _reset(db)
    course = Course(canvas_id=9301, name="内联图测试")
    db.add(course)
    await db.flush()
    db.add(
        CourseFile(
            canvas_id=93010, course_id=course.id, display_name="banner.png", is_inline_image=True
        )
    )
    db.add(
        CourseFile(
            canvas_id=93011, course_id=course.id, display_name="讲义.pdf", is_inline_image=False
        )
    )
    await db.commit()

    await _set(client, "sync_inline_images", True)
    await asyncio.sleep(0)
    assert len(sync_spy) == 1

    res = await _set(client, "sync_inline_images", False)
    assert res["sync_started"] is True
    await asyncio.sleep(0)
    assert len(sync_spy) == 2

    await db.rollback()
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 93010))
    ).scalar_one_or_none() is None
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 93011))
    ).scalar_one_or_none() is not None, "不是内联图的文件不能被误删"


# ---------- 同步时的兜底清理 ----------


async def test_sync_sweeps_data_for_switches_that_are_off(db):
    """界面上那次删只覆盖「用户亲手点」。直接改库、升级带来的默认值变化都绕过它，
    而且没勾选的课根本不会再被同步 —— 所以每轮同步必须再兜底扫一遍。

    这正是「这门课已经没在同步了，但它还挂在边栏里」那个状态的解法。
    """
    from app.integrations.canvas.sync import CanvasSyncService
    from app.services import storage

    await _reset(db)
    dropped = Course(canvas_id=9501, name="被取消的课", sync_enabled=False)
    db.add(dropped)
    await db.flush()
    db.add(CourseFile(canvas_id=95010, course_id=dropped.id, display_name="旧讲义.pdf"))

    local = storage.settings.storage_path / "9501_test" / "copy.pdf"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("x")
    active = Course(canvas_id=9502, name="在读", sync_enabled=True)
    db.add(active)
    await db.flush()
    db.add(
        CourseFile(
            canvas_id=95020,
            course_id=active.id,
            display_name="banner.png",
            is_inline_image=True,
            local_path=storage.relative_to_root(local),
        )
    )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    service.runtime = runtime_settings.RuntimeSettings(
        auto_download_files=False, sync_inline_images=False
    )
    await service._sweep_disabled_data()

    await db.rollback()
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 95010))
    ).scalar_one_or_none() is None, "没勾选的课不该留下文件"
    # 目录行要留着，否则用户没法再把这门课勾回来
    assert (
        await db.execute(select(Course).where(Course.canvas_id == 9501))
    ).scalar_one_or_none() is not None, "目录行必须留着"
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 95020))
    ).scalar_one_or_none() is None, "关着「同步内联图片」就不该留下内联图"
    assert not local.exists()


async def test_sync_keeps_data_for_switches_that_are_on(db):
    """反过来：勾选着的课 + 开着的开关，兜底清理不能动任何东西。"""
    from app.integrations.canvas.sync import CanvasSyncService

    await _reset(db)
    course = Course(canvas_id=9601, name="保留测试", is_concluded=True, sync_enabled=True)
    db.add(course)
    await db.flush()
    db.add(
        CourseFile(
            canvas_id=96010, course_id=course.id, display_name="banner.png", is_inline_image=True
        )
    )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    service.runtime = runtime_settings.RuntimeSettings(
        auto_download_files=True, sync_inline_images=True
    )
    await service._sweep_disabled_data()

    await db.rollback()
    # 已结课但用户选了同步 —— 结课只是标签，不该导致数据被清掉
    assert (
        await db.execute(select(Course).where(Course.canvas_id == 9601))
    ).scalar_one_or_none() is not None
    assert (
        await db.execute(select(CourseFile).where(CourseFile.canvas_id == 96010))
    ).scalar_one_or_none() is not None


# ---------- 链接的两个开关 ----------


async def test_link_toggles_behave_like_the_others(client, db, sync_spy):
    """和另外两个开关一条规矩：改动 → 同步一次；关闭 → 当场把数据删干净。"""
    from app.models.file import CourseFile, Medium
    from app.services import links

    course = Course(canvas_id=7300, name="链接开关")
    db.add(course)
    await db.commit()
    # 先把 id 存成普通 int：expire_all 之后再碰 ORM 属性会触发同步懒加载，
    # 在 async 下直接抛 MissingGreenlet
    course_id = course.id
    url = "https://youtu.be/abc"
    db.add(
        CourseFile(
            canvas_id=0,
            course_id=course_id,
            resource_key=links.resource_key(links.normalize(url)),
            medium=Medium.LINK,
            display_name="录播",
            external_url=url,
        )
    )
    await db.commit()

    # 默认是开的，关掉它
    body = await _set(client, "sync_links", False)
    assert body["sync_links"] is False
    assert sync_spy, "改开关必须触发一次同步"

    await db.rollback()
    db.expire_all()
    left = (
        await db.execute(select(CourseFile).where(CourseFile.course_id == course_id))
    ).scalars().all()
    assert left == [], "关掉「同步链接」之后库里不该还留着链接"


async def test_announcement_link_toggle_only_drops_announcement_only_links(client, db, sync_spy):
    """同一个链接可能既在公告里贴过、也挂在某个页面上。

    用户关掉的是"从公告收链接"，不是"删掉这个链接" —— 页面上还有的那条必须留下，
    否则他会发现自己在课程页面上看到的链接凭空少了。
    """
    from app.models.file import CourseFile, FileSource, Medium, SourceKind
    from app.services import links

    course = Course(canvas_id=7301, name="公告链接开关")
    db.add(course)
    await db.commit()
    course_id = course.id

    def make(url: str, name: str) -> CourseFile:
        f = CourseFile(
            canvas_id=0,
            course_id=course_id,
            resource_key=links.resource_key(links.normalize(url)),
            medium=Medium.LINK,
            display_name=name,
            external_url=url,
        )
        db.add(f)
        return f

    only_ann = make("https://a.com/only-announcement", "只在公告里")
    both = make("https://b.com/also-on-a-page", "公告和页面都有")
    await db.commit()
    layout = (
        (only_ann, [SourceKind.ANNOUNCEMENT]),
        (both, [SourceKind.ANNOUNCEMENT, SourceKind.PAGE]),
    )
    for f, kinds in layout:
        for i, kind in enumerate(kinds):
            db.add(
                FileSource(
                    file_id=f.id,
                    course_id=course_id,
                    source_kind=kind,
                    source_key=f"{kind}:{f.id}:{i}",
                )
            )
    await db.commit()

    # 默认就是关的，先打开再关掉，才走得到清理那一步
    await _set(client, "sync_announcement_links", True)
    await _set(client, "sync_announcement_links", False)

    await db.rollback()
    db.expire_all()
    left = {
        f.display_name
        for f in (
            await db.execute(select(CourseFile).where(CourseFile.course_id == course_id))
        ).scalars().all()
    }
    assert left == {"公告和页面都有"}


async def test_toggling_links_does_not_flood_the_update_feed(client, db, sync_spy):
    """回归：打开「同步链接」之后库里会一次性冒出几百个链接。

    那不是"新消息"，是用户刚点的那个开关的直接结果 —— 全发一遍提醒会把更新
    列表彻底淹掉（这门库里就有 494 个）。所以改开关时清掉链接的冷启动水位线，
    打开后的第一轮只入库、不通知。
    """
    from app.models.system import SyncState

    course = Course(canvas_id=7302, name="水位线")
    db.add(course)
    await db.commit()

    # 先压到 False，再造水位线，最后打开 —— 这样"改动"一定发生，
    # 不依赖前面的用例把开关留在哪个状态
    await _set(client, "sync_links", False)
    db.add(
        SyncState(
            resource_key=f"course:{course.canvas_id}:links",
            last_success_at=datetime.now(UTC),
        )
    )
    await db.commit()

    await _set(client, "sync_links", True)

    await db.rollback()
    db.expire_all()
    left = (
        await db.execute(
            select(SyncState).where(SyncState.resource_key.like("course:%:links"))
        )
    ).scalars().all()
    assert left == [], "改链接开关必须清掉链接的水位线，否则下一轮会发几百条提醒"
