"""在 HTTP 层模拟 Canvas：`httpx` 的 transport 把请求按 REST 路径分给 `Semester`。

装进 `CanvasClient` 之后，客户端本身（分页、重试、限流退避、流式下载）和整条
同步链路都照常运行，只是对面换成了这个函数。没实现的路径一律 404 ——
和真实 Canvas 上"学校关掉了这个功能"是同一个形状，收集器本来就要处理它。
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import httpx

from app.demo.semester import USER, Semester, file_bytes


def _json(data, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(data).encode(),
        headers={"content-type": "application/json; charset=utf-8"},
    )


def _not_found() -> httpx.Response:
    return _json({"errors": [{"message": "The specified resource does not exist."}]}, 404)


class DemoCanvas:
    def __init__(self, now: datetime):
        self.now = now
        self.stage = 0
        self.semester = Semester(now, 0)
        self._override_id = 0

    def set_stage(self, stage: int) -> None:
        self.stage = stage
        self.semester = Semester(self.now, stage)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ---------- 路由 ----------

    async def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        s = self.semester

        m = re.fullmatch(r"/files/(\d+)/download", path)
        if m:
            f = s.files.get(int(m.group(1)))
            if f is None:
                return _not_found()
            data = file_bytes(f, s.courses[f.course].name)
            return httpx.Response(
                200,
                content=data,
                headers={
                    "content-type": s.file_json(f)["content-type"],
                    "content-length": str(len(data)),
                    "content-disposition": f'attachment; filename="{f.name}"',
                },
            )

        if not path.startswith("/api/v1/"):
            return _not_found()
        path = path.removeprefix("/api/v1")

        if request.method == "POST" and path == "/planner/overrides":
            self._override_id += 1
            body = json.loads(request.content or b"{}")
            return _json({"id": self._override_id, **body})
        m = re.fullmatch(r"/planner/overrides/(\d+)", path)
        if request.method == "PUT" and m:
            return _json({"id": int(m.group(1)), **json.loads(request.content or b"{}")})
        if request.method != "GET":
            return _not_found()

        if path == "/users/self/profile":
            return _json(USER)
        if path == "/users/self/colors":
            return _json({"custom_colors": {f"course_{c.id}": c.color for c in s.courses.values()}})
        if path == "/courses":
            concluded = params.get("enrollment_state") == "completed"
            return _json([self._course(c) for c in s.courses.values() if c.concluded == concluded])
        if path == "/conversations":
            return _json(s.conversations)
        if path == "/planner/items":
            wanted = params.get_list("context_codes[]")
            items = s.planner
            if wanted:
                ids = {
                    int(code.removeprefix("course_"))
                    for code in wanted
                    if code.startswith("course_")
                }
                items = [i for i in items if i.get("course_id") in ids]
            return _json(items)
        if path == "/calendar_events":
            code = params.get("context_codes[]") or ""
            course = s.courses.get(int(code.removeprefix("course_") or 0))
            return _json(course.events if course else [])

        m = re.fullmatch(r"/files/(\d+)", path)
        if m:
            f = s.files.get(int(m.group(1)))
            return _json(s.file_json(f)) if f else _not_found()
        m = re.fullmatch(r"/folders/(\d+)", path)
        if m:
            for c in s.courses.values():
                for folder in c.folders:
                    if folder["id"] == int(m.group(1)):
                        return _json(folder)
            return _not_found()

        m = re.fullmatch(r"/courses/(\d+)(/.*)?", path)
        if not m:
            return _not_found()
        course = s.courses.get(int(m.group(1)))
        if course is None or course.concluded:
            return _not_found()
        rest = m.group(2) or ""
        if rest == "":
            return _json({**self._course(course), "syllabus_body": course.syllabus})
        if rest == "/tabs":
            return _json(course.tabs)
        if rest == "/external_tools":
            return _json(course.external_tools)
        if rest == "/modules":
            return _json(course.modules)
        if rest == "/assignments":
            return _json(course.assignments)
        if rest == "/quizzes":
            return _json(course.quizzes)
        if rest == "/discussion_topics":
            if params.get("only_announcements") in ("true", "True", "1"):
                return _json(course.announcements)
            return _json(course.discussions)
        if rest == "/pages":
            return _json([{k: v for k, v in p.items() if k != "body"} for p in course.pages])
        if rest == "/front_page":
            return _json(course.front_page) if course.front_page else _not_found()
        page = re.fullmatch(r"/pages/([^/]+)", rest)
        if page:
            hit = next((p for p in course.pages if p["url"] == page.group(1)), None)
            return _json(hit) if hit else _not_found()
        if rest in ("/files", "/folders"):
            # 学校关掉 Files 标签页时，列表接口就是 403 —— 单个文件接口照样能用
            if not course.files_tab:
                return _json({"status": "unauthorized"}, 403)
            if rest == "/folders":
                return _json(course.folders)
            listed = [s.files[i] for i in course.files if s.files[i].folder]
            return _json([s.file_json(f) for f in listed])
        return _not_found()

    def _course(self, c) -> dict:
        s = self.semester
        return {
            "id": c.id,
            "name": c.name,
            "course_code": c.code,
            "workflow_state": "available",
            "term": {"name": s.term if not c.concluded else "Winter " + str(s.term_start.year)},
            "enrollments": [
                {"type": "student", "enrollment_state": "completed" if c.concluded else "active"}
            ],
            "start_at": c.start.isoformat() if c.start else None,
            "end_at": c.end.isoformat() if c.end else None,
            "is_favorite": not c.concluded,
        }
