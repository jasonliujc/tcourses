"""在设置页改 Canvas 地址和令牌。

- 和当前的一样 → 什么都不做，连 Canvas 都不问
- 变了 → 先用新凭据问一次"我是谁"，保存后同步所有课程
- 换成另一个账号 → 先清掉上一个账号的数据，自建任务留着
- 同步进行中 → 不许改
- 令牌本身永远不出接口
"""

import pytest
from sqlalchemy import select

from app.api.v1 import settings as settings_api
from app.core.config import settings as real_settings
from app.integrations.canvas.client import CanvasAuthError, CanvasUnreachable
from app.models.course import Course
from app.models.setting import AppSetting
from app.models.tasks import UserTask
from app.services import canvas_connection
from app.services.canvas_connection import Account
from app.workers.scheduler import sync_manager

URL = "https://canvas.example.edu"
TOKEN_A1 = "token-account-a-1111"
TOKEN_A2 = "token-account-a-2222"
TOKEN_B = "token-account-b-3333"

_ACCOUNTS = {
    TOKEN_A1: Account(key="canvas.example.edu#1", name="Alex"),
    TOKEN_A2: Account(key="canvas.example.edu#1", name="Alex"),
    TOKEN_B: Account(key="canvas.example.edu#2", name="Blair"),
}


@pytest.fixture
async def canvas(monkeypatch, db):
    """一个已经连着账号 A 的环境。Canvas 和同步都是假的。"""
    keys = (
        canvas_connection.BASE_URL_KEY,
        canvas_connection.TOKEN_KEY,
        canvas_connection.ACCOUNT_KEY,
        canvas_connection.ACCOUNT_NAME_KEY,
    )

    async def clear_keys():
        for row in (
            await db.execute(select(AppSetting).where(AppSetting.key.in_(keys)))
        ).scalars():
            await db.delete(row)
        await db.commit()

    await clear_keys()
    for row in (await db.execute(select(Course))).scalars():
        await db.delete(row)
    await db.commit()

    # 测试结束时把进程里的凭据还原
    monkeypatch.setattr(real_settings, "canvas_base_url", URL)
    monkeypatch.setattr(real_settings, "canvas_access_token", TOKEN_A1)

    asked: list[str] = []

    async def fake_identify(base_url, token):
        asked.append(token)
        if token == "rejected-token-0000":
            raise CanvasAuthError("401")
        if base_url == "https://down.example.edu":
            raise CanvasUnreachable("connect timeout")
        return _ACCOUNTS[token]

    started: list[bool] = []

    async def fake_run_sync(*_args, **_kwargs):
        started.append(True)
        return {}

    monkeypatch.setattr(canvas_connection, "identify", fake_identify)
    monkeypatch.setattr(sync_manager, "mark_started", lambda: None)
    monkeypatch.setattr(sync_manager, "run_sync", fake_run_sync)

    yield {"asked": asked, "started": started}
    await clear_keys()


async def _check(client, **body):
    return await client.post("/api/v1/settings/canvas/check", json=body)


async def _save(client, **body):
    return await client.put("/api/v1/settings/canvas", json=body)


# ---------- 地址 ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("canvas.example.edu", "https://canvas.example.edu"),
        ("https://Canvas.Example.edu/", "https://canvas.example.edu"),
        ("https://canvas.example.edu/courses/12/pages/week-1", "https://canvas.example.edu"),
        ("http://localhost:3000", "http://localhost:3000"),
    ],
)
def test_url_keeps_only_scheme_and_host(raw, expected):
    assert canvas_connection.normalize_url(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://canvas.example.edu", "not a url"])
def test_bad_urls_are_rejected(raw):
    with pytest.raises(ValueError):
        canvas_connection.normalize_url(raw)


async def test_bad_url_is_a_400_with_a_code(client, canvas):
    resp = await _check(client, base_url="ftp://nope", token=TOKEN_A2)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_canvas_url"


# ---------- 没变就没有动静 ----------


async def test_same_token_does_nothing(client, canvas):
    resp = await _check(client, base_url=URL + "/", token=TOKEN_A1)
    assert resp.json() == {"changed": False, "user_name": None, "account_changed": False}

    resp = await _save(client, base_url=URL, token=TOKEN_A1)
    assert resp.json()["changed"] is False
    assert canvas["asked"] == [] and canvas["started"] == []


async def test_blank_token_means_keep_the_current_one(client, canvas):
    resp = await _save(client, base_url=URL, token="")
    assert resp.json()["changed"] is False
    assert real_settings.canvas_access_token == TOKEN_A1


async def test_no_token_at_all_is_rejected(client, canvas, monkeypatch):
    monkeypatch.setattr(real_settings, "canvas_access_token", "")
    resp = await _check(client, base_url=URL)
    assert resp.json()["detail"]["code"] == "canvas_token_required"


# ---------- 变了 ----------


async def test_new_token_for_the_same_account_keeps_data_and_syncs(client, canvas, db):
    db.add(Course(canvas_id=101, name="Databases", sync_enabled=True))
    await db.commit()

    check = (await _check(client, base_url=URL, token=TOKEN_A2)).json()
    assert check == {"changed": True, "user_name": "Alex", "account_changed": False}
    # 检查不落库
    assert real_settings.canvas_access_token == TOKEN_A1

    saved = (await _save(client, base_url=URL, token=TOKEN_A2)).json()
    assert saved["changed"] and saved["sync_started"] and saved["removed_courses"] == 0
    assert canvas["started"] == [True]
    # 进程里立刻换成新的，不用重启
    assert real_settings.canvas_access_token == TOKEN_A2
    assert (await db.execute(select(Course))).scalars().all()


async def test_saved_connection_survives_a_restart(client, canvas, db, monkeypatch):
    await _save(client, base_url=URL, token=TOKEN_A2)
    # 模拟重启：进程里回到 .env 的值，再从库里读
    monkeypatch.setattr(real_settings, "canvas_access_token", "")
    await canvas_connection.load(db)
    assert real_settings.canvas_access_token == TOKEN_A2
    assert real_settings.canvas_base_url == URL


async def test_first_setup_is_not_an_account_switch(client, canvas, monkeypatch):
    monkeypatch.setattr(real_settings, "canvas_access_token", "")
    check = (await _check(client, base_url=URL, token=TOKEN_B)).json()
    assert check["changed"] and not check["account_changed"]


# ---------- 换账号 ----------


async def test_switching_account_clears_the_old_one_but_keeps_my_tasks(client, canvas, db):
    await _save(client, base_url=URL, token=TOKEN_A2)  # 记下账号 A
    course = Course(canvas_id=101, name="Databases", sync_enabled=True)
    db.add(course)
    await db.commit()
    task = (
        await client.post("/api/v1/tasks", json={"title": "Read ch.3", "course_id": course.id})
    ).json()

    check = (await _check(client, base_url=URL, token=TOKEN_B)).json()
    assert check == {"changed": True, "user_name": "Blair", "account_changed": True}

    saved = (await _save(client, base_url=URL, token=TOKEN_B)).json()
    assert saved["account_changed"] and saved["removed_courses"] == 1

    db.expire_all()
    assert (await db.execute(select(Course))).scalars().all() == []
    kept = await db.get(UserTask, task["id"])
    assert kept is not None and kept.course_id is None
    await db.delete(kept)
    await db.commit()


async def test_legacy_env_setup_asks_canvas_who_the_current_account_is(client, canvas, db):
    """老版本只配过 .env，库里没记账号：用当前凭据补问一次。"""
    db.add(Course(canvas_id=101, name="Databases", sync_enabled=True))
    await db.commit()

    check = (await _check(client, base_url=URL, token=TOKEN_B)).json()
    assert check["account_changed"] is True
    assert canvas["asked"] == [TOKEN_B, TOKEN_A1]


# ---------- 出错 ----------


async def test_rejected_token(client, canvas):
    resp = await _check(client, base_url=URL, token="rejected-token-0000")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "canvas_auth_failed"


async def test_unreachable_host(client, canvas):
    resp = await _save(client, base_url="https://down.example.edu", token=TOKEN_A2)
    assert resp.json()["detail"]["code"] == "canvas_unreachable"
    assert real_settings.canvas_base_url == URL
    assert canvas["started"] == []


async def test_locked_while_syncing(client, canvas, monkeypatch):
    monkeypatch.setattr(sync_manager, "is_running", True)
    resp = await _save(client, base_url=URL, token=TOKEN_A2)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "settings_locked"
    assert real_settings.canvas_access_token == TOKEN_A1


# ---------- 令牌不出接口 ----------


async def test_settings_show_only_the_last_four_characters(client, canvas):
    await _save(client, base_url=URL, token=TOKEN_A2)
    body = (await client.get("/api/v1/settings")).json()
    assert body["canvas_token_hint"] == "2222"
    assert body["canvas_user_name"] == "Alex"
    assert body["canvas_configured"] is True
    assert TOKEN_A2 not in str(body)


def test_settings_module_reads_the_live_settings_object():
    # 换令牌是改进程里那个单例；设置接口读的必须是同一个对象
    assert settings_api.env_settings is real_settings
