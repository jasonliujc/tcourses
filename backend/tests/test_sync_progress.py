"""并发同步时的进度上报。

同步是**课程级并发**的（默认 4 门同时跑），所以"当前在同步哪门课"从来就不是
一门。早先进度里只存一个名字：谁开始谁覆盖、谁结束谁清空 —— 于是任何一门课
跑完都会把名字抹掉，界面上剩一个光秃秃的"正在同步…"，而那一刻其实还有三门在跑。

这个文件盯住两条：**有课在跑时名单不能为空**，**跑完的课要及时摘掉**。
"""

import asyncio

from sqlalchemy import select

from app.integrations.canvas.client import CanvasError
from app.integrations.canvas.sync import CanvasSyncService
from app.models.course import Course


class _FakeClient:
    """只实现 sync_all 直接用到的两个方法。"""

    def __init__(self, course_count: int):
        self._courses = [{"id": 8000 + i, "name": f"课程 {i}"} for i in range(course_count)]

    async def list_courses(self, enrollment_state: str = "active") -> list[dict]:
        return self._courses if enrollment_state == "active" else []

    async def list_conversations(self, *_args, **_kwargs):
        raise CanvasError("收件箱不测")


async def _clear_courses(db) -> None:
    for course in (await db.execute(select(Course))).scalars().all():
        await db.delete(course)
    await db.commit()


async def _run_and_capture(db, monkeypatch, course_count: int, concurrency: int) -> list[dict]:
    """跑一轮 sync_all，把每一次进度上报都记下来。

    每条快照还带上"上报那一刻真正卡在 sync_one_course 里的课"（`live`），
    用来核对上报的名单和事实一致。
    """
    from app.core.config import settings as env_settings

    await _clear_courses(db)
    monkeypatch.setattr(env_settings, "sync_concurrency", concurrency)

    live: set[str] = set()

    async def fake_sync_one_course(self, course):
        live.add(course.name)
        # 真同步是等网络，这里让出事件循环来模拟"还没跑完"。
        #
        # **每门课睡不一样长**：睡同样长的话，同时起跑的那几门会在同一个事件循环
        # tick 上一起醒来、一起跑完，于是第一次进度上报时 live 已经空了 ——
        # 用例会随机失败在"整轮都没抓到在跑的课"上。现实里更不可能齐步走。
        await asyncio.sleep(0.005 * (1 + course.canvas_id % 5))
        live.discard(course.name)

    monkeypatch.setattr(CanvasSyncService, "sync_one_course", fake_sync_one_course)

    seen: list[dict] = []

    def on_progress(done: int, total: int, courses: list[str]) -> None:
        # 回调是同步执行的，中途不会被其他协程插入，所以这里读到的 live 就是当时的
        seen.append({"done": done, "total": total, "courses": courses, "live": set(live)})

    service = CanvasSyncService(db, _FakeClient(course_count), on_progress=on_progress)
    await service.sync_all()
    return seen


async def test_progress_lists_every_course_actually_running(db, monkeypatch):
    """回归：一门课跑完不该把其他还在跑的课从进度里抹掉。

    准确的不变量是"正卡在 sync_one_course 里的课，必须都出现在上报的名单里"。
    不能写成"名单永远非空" —— 一批课同时结束、下一批还没抢到信号量的那一瞬间
    名单确实是空的，那时也确实没有课在跑，报空才是对的。
    """
    seen = await _run_and_capture(db, monkeypatch, course_count=6, concurrency=3)

    assert seen, "至少要上报一次进度"
    for snapshot in seen:
        missing = snapshot["live"] - set(snapshot["courses"])
        assert not missing, f"这些课正在跑却没出现在进度里: {missing}（快照 {snapshot}）"

    # 而且确实有过"有课在跑"的时刻，否则上面的断言是空转
    assert any(s["live"] for s in seen), "整轮都没抓到在跑的课，用例没测到东西"


async def test_progress_lists_every_concurrent_course(db, monkeypatch):
    """并发度是几，名单最多就该有几门 —— 而且确实会同时出现多门。"""
    seen = await _run_and_capture(db, monkeypatch, course_count=6, concurrency=3)

    widest = max(len(s["courses"]) for s in seen)
    assert widest > 1, "并发 3 跑 6 门课，进度里应该出现过同时多门"
    assert widest <= 3, f"名单里的课不该超过并发度，实际 {widest}"


async def test_progress_is_empty_only_at_the_end(db, monkeypatch):
    """跑完的课要摘掉：最后一次上报是 done == total 且名单为空。"""
    seen = await _run_and_capture(db, monkeypatch, course_count=4, concurrency=2)

    last = seen[-1]
    assert last["done"] == last["total"] == 4
    assert last["courses"] == [], "全部跑完后名单必须清空"


async def test_progress_snapshots_are_independent(db, monkeypatch):
    """每次上报要存副本。共用同一个 list 的话，前端读到的永远是"此刻"，
    而历史快照会被一起改掉 —— 断言也就测不出任何东西了。"""
    seen = await _run_and_capture(db, monkeypatch, course_count=4, concurrency=2)

    lists = [id(s["courses"]) for s in seen]
    assert len(set(lists)) > 1, "所有快照指向同一个 list，说明没有拷贝"


async def test_failed_course_is_removed_from_progress(db, monkeypatch):
    """一门课抛异常也要从名单里摘掉，否则进度条会一直挂着它。"""
    from app.core.config import settings as env_settings

    await _clear_courses(db)
    monkeypatch.setattr(env_settings, "sync_concurrency", 2)

    async def boom(self, course):
        raise RuntimeError("模拟同步炸了")

    monkeypatch.setattr(CanvasSyncService, "sync_one_course", boom)

    seen: list[dict] = []
    service = CanvasSyncService(
        db,
        _FakeClient(3),
        on_progress=lambda d, t, c: seen.append({"done": d, "total": t, "courses": c}),
    )
    try:
        await service.sync_all()
    except RuntimeError:
        pass  # 异常怎么往上冒不是这个用例关心的

    assert seen
    assert seen[-1]["courses"] == [], f"失败的课没被摘掉: {seen[-1]}"
