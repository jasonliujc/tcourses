"""接口不出人话，只出 key。

界面有中文和英文两套，而它们打的是同一个后端。只要后端返回一句拼好的中文，
英文界面上就必然中英混排 —— 而且像"提醒"这种写进库里的文案，切语言之后
再也改不了。所以这里钉住三件事：板块状态、结果说明、报错，一律只给 key。
"""

import pytest
from sqlalchemy import select

from app.models.course import Course
from app.models.system import SyncState
from app.services import boards


@pytest.fixture
async def course_with_coverage(db):
    for row in (await db.execute(select(Course))).scalars().all():
        await db.delete(row)
    for row in (await db.execute(select(SyncState))).scalars().all():
        await db.delete(row)
    await db.flush()

    course = Course(canvas_id=7001, name="I18N101", course_code="I18N101", sync_enabled=True)
    db.add(course)
    await db.flush()
    db.add(
        SyncState(
            resource_key=f"course:{course.canvas_id}:coverage",
            payload={
                "boards": [
                    {"id": boards.FILES, "label": "Files", "status": boards.SCANNED, "count": 3},
                    {
                        "id": boards.OTHERS,
                        "label": "Others",
                        "status": boards.UNSUPPORTED,
                        "count": 0,
                    },
                ],
                "unsupported": ["Piazza"],
            },
        )
    )
    await db.commit()
    return course


async def test_board_rows_carry_status_not_prose(client, course_with_coverage):
    rows = (await client.get("/api/v1/sync/courses")).json()
    boards_out = rows[0]["boards"]
    assert [b["status"] for b in boards_out] == ["scanned", "unsupported"]
    # 说法在前端的词典里 —— 后端一个字都不该拼
    assert all("status_label" not in b for b in boards_out)
    # 板块名和"哪几个没扫"是数据不是文案：前者全站统一英文，
    # 后者直接来自 Canvas 的导航栏，翻译反而会对不上用户在 Canvas 上看到的
    assert boards_out[0]["label"] == "Files"
    assert boards_out[1]["detail"] == "Piazza"


async def test_settings_notes_are_keys_with_numbers(client):
    res = (
        await client.patch("/api/v1/settings", json={"sync_interval_minutes": 30})
    ).json()
    assert res["notes"] == [{"key": "interval_rescheduled", "params": {}}]


async def test_errors_carry_code_and_data(client):
    res = await client.get("/api/v1/courses/999999")
    assert res.status_code == 404
    assert res.json()["detail"] == {"code": "course_not_found", "params": {}}
