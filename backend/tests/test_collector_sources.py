"""收集器的来源覆盖 —— 特别是两条后补上的。

写这个文件的直接原因：有一门课在界面上是 0 个资源，而它的 Canvas 页面上有一大堆
文件和链接。查下去发现整门课的资料都堆在**课程大纲**里，而大纲挂在课程对象上，
既不是页面也不是作业，前面几路一条都碰不到。同一次排查还发现模块里的
`ExternalUrl` 条目（Gradescope / Piazza / YouTube 播放列表）也从来没被收过。

两条都是"用户在 Canvas 上看得见、我们这里一片空白"，所以钉死。
"""

import pytest

from app.models.file import SourceKind
from app.services import boards
from app.services.file_collector import FileCollector


class _FakeClient:
    """只实现收集器会用到的那几个方法。"""

    base_url = "https://canvas.example.edu"

    def __init__(self, syllabus: str = "", modules: list | None = None):
        self._syllabus = syllabus
        self._modules = modules or []
        self.calls: list[str] = []

    async def get_syllabus(self, course_id: int) -> str:
        self.calls.append("syllabus")
        return self._syllabus

    async def list_modules_with_items(self, course_id: int) -> list:
        return self._modules

    async def list_files(self, course_id: int):
        from app.integrations.canvas.client import CanvasNotAvailable

        raise CanvasNotAvailable("403")

    async def list_folders(self, course_id: int):
        return []


def _collector(**kwargs) -> FileCollector:
    return FileCollector(_FakeClient(**kwargs), 394977, "CS236")


async def test_syllabus_files_and_links_are_collected():
    """回归：这门课 pages 404、没作业、没模块，**全部资料都在大纲里**。

    大纲这一路缺失时界面上显示 0 个资源，而用户在 Canvas 上看得见一整页东西。
    """
    body = (
        '<p>讲义 <a href="/courses/394977/files/123">Week 1 Notes</a></p>'
        '<p>录播 <a href="https://youtu.be/abc">Lecture 1</a></p>'
        '<img src="/courses/394977/files/999">'
    )
    c = _collector(syllabus=body)
    await c._from_syllabus()

    assert 123 in c.files
    (src,) = c.files[123].sources
    assert src.source_kind == SourceKind.SYLLABUS
    assert src.container_name == "Syllabus"

    (link,) = c.links.values()
    assert link.title == "Lecture 1"
    assert link.sources[0].source_kind == SourceKind.SYLLABUS

    # 大纲里的 banner 图同样按内联图判定，不能混进资料
    assert c.files[999].sources[0].via_img is True


async def test_syllabus_route_survives_a_disabled_endpoint():
    """大纲取不到是常态（和 Files 一样会被学校关掉），不能让整轮收集炸掉。"""
    c = _collector(syllabus="")
    await c._from_syllabus()
    assert c.stats["syllabus"] == 0


async def test_external_url_module_items_become_links():
    """教授把 Gradescope / Piazza / YouTube 播放列表直接挂成模块条目 ——
    正文里没有任何 <a> 标签，扫富文本永远扫不到。实测有的课有二十多个。"""
    modules = [
        {
            "name": "Course Info",
            "position": 1,
            "items": [
                {
                    "id": 1,
                    "type": "ExternalUrl",
                    "title": "Gradescope",
                    "external_url": "https://www.gradescope.ca/courses/18763",
                    "position": 1,
                },
                {
                    "id": 2,
                    "type": "ExternalUrl",
                    "title": "Pre-Class Videos",
                    "external_url": "https://www.youtube.com/playlist?list=PLxyz",
                    "position": 2,
                },
                # 指回 Canvas 自己的外链要被噪声过滤挡掉 —— 那不是"外部"资料
                {
                    "id": 3,
                    "type": "ExternalUrl",
                    "title": "Midterm Results",
                    "external_url": "https://canvas.example.edu/courses/1/discussion_topics/32329",
                    "position": 3,
                },
                {"id": 4, "type": "SubHeader", "title": "分隔线", "position": 4},
            ],
        }
    ]
    c = _collector(modules=modules)
    await c._from_modules()

    titles = sorted(link.title for link in c.links.values())
    assert titles == ["Gradescope", "Pre-Class Videos"]
    src = next(iter(c.links.values())).sources[0]
    assert src.source_kind == SourceKind.MODULE
    assert src.container_name == "Course Info"


async def test_external_url_items_respect_the_link_switch():
    modules = [
        {
            "name": "M",
            "position": 1,
            "items": [
                {
                    "id": 1,
                    "type": "ExternalUrl",
                    "title": "Piazza",
                    "external_url": "https://piazza.com/x",
                    "position": 1,
                }
            ],
        }
    ]
    c = FileCollector(_FakeClient(modules=modules), 1, "C", include_links=False)
    await c._from_modules()
    assert c.links == {}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """确保这些用例不会真的去打 Canvas。"""
    return monkeypatch


# ---------- 覆盖面：先问这门课有什么，再决定扫什么 ----------


class _TabClient(_FakeClient):
    def __init__(self, tabs, tools=None, quizzes=None, **kwargs):
        super().__init__(**kwargs)
        self._tabs = tabs
        self._tools = tools or []
        self._quizzes = quizzes or []

    async def list_tabs(self, course_id: int):
        return self._tabs

    async def list_external_tools(self, course_id: int):
        return self._tools

    async def list_quizzes(self, course_id: int):
        return self._quizzes


async def test_uncovered_tabs_are_reported_not_silently_skipped():
    """**核心用例。** 做不到覆盖每一种板块可以接受，让用户以为看到的是全部不行。

    原来的做法是照着"我们知道的容器"扫，于是一门课只要形状不一样就静悄悄地漏 ——
    某门课整门资料都在大纲里，界面上是 0 个资源，而没有任何地方提示过。
    """
    tabs = [
        {"id": "home", "label": "Home"},
        {"id": "modules", "label": "Modules"},
        {"id": "grades", "label": "Grades"},  # 没有资料，不算漏
        {"id": "collaborations", "label": "Collaborations"},  # 没有资料
        {"id": "hidden_thing", "label": "Secret", "hidden": True},  # 学生看不见
        # 学校自己加的板块，我们不认识 —— 这才是要报出来的
        {"id": "context_module_sub_header_x", "label": "实习申请"},
    ]
    c = FileCollector(_TabClient(tabs), 1, "C")
    await c._discover_tabs()

    assert c.uncovered_tabs == ["实习申请"]
    # 认识的板块记成"扫过了"，不认识的把 Others 标成"还不支持"
    assert c.board_status["modules"] == boards.SCANNED
    assert c.board_status["others"] == boards.UNSUPPORTED
    assert "grades" not in c.board_status


async def test_discussions_are_a_switch_not_a_gap():
    """讨论区默认不收，但那是**用户的选择**，不是我们扫不了 ——
    所以它是"已关闭"，不该混进"还不支持"的清单里。"""
    tabs = [{"id": "discussions", "label": "Discussions"}]
    c = FileCollector(_TabClient(tabs), 1, "C")
    await c._discover_tabs()
    await c._from_discussions()

    assert c.uncovered_tabs == []
    assert c.board_status["discussions"] == boards.DISABLED


async def test_discussion_topics_are_scanned_when_switched_on():
    tabs = [{"id": "discussions", "label": "Discussions"}]
    topics = [
        {
            "id": 5,
            "title": "Week 1 Reading",
            "is_announcement": False,
            "html_url": "https://canvas.example.edu/courses/1/discussion_topics/5",
            "message": '读 <a href="/courses/1/files/42">这篇</a> 再来讨论',
        }
    ]

    class _C(_TabClient):
        async def list_discussions(self, course_id: int):
            return [t for t in topics if not t.get("is_announcement")]

    c = FileCollector(_C(tabs), 1, "C", include_discussions=True)
    await c._discover_tabs()
    await c._from_discussions()

    assert 42 in c.files
    assert c.files[42].sources[0].source_kind == SourceKind.DISCUSSION


async def test_nav_external_tools_become_links():
    """教授把 Piazza / MarkUs 直接做成导航栏上的一项，正文里没有任何 <a> 标签。
    目的地藏在 custom_fields.url 里 —— 工具的 url 字段只是跳转器地址。"""
    tabs = [{"id": "context_external_tool_27944", "label": "Piazza", "type": "external"}]
    tools = [
        {
            "id": "27944",
            "name": "Redirect Tool",
            "url": "https://www.edu-apps.org/redirect",
            "custom_fields": {"url": "https://piazza.com/example.edu/fall2026/cs301"},
        }
    ]
    c = FileCollector(_TabClient(tabs, tools=tools), 1, "C")
    await c._discover_tabs()
    await c._from_nav_tools()

    (link,) = c.links.values()
    assert link.title == "Piazza"
    assert link.url.startswith("https://piazza.com/")
    assert link.sources[0].source_kind == SourceKind.NAV


async def test_nav_tool_pointing_at_an_own_page_feeds_the_page_route():
    """导航栏上的工具指回本课的某个页面时，那是页面不是外链。

    这条尤其要紧：pages 端点被学校禁掉的课，导航栏是发现这些页面的唯一入口。
    """
    tabs = [{"id": "context_external_tool_1", "label": "CS Teaching Labs"}]
    tools = [
        {
            "id": "1",
            "custom_fields": {"url": "https://canvas.example.edu/courses/1/pages/cs-teaching-labs"},
        }
    ]
    c = FileCollector(_TabClient(tabs, tools=tools), 1, "C")
    await c._discover_tabs()
    await c._from_nav_tools()

    assert c.links == {}  # 不该被当成外部链接
    assert [p[0] for p in c._module_pages] == ["cs-teaching-labs"]


async def test_quiz_descriptions_are_scanned():
    """教授把考试范围、参考资料写在测验说明里 —— 实测有一门课 70 个测验，
    每一个都有说明正文，而我们一个都没扫过。"""
    tabs = [{"id": "quizzes", "label": "Quizzes"}]
    quizzes = [
        {
            "id": 9,
            "title": "Term Test 1",
            "html_url": "https://canvas.example.edu/courses/1/quizzes/9",
            "description": (
                '参考 <a href="/courses/1/files/77">往年题</a> 和 '
                '<a href="https://youtu.be/rev">复习录播</a>'
            ),
        }
    ]
    c = FileCollector(_TabClient(tabs, quizzes=quizzes), 1, "C")
    await c._discover_tabs()
    await c._from_quizzes()

    assert 77 in c.files
    assert c.files[77].sources[0].source_kind == SourceKind.QUIZ
    assert c.files[77].sources[0].container_name == "Term Test 1"
    (link,) = c.links.values()
    assert link.title == "复习录播"


async def test_quizzes_are_skipped_when_the_course_has_no_such_tab():
    """导航栏上没有测验就别白发一个请求。"""
    c = FileCollector(_TabClient([{"id": "home", "label": "Home"}]), 1, "C")
    await c._discover_tabs()
    await c._from_quizzes()
    assert c.stats["quiz"] == 0
