
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.types import UTCDatetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    course_id: int | None = None
    course_name: str | None = None
    canvas_item_id: int | None = None
    file_id: int | None = None
    title: str
    summary: str | None = None
    payload: dict | None = None
    is_read: bool = False
    created_at: UTCDatetime | None = None


class SyncStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    resource_key: str
    status: str
    last_synced_at: UTCDatetime | None = None
    last_success_at: UTCDatetime | None = None
    items_seen: int = 0
    items_changed: int = 0
    error_message: str | None = None


class SyncProgressOut(BaseModel):
    """同步进度。按课程数而不是百分比 —— 每门课耗时差异极大，百分比会骗人。"""

    done: int = 0
    total: int = 0
    # 同时在跑的那几门课（课程级并发），不是一门
    current_courses: list[str] = []


class BoardStatusOut(BaseModel):
    """一门课的一个板块（Canvas 导航栏上那一列）扫下来是什么结果。

    以前这里抛的是内部的 sync_state：公告 / 作业 / 板块覆盖 / Canvas Files 页面 /
    文件收集 / 外部链接混在一张表里，有的带条数有的不带，状态还分"正常 / 部分覆盖 /
    学校未开放" —— 用户根本看不出哪一行是要紧的。

    现在一行就是一个板块，四种状态说人话，只有 unsupported 需要用户注意。
    """

    id: str  # home / syllabus / announcements / files / ... / others
    label: str  # Home / Syllabus / Announcements …（统一英文，和侧边栏同一套词）
    # **只给状态，不给说法。** "已扫描 / 学校未开放"这类措辞在前端的词典里 ——
    # 后端拼好中文的话，英文界面上就会冒出中文，而这是同一个接口。
    status: str  # scanned / unavailable / disabled / unsupported
    count: int = 0
    #: status == unsupported 时，具体是哪几个板块没扫
    detail: str | None = None


class CourseSyncStatusOut(BaseModel):
    course_id: int
    name: str
    course_code: str | None = None
    is_concluded: bool = False
    # ok / failed / never / syncing
    status: str
    last_synced_at: UTCDatetime | None = None
    error: str | None = None
    file_count: int = 0
    boards: list[BoardStatusOut] = []


class SyncStatusOut(BaseModel):
    canvas_configured: bool
    canvas_base_url: str
    is_running: bool
    interval_minutes: int
    interval_choices: list[int] = []
    next_run_at: UTCDatetime | None = None
    # 上一次**全量**同步跑完的时刻。取落库的那一份，不是内存里的 last_report ——
    # 后者一重启就没了，而「上次同步是什么时候」正是重启之后最该还记得的事
    last_full_sync_at: UTCDatetime | None = None
    last_report: dict | None = None
    progress: SyncProgressOut = SyncProgressOut()
    # 正在单独同步的课程，前端在课程页显示转圈
    syncing_course_ids: list[int] = []


class ResultNote(BaseModel):
    """一条"这次改动做了什么"的说明。

    **只有 key 和数字，没有句子。** 句子由前端按当前界面语言拼出来 ——
    后端拼好中文塞进 `notes: list[str]`，英文界面就只能原样显示中文。
    """

    key: str
    params: dict = Field(default_factory=dict)


class SettingsOut(BaseModel):
    auto_download_files: bool
    sync_inline_images: bool
    sync_links: bool
    sync_announcement_links: bool
    sync_discussions: bool
    sync_interval_minutes: int
    interval_choices: list[int] = []
    planner_writeback: bool = False
    canvas_base_url: str
    canvas_configured: bool = False
    # 令牌末四位。令牌本身不出接口
    canvas_token_hint: str | None = None
    canvas_user_name: str | None = None
    demo_mode: bool = False
    # 关掉内联图片会删掉多少条记录
    inline_image_count: int = 0
    # 「选择同步课程」那一行的摘要：已选 / 总共
    selected_course_count: int = 0
    total_course_count: int = 0


class CanvasConnectionIn(BaseModel):
    base_url: str
    # 空 = 沿用当前令牌，只改地址
    token: str | None = None


class CanvasConnectionCheck(BaseModel):
    # 和当前的一模一样就什么都不做
    changed: bool
    user_name: str | None = None
    # 换成了另一个账号：上一个账号的数据会被清掉
    account_changed: bool = False


class CanvasConnectionResult(CanvasConnectionCheck):
    sync_started: bool = False
    removed_courses: int = 0


class SettingsUpdate(BaseModel):
    auto_download_files: bool | None = None
    sync_inline_images: bool | None = None
    sync_links: bool | None = None
    sync_announcement_links: bool | None = None
    sync_discussions: bool | None = None
    sync_interval_minutes: int | None = None
    planner_writeback: bool | None = None

    @field_validator("sync_interval_minutes")
    @classmethod
    def _check_interval(cls, v: int | None) -> int | None:
        # 在 schema 里挡住非法值，客户端拿到 422 而不是 500
        from app.services.runtime_settings import INTERVAL_CHOICES

        if v is not None and v not in INTERVAL_CHOICES:
            raise ValueError(f"同步间隔只能是 {INTERVAL_CHOICES} 之一（分钟）")
        return v


class SettingsUpdateResult(BaseModel):
    auto_download_files: bool
    sync_inline_images: bool
    sync_links: bool
    sync_announcement_links: bool
    sync_discussions: bool
    sync_interval_minutes: int = 15
    planner_writeback: bool = False
    next_run_at: UTCDatetime | None = None
    removed_courses: int = 0
    removed_files: int = 0
    # 这次改动是否触发了一次全量同步
    sync_started: bool = False
    notes: list[ResultNote] = []


class CourseChoiceOut(BaseModel):
    """「选择同步课程」里的一行。

    列的是 **Canvas 上能看到的所有课**，不只是在同步的那些 —— 不然用户没法
    把一门没在同步的课勾回来。`is_concluded` 只是个标签，和选不选无关。
    """

    course_id: int
    canvas_id: int
    name: str
    course_code: str | None = None
    term_name: str | None = None
    is_concluded: bool = False
    sync_enabled: bool = False
    # 已经同步下来多少文件 —— 取消勾选会删掉这些，得让用户看见
    file_count: int = 0


class CourseSelectionUpdate(BaseModel):
    """整份提交：列表里没有的课一律视为取消勾选。

    做成"全量覆盖"而不是"增量改动"，是因为界面就是一次性勾完再保存的；
    增量语义下"用户什么都没勾"和"用户没改"没法区分。
    """

    course_ids: list[int]


class CourseSelectionResult(BaseModel):
    enabled: int = 0
    disabled: int = 0
    # 这次取消勾选清掉了多少课、多少文件
    removed_courses: int = 0
    removed_files: int = 0
    sync_started: bool = False
    notes: list[ResultNote] = []


class ConnectionCheckOut(BaseModel):
    ok: bool
    message: str
    user_name: str | None = None
    course_count: int | None = None
