"""演示模式：生成的学期和模拟的 Canvas。

完整的启动流程（两轮同步 + 用户数据）要一个独立的数据库，由启动器负责；
这里钉住它依赖的几件事：两轮之间 id 不变、不出现未来的文件、正文里的链接
都指得到文件、模拟的接口和真实客户端接得上。
"""

import re
from datetime import UTC, datetime

import pytest

from app.demo.semester import BASE_URL, Semester
from app.demo.transport import DemoCanvas
from app.integrations.canvas import client as canvas_client
from app.integrations.canvas.client import CanvasClient, CanvasNotAvailable

NOW = datetime(2026, 11, 17, 20, 0, tzinfo=UTC)


@pytest.fixture
def demo():
    canvas = DemoCanvas(NOW)
    canvas_client.install_transport(canvas.transport())
    yield canvas
    canvas_client.install_transport(None)


# ---------- 学期 ----------


def test_ids_are_stable_between_stages():
    before, after = Semester(NOW, 0), Semester(NOW, 1)
    assert all(after.files[i].name == f.name for i, f in before.files.items())
    new = sorted(after.files[i].name for i in after.files.keys() - before.files.keys())
    assert len(new) == 4 and "hw4_clarifications.pdf" in new


def test_no_file_is_uploaded_in_the_future():
    for stage in (0, 1):
        s = Semester(NOW, stage)
        assert all(f.created <= NOW for f in s.files.values())


def test_every_link_in_a_body_points_at_a_real_file():
    s = Semester(NOW, 1)
    bodies = [s.courses[c].syllabus for c in s.courses]
    for c in s.active:
        bodies += [p["body"] for p in c.pages]
        bodies += [a["description"] for a in c.assignments]
        bodies += [q["description"] for q in c.quizzes]
        bodies += [a["message"] for a in c.announcements]
    linked = {int(m) for b in bodies for m in re.findall(r"/files/(\d+)", b or "")}
    assert linked and linked <= s.files.keys()


def test_today_stage_changes_a_deadline_instead_of_adding_a_copy():
    def hw4(s):
        return next(a for c in s.active for a in c.assignments if a["name"] == "HW 4")

    before, after = hw4(Semester(NOW, 0)), hw4(Semester(NOW, 1))
    assert before["id"] == after["id"] and before["due_at"] < after["due_at"]


# ---------- 模拟的 Canvas ----------


async def test_real_client_talks_to_the_simulated_canvas(demo):
    async with CanvasClient(BASE_URL, "demo") as client:
        profile = await client.probe()
        active = await client.list_courses("active")
        done = await client.list_courses("completed")
    assert profile["name"] == "Demo Student"
    assert len(active) == 4 and len(done) == 2
    assert all(c["enrollments"][0]["enrollment_state"] == "active" for c in active)


async def test_files_tab_off_means_403_but_single_files_still_work(demo):
    s = demo.semester
    hidden = next(c for c in s.active if not c.files_tab)
    async with CanvasClient(BASE_URL, "demo") as client:
        with pytest.raises(CanvasNotAvailable):
            await client.list_files(hidden.id)
        meta = await client.get_file(hidden.files[0])
    assert meta["id"] == hidden.files[0]


async def test_downloads_are_real_files_of_the_advertised_size(demo, tmp_path):
    s = demo.semester
    pdf = next(f for f in s.files.values() if f.kind == "pdf")
    async with CanvasClient(BASE_URL, "demo") as client:
        meta = await client.get_file(pdf.id)
        size = await client.download_file(meta["url"], tmp_path / "x.pdf", 10_000_000)
    data = (tmp_path / "x.pdf").read_bytes()
    assert size == meta["size"] == len(data)
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")


async def test_planner_can_be_scoped_to_one_course(demo):
    async with CanvasClient(BASE_URL, "demo") as client:
        everything = await client.list_planner_items("2026-01-01", "2027-12-31")
        one = await client.list_planner_items("2026-01-01", "2027-12-31", ["course_41001"])
    assert 0 < len(one) < len(everything)
    assert {i["course_id"] for i in one} == {41001}
    # 不挂课程的个人待办只在全量里
    assert any(i["course_id"] is None for i in everything)


async def test_second_stage_brings_new_announcement_and_message(demo):
    async with CanvasClient(BASE_URL, "demo") as client:
        before = await client.list_announcements(41004)
        demo.set_stage(1)
        after = await client.list_announcements(41004)
        inbox = await client.list_conversations()
    assert len(after) == len(before) + 1 and after[0]["attachments"]
    assert len(inbox) == 2


async def test_unknown_endpoints_look_like_disabled_features(demo):
    async with CanvasClient(BASE_URL, "demo") as client:
        with pytest.raises(CanvasNotAvailable):
            await client.get_one("/courses/41001/rubrics")
        override = await client.create_planner_override("assignment", "1", True)
    assert override["marked_complete"] is True
