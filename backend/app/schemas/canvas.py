
from pydantic import BaseModel, ConfigDict

from app.schemas.types import UTCDatetime


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canvas_id: int
    name: str
    course_code: str | None = None
    term_name: str | None = None
    is_favorite: bool = False
    is_hidden: bool = False
    is_concluded: bool = False
    last_synced_at: UTCDatetime | None = None
    #: 显示用的颜色（用户改过的 > Canvas 里的 > 兜底色）
    color: str | None = None
    #: Canvas 里的那个，"恢复默认"用
    canvas_color: str | None = None
    #: 用户自己改过颜色
    custom_color: bool = False


class CourseStatsOut(CourseOut):
    file_count: int = 0
    group_count: int = 0
    unread_event_count: int = 0


class CourseUpdate(BaseModel):
    is_favorite: bool | None = None
    is_hidden: bool | None = None
    #: `#rrggbb`；传空串 = 恢复成 Canvas 里的颜色
    color: str | None = None


class SidebarCourseItem(BaseModel):
    course_id: int
    #: 藏起来的课照常同步，只是不出现在边栏
    is_hidden: bool = False


class SidebarLayoutUpdate(BaseModel):
    """边栏的课程布局：**数组顺序就是边栏顺序**。

    不另开一个 sort_order 字段让前端自己算，是因为界面上用户干的事就是拖动 ——
    拖完的那个数组本身就是答案，中间再转一道只会多一处能对不上的地方。
    列表里没提到的课保持原样（例如另一个标签页刚同步出来的新课）。
    """

    items: list[SidebarCourseItem]


class SidebarLayoutResult(BaseModel):
    visible: int = 0
    hidden: int = 0


class CanvasItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    canvas_id: str
    course_id: int | None = None
    title: str
    html_url: str | None = None
    author_name: str | None = None
    due_at: UTCDatetime | None = None
    posted_at: UTCDatetime | None = None
    canvas_updated_at: UTCDatetime | None = None
    is_read: bool = False
