"""「课程管理」——边栏显示哪些课、按什么顺序。

两条不变量：

1. **隐藏 ≠ 取消同步。** 藏起来的课照常同步、资料一条不少，只是不占边栏的位置。
   用取消同步来给边栏清场会把资料一起删掉，那是另一件事。
2. **新课不许插队。** `sidebar_sort_order == 0` 表示"从没排过"，它必须排在
   排过的课**后面** —— 否则每学期同步出一门新课，用户排好的顺序就自己坏一次。
"""

import pytest
from sqlalchemy import select

from app.models.course import Course


async def _courses(db, *names) -> list[Course]:
    for course in (await db.execute(select(Course))).scalars().all():
        await db.delete(course)
    await db.flush()

    out = []
    for i, name in enumerate(names, start=1):
        course = Course(canvas_id=9000 + i, name=name, course_code=name, sync_enabled=True)
        db.add(course)
        out.append(course)
    await db.commit()
    for course in out:
        await db.refresh(course)
    return out


@pytest.fixture
async def three(db):
    return await _courses(db, "BBB", "AAA", "CCC")


async def test_defaults_to_alphabetical(client, three):
    """没排过的时候按显示名的字母序 —— 不排也有一个稳定顺序。"""
    rows = (await client.get("/api/v1/courses")).json()
    assert [r["name"] for r in rows] == ["AAA", "BBB", "CCC"]


async def test_manual_order_is_kept(client, db, three):
    order = [three[2].id, three[0].id, three[1].id]  # CCC, BBB, AAA
    res = await client.put(
        "/api/v1/courses/sidebar",
        json={"items": [{"course_id": cid, "is_hidden": False} for cid in order]},
    )
    assert res.status_code == 200
    assert res.json() == {"visible": 3, "hidden": 0}

    rows = (await client.get("/api/v1/courses")).json()
    assert [r["name"] for r in rows] == ["CCC", "BBB", "AAA"]


async def test_hidden_course_leaves_sidebar_but_keeps_syncing(client, db, three):
    # 先把 id 取成普通整数：expire_all 之后再读 ORM 属性会触发一次同步 IO，
    # 在 async 会话里直接炸 MissingGreenlet
    hidden_id = three[0].id  # BBB
    ids = [c.id for c in three]
    await client.put(
        "/api/v1/courses/sidebar",
        json={"items": [{"course_id": cid, "is_hidden": cid == hidden_id} for cid in ids]},
    )

    listed = (await client.get("/api/v1/courses")).json()
    assert [r["name"] for r in listed] == ["AAA", "CCC"]

    # 藏起来只动 is_hidden，同步开关一动不动
    db.expire_all()
    row = await db.get(Course, hidden_id)
    assert row.is_hidden is True
    assert row.sync_enabled is True

    # 课程管理页要连藏起来的课一起列出来，否则藏了就改不回来
    all_rows = (await client.get("/api/v1/courses?include_hidden=true")).json()
    assert [r["name"] for r in all_rows] == ["BBB", "AAA", "CCC"]


async def test_new_course_does_not_jump_to_top(client, db, three):
    """新同步出来的课排在已排序的课后面，而不是凭空跳到最上面。"""
    await client.put(
        "/api/v1/courses/sidebar",
        json={"items": [{"course_id": c.id, "is_hidden": False} for c in three]},
    )
    db.add(Course(canvas_id=9999, name="AAAA-new", course_code="AAAA-new", sync_enabled=True))
    await db.commit()

    rows = (await client.get("/api/v1/courses")).json()
    assert [r["name"] for r in rows] == ["BBB", "AAA", "CCC", "AAAA-new"]


async def test_unsynced_course_is_ignored(client, db, three):
    """没在同步的课不进边栏 —— 它在库里只是一条目录记录，点进去是空的。"""
    ghost = Course(canvas_id=8888, name="ZZZ", course_code="ZZZ", sync_enabled=False)
    db.add(ghost)
    await db.commit()
    await db.refresh(ghost)

    res = await client.put(
        "/api/v1/courses/sidebar",
        json={
            "items": [{"course_id": ghost.id, "is_hidden": False}]
            + [{"course_id": c.id, "is_hidden": False} for c in three]
        },
    )
    assert res.json() == {"visible": 3, "hidden": 0}

    rows = (await client.get("/api/v1/courses?include_hidden=true")).json()
    assert "ZZZ" not in [r["name"] for r in rows]
