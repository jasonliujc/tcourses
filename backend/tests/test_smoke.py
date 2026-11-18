"""端到端冒烟：不连 Canvas，直接往库里塞一门课和几个文件，走完整条 API 链路。"""

from sqlalchemy import select

from app.models.course import Course
from app.models.file import CourseFile, FileSource, SourceKind
from app.services.file_collector import UNGROUPED_PAGES, UNTITLED_MODULE


async def _seed(db) -> Course:
    stmt = select(Course).where(Course.canvas_id == 999)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing

    course = Course(canvas_id=999, name="CS 101 Intro", course_code="CS101", term_name="Fall 2026")
    db.add(course)
    await db.flush()

    samples = [
        ("Week 1 Slides.pptx", "Lectures/Week 1"),
        ("Week 2 Slides.pptx", "Lectures/Week 2"),
        ("Problem Set 1.pdf", "Assignments"),
        ("Syllabus.pdf", None),
        ("starter_code.ipynb", "Labs/Lab 1"),
    ]
    for i, (name, folder) in enumerate(samples):
        db.add(
            CourseFile(
                canvas_id=1000 + i,
                course_id=course.id,
                display_name=name,
                filename=name,
                canvas_folder_path=folder,
            )
        )
    await db.commit()
    return course


async def _page_course(db, canvas_id: int) -> Course:
    """页面测试专用：每个测试一门独立的课，避免互相看见对方造的数据。"""
    stmt = select(Course).where(Course.canvas_id == canvas_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return existing
    course = Course(canvas_id=canvas_id, name=f"页面测试 {canvas_id}")
    db.add(course)
    await db.commit()
    return course


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_organize_then_browse_by_dimension(client, db):
    course = await _seed(db)

    resp = await client.post(f"/api/v1/courses/{course.id}/organize")
    assert resp.status_code == 200
    assert resp.json()["organized"] == 5

    groups = (await client.get(f"/api/v1/courses/{course.id}/groups")).json()
    # 按周次 / 按用途已经移除，只剩按文件类型
    assert {g["dimension"] for g in groups} == {"type"}

    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    assert len(files) == 5
    # 每个文件在每个维度都有归属
    assert all(len(f["group_ids"]) == len(("type",)) for f in files)


async def test_retired_dimension_groups_are_cleaned_up(client, db):
    """维度下线后，库里遗留的旧分组要清掉，否则界面上冒出一堆再也不更新的组。"""
    from app.models.file import FileGroup, GroupKind

    course = await _seed(db)
    db.add(
        FileGroup(
            course_id=course.id,
            name="Week 1",
            kind=GroupKind.AUTO,
            rule_key="week:1",
        )
    )
    await db.commit()

    await client.post(f"/api/v1/courses/{course.id}/organize")

    left = (await client.get(f"/api/v1/courses/{course.id}/groups")).json()
    assert not any(g["rule_key"] and g["rule_key"].startswith("week:") for g in left)


async def test_files_always_carry_the_four_display_fields(client, db):
    """tag / source / date / kind 在任何视图都恒定显示，兜底保证永不为空。"""
    course = await _seed(db)
    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    assert files
    for f in files:
        # 标签是一组板块，至少一个，每个都有说法
        assert f["tags"]
        assert all(tag["id"] and tag["label"] for tag in f["tags"])
        assert f["source_label"]
        assert f["kind"] and f["kind_label"]
        # 界面按它决定类型标签的样子（文件 vs 外部链接）；链接接进来之前恒为 file
        assert f["medium"] == "file"


async def test_user_grouping_survives_reorganize(client, db):
    """用户整理的结果和自动分组是两套表，重跑整理碰不到它。

    以前这条靠"给文件打 organize_locked、让自动管道绕开"来保证 —— 规则里
    漏一个分支就破了。现在是结构性的：自动分组写 `file_groups`，
    用户的东西在 `user_groups`，两边根本不共享行。
    """
    course = await _seed(db)
    await client.post(f"/api/v1/courses/{course.id}/organize")

    created = (
        await client.post(
            f"/api/v1/courses/{course.id}/user-groups", json={"name": "期末复习必看"}
        )
    ).json()
    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    picked = [files[0]["id"], files[1]["id"]]
    added = (
        await client.post(
            f"/api/v1/user-groups/{created['id']}/files", json={"file_ids": picked}
        )
    ).json()
    assert added["changed"] == 2

    # 重跑自动分组
    await client.post(f"/api/v1/courses/{course.id}/organize")

    still_there = (
        await client.get(
            f"/api/v1/courses/{course.id}/files?user_group_id={created['id']}"
        )
    ).json()
    assert {f["id"] for f in still_there} == set(picked)

    # 而且分组归属跟着文件走：任何视图下都能看到"这个文件在我的哪个组里"
    everything = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    tagged = {f["id"]: [g["name"] for g in f["user_groups"]] for f in everything}
    for file_id in picked:
        assert tagged[file_id] == ["期末复习必看"]


async def test_sync_status_reports_missing_canvas_config(client):
    status = (await client.get("/api/v1/sync/status")).json()
    assert status["canvas_configured"] is False

    # 没配 token 时连接检查应给出明确的 412，而不是 500
    resp = await client.get("/api/v1/sync/check")
    assert resp.status_code == 412


async def test_file_lock_endpoint_serializes(client, db):
    """回归：FileOut 会读 sources 这个 relationship，端点必须预加载，否则 async 下直接 500。"""
    course = await _seed(db)
    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    resp = await client.post(f"/api/v1/files/{files[0]['id']}/lock?locked=true")
    assert resp.status_code == 200
    assert resp.json()["organize_locked"] is True


async def test_source_tree_empty_without_sources(client, db):
    course = await _seed(db)
    resp = await client.get(f"/api/v1/courses/{course.id}/sources")
    assert resp.status_code == 200
    assert resp.json() == []


async def _module_page_course(db) -> Course:
    """一门课，文件分布覆盖合并树要处理的每种情况。

    2001 同时挂在 module "Week 1" 和该模块下的页面里（去重要用到）；
    2002 只在页面里；2003 是没归到任何模块的页面；2004 是没名字的模块。
    """
    from app.models.file import FileSource

    # 库在同一个测试文件里是共用的，和 _seed 一样要幂等
    existing = (
        await db.execute(select(Course).where(Course.canvas_id == 778))
    ).scalar_one_or_none()
    if existing:
        return existing

    course = Course(canvas_id=778, name="合并树测试")
    db.add(course)
    await db.flush()

    layout = [
        (2001, [("module", "Week 1"), ("page", "Week 1")]),
        (2002, [("page", "Week 1")]),
        (2003, [("page", UNGROUPED_PAGES)]),
        (2004, [("module", UNTITLED_MODULE)]),
    ]
    for canvas_id, sources in layout:
        f = CourseFile(canvas_id=canvas_id, course_id=course.id, display_name=f"f{canvas_id}.pdf")
        db.add(f)
        await db.flush()
        for kind, container in sources:
            db.add(
                FileSource(
                    file_id=f.id,
                    course_id=course.id,
                    source_kind=kind,
                    source_key=f"{kind}:{container}:{canvas_id}",
                    container_name=container,
                )
            )
    await db.commit()
    return course


async def test_source_tree_merges_modules_and_pages(client, db):
    """导航树按板块组织，名字和设置页那份列表同一套词，但 Modules 和 Pages 合并。

    这两处以前各说各的：树上叫「Canvas Modules & Pages」，设置页叫「板块覆盖」，
    收集器内部又是 source_kind —— 用户得自己在三套说法之间换算。

    计数按文件去重：同一个文件既是 module 条目、又被同模块的页面引用过是常态，
    直接相加会算两次。
    """
    course = await _module_page_course(db)

    tree = {n["kind"]: n for n in (await client.get(f"/api/v1/courses/{course.id}/sources")).json()}
    # Modules 和 Pages 在**树上**是一个节点：Canvas 上页面几乎都挂在模块下，
    # 分开的话同一个 "Week 1" 在两个父节点下各出现一次、各显示一部分。
    # 设置页那份板块列表仍然分开 —— 那里回答的是"每个板块扫到了没有"
    assert set(tree) == {"modules_pages"}
    merged = tree["modules_pages"]
    assert merged["name"] == "Modules & Pages"
    # 4 个文件，2001 同时是 module 条目又被同模块页面引用，只算一次
    assert merged["file_count"] == 4

    kids = {c["name"]: c["file_count"] for c in merged["children"]}
    assert kids == {"Week 1": 2, UNGROUPED_PAGES: 2}
    # 认不出归属的排最后
    assert merged["children"][-1]["name"] == UNGROUPED_PAGES

    # 「认不出归属」这个节点背后是两个容器名（Untitled module / Ungrouped），
    # 点开要两种都出来
    ungrouped = (
        await client.get(
            f"/api/v1/courses/{course.id}/files"
            f"?source_kind=modules_pages&source_key={UNGROUPED_PAGES}"
        )
    ).json()
    assert {f["canvas_id"] for f in ungrouped} == {2003, 2004}

    week1 = (
        await client.get(
            f"/api/v1/courses/{course.id}/files?source_kind=modules_pages&source_key=Week 1"
        )
    ).json()
    assert {f["canvas_id"] for f in week1} == {2001, 2002}


async def test_tags_speak_the_same_vocabulary_as_the_sidebar_and_filter(client, db):
    """行上的标签、侧边栏的节点、筛选面板的板块，**必须是同一套词**。

    以前行上写的是 source_kind（module / page 分开），而侧边栏和筛选说的是板块
    （两者合成 Modules & Pages）。同一个东西两套说法，用户按板块筛出来一堆标着
    另一个词的资料，只能理解成出了 bug。

    这里用"能不能筛回来"来钉死它：一个文件身上的每一个标签，拿去当筛选条件
    都必须能把它自己筛出来。
    """
    course = await _module_page_course(db)
    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    assert files

    for f in files:
        assert f["tags"], f["display_name"]
        for tag in f["tags"]:
            hit = (
                await client.get(f"/api/v1/courses/{course.id}/files?board={tag['id']}")
            ).json()
            assert f["canvas_id"] in {h["canvas_id"] for h in hit}, (
                f"{f['display_name']} 标着 {tag['id']}，按这个板块却筛不出来"
            )

    # 模块里的和页面里的资料，标签都是侧边栏上那个合并节点
    tags = {f["canvas_id"]: [t["id"] for t in f["tags"]] for f in files}
    assert tags[2001] == ["modules_pages"]
    assert tags[2002] == ["modules_pages"]


async def test_a_file_in_two_boards_carries_both_tags(client, db):
    """一个文件同时挂在两个板块下，两个标签都要给出来 —— 只给主来源那个的话，
    它会出现在另一个板块的筛选结果里，而行上完全看不出为什么。"""
    course = await _seed(db)
    file = (
        await db.execute(select(CourseFile).where(CourseFile.course_id == course.id).limit(1))
    ).scalars().first()
    db.add_all(
        [
            FileSource(
                file_id=file.id,
                course_id=course.id,
                source_kind=SourceKind.MODULE,
                source_key="m9",
                container_name="Week 9",
            ),
            FileSource(
                file_id=file.id,
                course_id=course.id,
                source_kind=SourceKind.SYLLABUS,
                source_key="syllabus",
                container_name="Syllabus",
            ),
        ]
    )
    await db.commit()

    rows = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    row = next(r for r in rows if r["id"] == file.id)
    assert {t["id"] for t in row["tags"]} >= {"modules_pages", "syllabus"}
    # 主来源那个排第一：路径和日期都取自它，标签的顺序跟它一致才不会看着别扭
    assert row["tags"][0]["id"] == "modules_pages"


async def test_file_sorting(client, db):
    course = await _seed(db)
    by_name = (
        await client.get(f"/api/v1/courses/{course.id}/files?sort_by=name&order=asc")
    ).json()
    names = [f["display_name"] for f in by_name]
    assert names == sorted(names)

    desc = (await client.get(f"/api/v1/courses/{course.id}/files?sort_by=name&order=desc")).json()
    assert [f["display_name"] for f in desc] == sorted(names, reverse=True)


async def test_duplicate_file_sources_are_deduped_within_a_round(client, db):
    """回归：同一个页面被多个 module 引用时，收集器会产出重复来源。
    本轮内不去重的话第二条会撞唯一键，整轮同步随之中断。"""
    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.file import CourseFile, FileSource
    from app.services.file_collector import CollectedSource

    course = await _seed(db)
    file = (
        await db.execute(select(CourseFile).where(CourseFile.course_id == course.id).limit(1))
    ).scalar_one()

    dup = CollectedSource(
        source_kind="page",
        source_key="modpage:同一个键",
        container_name="Module A",
        label="A",
    )
    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    await service._sync_file_sources(file, [dup, dup], course)
    await db.commit()

    rows = (
        (await db.execute(select(FileSource).where(FileSource.file_id == file.id))).scalars().all()
    )
    assert len(rows) == 1


async def test_each_dimension_partitions_all_files(client, db):
    """核心不变量：某个维度下各分组的文件数相加 == 该课文件总数。

    做不到这点，界面上的数字就没法信 —— "文档 8 个"会变成"至少 8 个"。
    """
    course = await _seed(db)
    await client.post(f"/api/v1/courses/{course.id}/organize")

    files = (await client.get(f"/api/v1/courses/{course.id}/files")).json()
    total = len(files)
    assert total > 0

    for dim in ("type",):
        groups = (
            await client.get(f"/api/v1/courses/{course.id}/groups?dimension={dim}")
        ).json()
        summed = sum(g["file_count"] for g in groups)
        assert summed == total, f"{dim}: {summed} != {total}"


async def test_no_empty_auto_groups_survive_reorganize(client, db):
    course = await _seed(db)
    await client.post(f"/api/v1/courses/{course.id}/organize")
    groups = (await client.get(f"/api/v1/courses/{course.id}/groups")).json()
    empty = [g["name"] for g in groups if g["kind"] == "auto" and g["file_count"] == 0]
    assert empty == []


async def test_settings_roundtrip(client):
    before = (await client.get("/api/v1/settings")).json()
    assert "auto_download_files" in before

    resp = await client.patch("/api/v1/settings", json={"auto_download_files": True})
    assert resp.status_code == 200
    assert resp.json()["auto_download_files"] is True

    after = (await client.get("/api/v1/settings")).json()
    assert after["auto_download_files"] is True


async def test_deleting_course_cascades_to_notifications(client, db):
    """回归：SQLite 默认不执行外键，删课程会留下一堆孤儿通知。"""
    from app.models.system import NotificationEvent

    course = await _seed(db)
    db.add(
        NotificationEvent(
            kind="new_file", course_id=course.id, title="测试通知", dedup_key="cascade-test"
        )
    )
    await db.commit()

    course_id = course.id
    await db.delete(course)
    await db.commit()

    left = (
        await db.execute(
            select(NotificationEvent).where(NotificationEvent.course_id == course_id)
        )
    ).scalars().all()
    assert left == []


def test_transient_transport_errors_are_retried():
    """回归：一轮同步几百个请求，网络层抖动是常态。

    最初只重试限流，后来补了超时和连接错误，再后来撞上
    RemoteProtocolError（服务端不返回响应就掐断连接）—— 逐个补会补一个漏一个。
    这些全是 httpx.TransportError 的子类，直接覆盖整类。
    """
    import httpx

    from app.integrations.canvas.client import (
        CanvasNotAvailable,
        CanvasRateLimited,
        _is_transient,
    )

    # 值得重试：传输层抖动
    for exc in (
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("no route"),
        httpx.ConnectError("refused"),
        httpx.PoolTimeout("pool busy"),
        httpx.WriteError("broken pipe"),
        CanvasRateLimited("limited"),
    ):
        assert _is_transient(exc), f"{type(exc).__name__} 应该重试"

    # 不该重试：重试多少次结果都一样
    for exc in (
        httpx.LocalProtocolError("我们自己把请求构造错了"),
        CanvasNotAvailable("学校禁用了这个端点"),
        ValueError("代码 bug"),
    ):
        assert not _is_transient(exc), f"{type(exc).__name__} 不该重试"


def test_same_moment_tolerates_missing_tzinfo():
    """SQLite 不存时区：库里读出来是 naive，Canvas 解析出来是 aware。
    直接比较永远不等 —— 每个文件每轮都会被当成"已更新"，重复下载重复提醒。"""
    from datetime import UTC, datetime

    from app.integrations.canvas.mapper import same_moment

    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert same_moment(aware, naive)
    assert same_moment(naive, naive)
    assert same_moment(None, None)
    assert not same_moment(aware, None)
    assert not same_moment(aware, datetime(2026, 1, 2, 3, 4, 6, tzinfo=UTC))


async def test_interval_change_reschedules_with_new_value(client):
    """回归：重排必须在 commit 之后。

    reschedule 用自己的 session 读设置，提交前读到的是旧间隔 ——
    结果就是用户选了 5 分钟，下次同步却排在 15 分钟后。
    """
    from app.workers.scheduler import sync_manager

    resp = await client.patch("/api/v1/settings", json={"sync_interval_minutes": 5})
    assert resp.status_code == 200
    assert resp.json()["sync_interval_minutes"] == 5
    assert sync_manager.interval_minutes == 5

    status = (await client.get("/api/v1/sync/status")).json()
    assert status["interval_minutes"] == 5
    assert status["interval_choices"] == [0, 1, 5, 15, 30, 60, 1440]


async def test_rejects_arbitrary_interval(client):
    resp = await client.patch("/api/v1/settings", json={"sync_interval_minutes": 7})
    assert resp.status_code == 422


async def test_interval_zero_turns_off_auto_sync(client):
    """0 = 关闭自动同步：撤掉排期，只留手动触发。"""
    from app.workers.scheduler import sync_manager

    resp = await client.patch("/api/v1/settings", json={"sync_interval_minutes": 0})
    assert resp.status_code == 200
    assert sync_manager.next_run_at is None

    status = (await client.get("/api/v1/sync/status")).json()
    assert status["interval_minutes"] == 0
    assert status["next_run_at"] is None


async def test_timestamps_carry_utc_marker(client, db):
    """回归：SQLite 存的 datetime 没有时区，直接序列化出去浏览器会按本地时区解析，
    时差多少就显示错多少小时。"""
    from datetime import UTC, datetime

    course = await _seed(db)
    course.last_synced_at = datetime.now(UTC)
    await db.commit()

    rows = (await client.get("/api/v1/sync/courses")).json()
    stamp = next(r["last_synced_at"] for r in rows if r["course_id"] == course.id)
    assert stamp.endswith("Z") or "+00:00" in stamp, stamp


async def test_reschedule_counts_from_last_completion(client):
    """回归：改间隔后要按"上次同步跑完 + 间隔"排，不是"现在 + 间隔"。

    用户设 30 分钟的意思是"距上次同步 30 分钟"，不是"从我改设置这刻起再等 30 分钟"。
    """
    from datetime import UTC, datetime, timedelta

    from app.workers.scheduler import sync_manager

    # 假装 10 分钟前刚跑完
    completed = datetime.now(UTC) - timedelta(minutes=10)
    sync_manager.last_completed_at = completed

    await client.patch("/api/v1/settings", json={"sync_interval_minutes": 30})

    assert sync_manager.next_run_at is not None
    drift = abs((sync_manager.next_run_at - (completed + timedelta(minutes=30))).total_seconds())
    assert drift < 5, f"应排在上次完成后 30 分钟，实际偏差 {drift}s"

    # 从现在算的话会是 30 分钟后；从上次完成算应该只剩约 20 分钟
    remaining = (sync_manager.next_run_at - datetime.now(UTC)).total_seconds() / 60
    assert 19 < remaining < 21, remaining


async def test_reschedule_fires_soon_when_already_overdue(client):
    """把间隔从 1 天改成 1 分钟，而上次同步是 2 小时前 —— 早该跑了，别排到过去。"""
    from datetime import UTC, datetime, timedelta

    from app.workers.scheduler import sync_manager

    sync_manager.last_completed_at = datetime.now(UTC) - timedelta(hours=2)
    await client.patch("/api/v1/settings", json={"sync_interval_minutes": 1})

    assert sync_manager.next_run_at > datetime.now(UTC)
    assert (sync_manager.next_run_at - datetime.now(UTC)).total_seconds() < 60


async def test_last_completion_survives_restart(client):
    """回归：完成时间只存在内存里的话，进程一重启排期基准就丢了 ——
    间隔会退化成"从重启那刻起算"，用户设的"每小时"变成"重启后一小时"。"""
    from datetime import timedelta

    from app.workers.scheduler import SyncManager, sync_manager

    await sync_manager._record_completion()
    recorded = sync_manager.last_completed_at
    assert recorded is not None

    # 模拟重启：全新的 manager，内存里什么都没有
    fresh = SyncManager()
    assert fresh.last_completed_at is None
    restored = await fresh.last_completed()
    assert restored is not None
    assert abs((restored - recorded).total_seconds()) < 2

    # 恢复出来的基准要能正确用于排期（间隔由 reschedule 自己从库里读）
    await client.patch("/api/v1/settings", json={"sync_interval_minutes": 60})
    await fresh.reschedule()
    expected = recorded + timedelta(minutes=60)
    assert abs((fresh.next_run_at - expected).total_seconds()) < 5


async def test_full_sync_blocked_while_single_course_syncing(client, db):
    """回归：单课同步时 is_running 是 False，光看它会放行全量同步，
    结果两边同时写同一门课。"""
    from app.workers.scheduler import sync_manager

    course = await _seed(db)
    sync_manager.syncing_course_ids.add(course.id)
    try:
        assert sync_manager.is_busy
        resp = await client.post("/api/v1/sync/run")
        assert resp.status_code == 409
    finally:
        sync_manager.syncing_course_ids.discard(course.id)


async def test_settings_blocked_while_syncing(client, db):
    """关掉"同步过往课程"会当场删课程，同步期间改设置等于边写边删。"""
    from app.workers.scheduler import sync_manager

    course = await _seed(db)
    sync_manager.syncing_course_ids.add(course.id)
    try:
        resp = await client.patch("/api/v1/settings", json={"sync_interval_minutes": 30})
        assert resp.status_code == 409
    finally:
        sync_manager.syncing_course_ids.discard(course.id)

    # 同步结束后照常可改
    assert (
        await client.patch("/api/v1/settings", json={"sync_interval_minutes": 15})
    ).status_code == 200


async def test_page_cache_renews_even_when_content_unchanged(db):
    """回归：缓存新鲜度不能用 updated_at —— 那个字段只在内容变化时刷新。

    页面没改动 → 行不更新 → 时间戳永远停在第一次抓取 → 缓存过期后再也续不上，
    之后每一轮都要重抓全部页面。
    """
    from datetime import UTC, datetime, timedelta

    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.canvas_item import CanvasItem, ItemKind

    course = await _page_course(db, 9101)
    now = datetime.now(UTC)
    item = CanvasItem(
        kind=ItemKind.PAGE,
        canvas_id="cache-test",
        course_id=course.id,
        title="没变过的页面",
        content_hash="same",
        raw={"url": "cache-test", "body": "<p>hi</p>"},
        canvas_updated_at=now - timedelta(days=2),
        content_fetched_at=now - timedelta(days=3),
    )
    db.add(item)
    await db.commit()

    # 上次检查在 3 天前，而这页 2 天前才改过 —— 该复查了
    assert CanvasSyncService._refresh_score(item, now) >= 1.0
    cache, stale = await CanvasSyncService(db, client=None)._page_refresh_plan(course)  # type: ignore[arg-type]
    # 缓存存的是整个 raw，不只是正文 —— 命中缓存的页面也得带着 updated_at，
    # 否则从这个页面扒出来的链接在界面上就没有日期
    assert cache.get("cache-test", {}).get("body") == "<p>hi</p>"
    assert "cache-test" in stale

    # 记下新的抓取时刻后（内容没变也要记），分数立刻掉下来，不再重复抓
    item.content_fetched_at = now
    await db.commit()
    assert CanvasSyncService._refresh_score(item, now) < 1.0


async def test_page_refresh_prioritises_active_pages(db):
    """刷新配额要按页面活跃度分配，不是雨露均沾地轮询最旧的。

    真实数据：419 个页面里 371 个超过半年没改过，7 天内改过的只有 5 个。
    均分配额等于把绝大部分请求浪费在永远不会变的页面上。
    """
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings as env
    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.canvas_item import CanvasItem, ItemKind

    course = await _page_course(db, 9102)
    now = datetime.now(UTC)
    # 两天前统一检查过一轮
    checked = now - timedelta(days=2)

    # 活跃页面：昨天刚在 Canvas 上改过
    for i in range(3):
        db.add(
            CanvasItem(
                kind=ItemKind.PAGE,
                canvas_id=f"hot-{i}",
                course_id=course.id,
                title=f"活跃 {i}",
                content_hash=f"h{i}",
                raw={"url": f"hot-{i}", "body": "x"},
                canvas_updated_at=now - timedelta(days=1),
                content_fetched_at=checked,
            )
        )
    # 陈旧页面：一年没动过，而且"最旧"（更早被检查）—— 旧策略会优先选它们
    for i in range(20):
        db.add(
            CanvasItem(
                kind=ItemKind.PAGE,
                canvas_id=f"cold-{i}",
                course_id=course.id,
                title=f"陈旧 {i}",
                content_hash=f"c{i}",
                raw={"url": f"cold-{i}", "body": "x"},
                canvas_updated_at=now - timedelta(days=365),
                content_fetched_at=checked - timedelta(hours=i + 1),
            )
        )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    cache, stale = await service._page_refresh_plan(course)

    assert len(cache) == 23  # 全部可复用
    assert len(stale) <= env.page_refresh_batch  # 每轮只刷一小批
    # 配额应该给活跃页面，而不是那些"更旧但一年没动"的
    assert {"hot-0", "hot-1", "hot-2"} <= stale
    assert not any(u.startswith("cold-") for u in stale)


async def test_stale_pages_are_not_starved(db):
    """陈旧页面也不能永远不查 —— 万一教授时隔半年改了呢。"""
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings as env
    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.canvas_item import CanvasItem, ItemKind

    course = await _page_course(db, 9103)
    now = datetime.now(UTC)
    db.add(
        CanvasItem(
            kind=ItemKind.PAGE,
            canvas_id="ancient",
            course_id=course.id,
            title="很久没动的页面",
            content_hash="a",
            raw={"url": "ancient", "body": "x"},
            canvas_updated_at=now - timedelta(days=400),
            # 超过复查上限没查过了
            content_fetched_at=now - timedelta(days=env.max_recheck_days + 1),
        )
    )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    _, stale = await service._page_refresh_plan(course)
    assert "ancient" in stale


async def test_fresh_pages_cost_nothing(db):
    """都还新鲜的时候，这一轮一个页面请求都不该发。"""
    from datetime import UTC, datetime, timedelta

    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.canvas_item import CanvasItem, ItemKind

    course = await _page_course(db, 9104)
    now = datetime.now(UTC)
    db.add(
        CanvasItem(
            kind=ItemKind.PAGE,
            canvas_id="just-checked",
            course_id=course.id,
            title="刚查过",
            content_hash="j",
            raw={"url": "just-checked", "body": "x"},
            canvas_updated_at=now - timedelta(days=1),
            content_fetched_at=now - timedelta(minutes=1),
        )
    )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    _, stale = await service._page_refresh_plan(course)
    assert stale == set()


async def test_overdue_pages_jump_the_queue(db):
    """硬性兜底：超过 max_recheck_days 没核对的页面必须插到活跃页面前面。

    光按活跃度排序不够 —— 活跃页面分数能到 8，超期 31 天的陈旧页面才 1.03，
    会被一直挤在后面，"库里不存在超过 30 天没核对的条目"这个保证就守不住。
    """
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings as env
    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.canvas_item import CanvasItem, ItemKind

    course = await _page_course(db, 9105)
    now = datetime.now(UTC)

    # 一批高分活跃页面：昨天刚改过、两天没查，分数会很高
    for i in range(env.page_refresh_batch):
        db.add(
            CanvasItem(
                kind=ItemKind.PAGE,
                canvas_id=f"sla-hot-{i}",
                course_id=course.id,
                title=f"活跃 {i}",
                content_hash=f"sh{i}",
                raw={"url": f"sla-hot-{i}", "body": "x"},
                canvas_updated_at=now - timedelta(days=1),
                content_fetched_at=now - timedelta(days=2),
            )
        )
    # 两个超期项：一年没动，但也一年没核对过 —— 分数低，却必须优先
    for i in range(2):
        db.add(
            CanvasItem(
                kind=ItemKind.PAGE,
                canvas_id=f"sla-overdue-{i}",
                course_id=course.id,
                title=f"超期 {i}",
                content_hash=f"so{i}",
                raw={"url": f"sla-overdue-{i}", "body": "x"},
                canvas_updated_at=now - timedelta(days=365),
                content_fetched_at=now - timedelta(days=env.max_recheck_days + 10 + i),
            )
        )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    _, stale = await service._page_refresh_plan(course)

    assert len(stale) == env.page_refresh_batch
    # 超期的两个必须都在，哪怕活跃页面分数更高
    assert {"sla-overdue-0", "sla-overdue-1"} <= stale


async def test_files_are_rechecked_after_deadline(db):
    """文件元数据也要有硬性上限：增量同步默认跳过已知文件，
    不强制复查的话文件名/大小在 Canvas 上改了永远发现不了。"""
    from datetime import UTC, datetime, timedelta

    from app.core.config import settings as env
    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.file import CourseFile

    course = await _page_course(db, 9106)
    now = datetime.now(UTC)
    db.add_all(
        [
            CourseFile(
                canvas_id=91060,
                course_id=course.id,
                display_name="刚核对过.pdf",
                metadata_fetched_at=now - timedelta(days=1),
            ),
            CourseFile(
                canvas_id=91061,
                course_id=course.id,
                display_name="超期了.pdf",
                metadata_fetched_at=now - timedelta(days=env.max_recheck_days + 1),
            ),
            CourseFile(
                canvas_id=91062,
                course_id=course.id,
                display_name="从没核对过.pdf",
                metadata_fetched_at=None,
            ),
        ]
    )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]
    due = await service._files_due_for_recheck(course)

    assert 91061 in due and 91062 in due
    assert 91060 not in due  # 还新鲜，不浪费请求


async def test_pages_checked_via_list_are_not_refetched(db):
    """回归：pages 列表能一个请求精确比对所有页面的 updated_at，
    比对下来"没变"也算核对过。

    不记 content_fetched_at 的话，这些页面在复查评分里是"从没查过"
    （分数无穷大、最高优先级），于是每轮都被白抓一遍。
    """
    from datetime import UTC, datetime

    from app.integrations.canvas.sync import CanvasSyncService
    from app.models.canvas_item import CanvasItem, ItemKind

    course = await _page_course(db, 9107)
    db.add(
        CanvasItem(
            kind=ItemKind.PAGE,
            canvas_id="listed-page",
            course_id=course.id,
            title="通过列表同步的页面",
            content_hash="lp",
            raw={"url": "listed-page", "body": "x"},
            canvas_updated_at=datetime.now(UTC),
            content_fetched_at=None,  # 就是这个空值导致反复重抓
        )
    )
    await db.commit()

    service = CanvasSyncService(db, client=None)  # type: ignore[arg-type]

    # 修复前：空的 content_fetched_at → 每轮都被选中
    _, stale = await service._page_refresh_plan(course)
    assert "listed-page" in stale

    # 走过列表核对之后就不该再被选中
    service._checked_page_ids = {"listed-page"}
    await service._mark_pages_checked(course)

    _, stale = await service._page_refresh_plan(course)
    assert "listed-page" not in stale


async def test_list_path_records_checked_ids(db):
    """回归：`_list_pages_with_body` 必须把列表里的页面登记成"已核对"。

    这一环漏掉的话，后面 `_mark_pages_checked` 拿到的是空集合，什么都不会更新 ——
    pages 列表可用的课会一直被当成"从没查过"，每轮白抓一遍。
    """
    from app.integrations.canvas.sync import CanvasSyncService

    class FakeClient:
        """只实现这条路径要用到的两个方法。"""

        async def list_pages(self, course_id):
            return [
                {"page_id": 111, "url": "intro", "updated_at": "2026-01-01T00:00:00Z"},
                {"page_id": 222, "url": "syllabus", "updated_at": "2026-01-02T00:00:00Z"},
            ]

        async def get_page_body(self, course_id, page_url):
            return {"url": page_url, "body": "<p>x</p>", "updated_at": "2026-01-01T00:00:00Z"}

    course = await _page_course(db, 9108)
    service = CanvasSyncService(db, FakeClient())  # type: ignore[arg-type]

    pages = await service._list_pages_with_body(course)

    assert len(pages) == 2
    # canvas_id 的算法必须和 map_page 一致，否则 update 一行都匹配不到
    assert service._checked_page_ids == {"111", "222"}
    from app.integrations.canvas import mapper

    assert {mapper.map_page(p, course.id)["canvas_id"] for p in pages} == service._checked_page_ids


async def test_local_copy_skips_the_network(client, db):
    """本地已经有副本就直接发本地那份，一点网络都不该碰。"""
    from app.models.file import CourseFile
    from app.services import storage

    course = await _page_course(db, 9109)
    path = storage.settings.storage_path / "smoke-9109" / "本地的.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"LOCAL")
    db.add(
        CourseFile(
            canvas_id=91090,
            course_id=course.id,
            display_name="本地的.pdf",
            local_path=storage.relative_to_root(path),
            # 故意不给 download_url：走到网络那条路就会 404，测试就会红
        )
    )
    await db.commit()
    file_id = (
        await db.execute(select(CourseFile.id).where(CourseFile.canvas_id == 91090))
    ).scalar_one()

    resp = await client.get(f"/api/v1/files/{file_id}/download")
    assert resp.status_code == 200
    assert resp.content == b"LOCAL"


async def test_sort_field_matches_displayed_date(client, db):
    """回归：界面显示主来源日期，却按 canvas_updated_at 排序 —— 两个不同的值，
    结果就是"排序完全不对"。显示和排序必须共用同一个字段。
    """
    from datetime import UTC, datetime, timedelta

    from app.models.file import CourseFile

    course = await _page_course(db, 9110)
    now = datetime.now(UTC)
    # 刻意让 display_date 和 canvas_updated_at 的顺序相反
    db.add_all(
        [
            CourseFile(
                canvas_id=91100,
                course_id=course.id,
                display_name="较新的来源日期.pdf",
                display_date=now,
                canvas_updated_at=now - timedelta(days=30),
            ),
            CourseFile(
                canvas_id=91101,
                course_id=course.id,
                display_name="较旧的来源日期.pdf",
                display_date=now - timedelta(days=10),
                canvas_updated_at=now,
            ),
        ]
    )
    await db.commit()

    rows = (
        await client.get(f"/api/v1/courses/{course.id}/files?sort_by=date&order=desc")
    ).json()
    shown = [r["source_date"] for r in rows]
    assert shown == sorted(shown, reverse=True), "显示的日期必须是降序的"
    assert rows[0]["display_name"] == "较新的来源日期.pdf"

    asc = (await client.get(f"/api/v1/courses/{course.id}/files?sort_by=date&order=asc")).json()
    shown_asc = [r["source_date"] for r in asc]
    assert shown_asc == sorted(shown_asc)


async def test_sort_by_name(client, db):
    course = await _seed(db)
    rows = (
        await client.get(f"/api/v1/courses/{course.id}/files?sort_by=name&order=asc")
    ).json()
    names = [r["display_name"] for r in rows]
    assert names == sorted(names)


async def test_undated_resources_always_sort_last(client, db):
    """没有日期的资料在**两个方向**都排最后。

    课程大纲和模块条目在 Canvas 上没有任何时间字段，所以它们的日期是空的。
    空值如果参与正常排序，正序时会全部挤在最前面 —— 用户一进来看到的就是一堆
    没有日期的东西，而他按日期排序恰恰是想先看最近的。
    """
    from datetime import UTC, datetime

    from app.models.file import CourseFile

    course = await _page_course(db, 9120)
    db.add_all(
        [
            CourseFile(
                canvas_id=91200,
                course_id=course.id,
                display_name="有日期的.pdf",
                display_date=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            CourseFile(
                canvas_id=91201,
                course_id=course.id,
                display_name="大纲里的链接",
                display_date=None,
            ),
            CourseFile(
                canvas_id=91202,
                course_id=course.id,
                display_name="另一个有日期的.pdf",
                display_date=datetime(2026, 6, 1, tzinfo=UTC),
            ),
        ]
    )
    await db.commit()

    for order in ("asc", "desc"):
        rows = (
            await client.get(
                f"/api/v1/courses/{course.id}/files?sort_by=date&order={order}"
            )
        ).json()
        assert rows[-1]["canvas_id"] == 91201, f"order={order} 时没日期的没排到最后"
