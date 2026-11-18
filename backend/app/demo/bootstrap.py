"""演示模式启动时做的事：接上模拟的 Canvas，同步两轮，再铺一层用户数据。

1. 装上 `DemoCanvas` 的 transport，凭据设成演示账号
2. 按"几天前"同步一轮（冷启动：只入库，不产生提醒）
3. 按"今天"再同步一轮 —— 新讲义、新公告、改了截止的作业、新私信
   自然变成「更新」和提醒
4. 通过应用自己的 REST 接口加星标、个人分组、清单、自建任务和几处日历改动

全程不碰网络，几秒钟跑完。数据库是启动器给的一次性文件，每次打开都重新生成。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta

import httpx
from sqlalchemy import select

from app.db.session import SessionLocal
from app.demo.semester import BASE_URL, USER, Semester
from app.demo.transport import DemoCanvas
from app.integrations.canvas import client as canvas_client
from app.models.course import Course
from app.models.file import CourseFile
from app.models.planner import PlannerItem
from app.services import canvas_connection
from app.services.canvas_connection import Account
from app.workers.scheduler import sync_manager

logger = logging.getLogger(__name__)

DEMO_TOKEN = "demo-token-0000"


async def prepare() -> None:
    demo = DemoCanvas(datetime.now(UTC))
    canvas_client.install_transport(demo.transport())
    async with SessionLocal() as db:
        await canvas_connection.save(
            db,
            BASE_URL,
            DEMO_TOKEN,
            Account(key=f"canvas.example.edu#{USER['id']}", name=USER["name"]),
        )
        await db.commit()

    await sync_manager.run_sync(reschedule=False)
    demo.set_stage(1)
    await sync_manager.run_sync(reschedule=False)

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://demo") as api:
        await _seed_user_data(api, demo.semester)
    logger.info("演示数据就绪")


async def _ids(model, column, names: list[str]) -> dict[str, int]:
    async with SessionLocal() as db:
        rows = (await db.execute(select(column, model.id).where(column.in_(names)))).all()
    return {name: id_ for name, id_ in rows}


async def _seed_user_data(api: httpx.AsyncClient, s: Semester) -> None:
    async def call(method: str, path: str, body: dict | None = None) -> dict:
        resp = await api.request(method, f"/api/v1{path}", json=body)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    courses = await _ids(
        Course, Course.course_code, ["CS 3200", "STAT 3010", "CS 4100", "MATH 2210"]
    )
    files = await _ids(
        CourseFile,
        CourseFile.display_name,
        [
            "Midterm Practice.pdf",
            "Midterm Practice Solutions.pdf",
            "Course Outline.pdf",
            "Midterm Review Sheet.pdf",
            "housing.csv",
            "Reading - Chapter 3.pdf",
            "Lecture 05 - Relational algebra.pdf",
            "Lecture 08 - Normalization.pdf",
        ],
    )

    # 星标和一个个人分组
    await call(
        "POST",
        "/resources/star",
        {
            "file_ids": [
                files[n]
                for n in (
                    "Midterm Practice.pdf",
                    "Course Outline.pdf",
                    "Midterm Review Sheet.pdf",
                    "housing.csv",
                )
                if n in files
            ]
        },
    )
    group = await call("POST", f"/courses/{courses['CS 3200']}/user-groups", {"name": "Exam prep"})
    await call(
        "POST",
        f"/user-groups/{group['id']}/files",
        {
            "file_ids": [
                files[n]
                for n in (
                    "Midterm Practice.pdf",
                    "Midterm Practice Solutions.pdf",
                    "Lecture 05 - Relational algebra.pdf",
                    "Lecture 08 - Normalization.pdf",
                )
                if n in files
            ]
        },
    )

    # 清单和自建任务
    personal = await call("POST", "/task-lists", {"name": "Personal", "color": "#0891B2"})
    research = await call("POST", "/task-lists", {"name": "Research", "color": "#DB2777"})
    today = datetime.now(s.local).date()

    def at(days: int, hh: int, mm: int = 0) -> str:
        moment = datetime.combine(today + timedelta(days=days), time(hh, mm), tzinfo=s.local)
        return moment.astimezone(UTC).isoformat()

    def day(days: int) -> str:
        return (today + timedelta(days=days)).isoformat()

    tasks = [
        {
            "title": "Review lecture notes",
            "due_date": day(0),
            "course_id": courses["CS 3200"],
            "repeat": {"freq": "weekly", "interval": 1},
            "tz": "UTC",
            "steps": [
                {"title": "Relational algebra", "done": True},
                {"title": "Normalization", "done": False},
            ],
        },
        {
            "title": "Email TA about regrade",
            "due_date": day(0),
            "important": True,
            "course_id": courses["STAT 3010"],
        },
        {
            "title": "Prepare lab meeting slides",
            "due_at": at(2, 14),
            "list_id": research["id"],
            "note": "Results from the ablation runs.",
        },
        {
            "title": "Read two papers for reading group",
            "due_date": day(4),
            "list_id": research["id"],
            "steps": [
                {"title": "Attention Is All You Need", "done": True},
                {"title": "Deep Residual Learning", "done": False},
            ],
        },
        {
            "title": "Gym",
            "due_date": day(1),
            "list_id": personal["id"],
            "repeat": {"freq": "weekly", "interval": 1, "weekdays": [0, 2, 4]},
            "tz": "UTC",
        },
        {"title": "Renew library books", "due_date": day(3), "list_id": personal["id"]},
        {
            "title": "Start final project outline",
            "due_date": day(6),
            "course_id": courses["CS 4100"],
        },
    ]
    for task in tasks:
        await call("POST", "/tasks", task)
    done = await call(
        "POST",
        "/tasks",
        {"title": "Submit course survey", "due_date": day(-1), "list_id": personal["id"]},
    )
    await call("PATCH", f"/tasks/{done['id']}", {"completed": True})

    # 日历条目上的几处改动：重点、备注、手动标完成
    async with SessionLocal() as db:
        rows = (await db.execute(select(PlannerItem))).scalars().all()
    by_title = {}
    for row in rows:
        by_title.setdefault(row.title, row)
    if (a := by_title.get("Assignment 4")) is not None:
        await call("PATCH", f"/planner/items/{a.id}", {"important": True})
    if (e := by_title.get("CS 4100 Midterm")) is not None:
        await call(
            "PATCH", f"/planner/items/{e.id}", {"note": "Bring a calculator and student ID."}
        )
    if (n := by_title.get("Book an academic advising appointment")) is not None:
        await call("PATCH", f"/planner/items/{n.id}", {"important": True})
