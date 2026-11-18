from pydantic import BaseModel, Field

from app.schemas.types import UTCDatetime


class StepOut(BaseModel):
    id: int
    title: str
    done: bool = False


class PlannerItemOut(BaseModel):
    """一条日历 / 待办。同步数据和用户改动在这里合成一份，前端不用自己再算。"""

    id: int
    item_key: str
    kind: str
    plannable_type: str
    #: canvas = 同步来的；user = 用户自己建的（第二期）
    source: str = "canvas"

    course_id: int | None = None
    course_code: str | None = None
    course_name: str | None = None
    #: 课程颜色：用户改过的 > Canvas 里的 > 按课程 id 取的兜底色。
    #: 自建任务没挂课程时用清单色，再没有用中性色
    color: str | None = None
    #: 自建任务所在的清单
    list_id: int | None = None
    list_name: str | None = None

    #: 显示用的标题（用户改过就是改过的）；canvas_title 是原样
    title: str
    canvas_title: str
    #: 显示和排序用的那个时刻：用户自己的截止 > Canvas 截止 > 待办日期 > 开始时间
    date: UTCDatetime | None = None
    #: Canvas Planner 给的截止（对本人生效的那一份）
    due_at: UTCDatetime | None = None
    #: 用户自己设的截止
    my_due_at: UTCDatetime | None = None
    #: 作业 / 测验本身的截止（课程作业列表里那一份）。通常和 due_at 一样，
    #: 有 section 差异时不一样 —— 作为备注给用户对照
    item_due_at: UTCDatetime | None = None
    todo_date: UTCDatetime | None = None
    #: 事件的起止。用户改过就是改过的；canvas_* 是原样
    start_at: UTCDatetime | None = None
    end_at: UTCDatetime | None = None
    canvas_start_at: UTCDatetime | None = None
    canvas_end_at: UTCDatetime | None = None
    #: 用户改过时间（截止或起止）
    time_modified: bool = False
    all_day: bool = False
    all_day_date: str | None = None
    #: 地点。用户改过就是改过的；canvas_location 是原样
    location_name: str | None = None
    canvas_location: str | None = None
    location_address: str | None = None
    points_possible: float | None = None
    html_url: str | None = None
    description: str | None = None
    series_uuid: str | None = None

    submitted: bool = False
    graded: bool = False
    missing: bool = False
    late: bool = False
    excused: bool = False
    has_feedback: bool = False
    new_activity: bool = False
    canvas_marked_complete: bool = False

    #: 最终算不算完成
    completed: bool = False
    #: 用户明确设过的完成状态；None = 跟随 Canvas
    completed_override: bool | None = None
    hidden: bool = False
    note: str | None = None
    #: 重点
    important: bool = False
    #: Canvas 上已经没了，因为用户改过所以留着
    deleted_on_canvas: bool = False

    # ---- 只有自建任务有 ----
    steps: list[StepOut] = Field(default_factory=list)
    #: 重复规则 {"freq", "interval", "weekdays"}
    repeat: dict | None = None
    series_id: str | None = None
    #: 建任务时的时区，重复按它算
    tz: str | None = None
    completed_at: UTCDatetime | None = None
    #: 日历里重复任务往后的预览，不是真的一条（点开看的是当前那一次）
    virtual: bool = False


class PlannerItemUpdate(BaseModel):
    """改一条。**字段不传 = 不动，传 null = 清掉**（恢复成跟随 Canvas）。"""

    hidden: bool | None = None
    completed: bool | None = None
    title: str | None = None
    #: 截止类条目的时间
    due_at: UTCDatetime | None = None
    #: 事件的起止
    start_at: UTCDatetime | None = None
    end_at: UTCDatetime | None = None
    location: str | None = None
    note: str | None = None
    important: bool | None = None


class PlannerUpdateResult(BaseModel):
    item: PlannerItemOut
    #: off = 没开回写；ok = 已写回 Canvas；failed = 本地改了，Canvas 那边没写成
    writeback: str = "off"


class PlannerSyncState(BaseModel):
    count: int = 0
    status: str = "never"
    last_success_at: UTCDatetime | None = None
    last_synced_at: UTCDatetime | None = None
    error: str | None = None


class PlannerStatusOut(BaseModel):
    """设置页「同步」那一栏里，日历事件和待办各自的同步状态。"""

    items: PlannerSyncState = Field(default_factory=PlannerSyncState)
    events: PlannerSyncState = Field(default_factory=PlannerSyncState)


# ---------- 自建任务 ----------


class StepIn(BaseModel):
    id: int | None = None
    title: str
    done: bool = False


class TaskCreate(BaseModel):
    title: str
    note: str | None = None
    #: 二选一：全天给 due_date（YYYY-MM-DD），具体时刻给 due_at
    due_date: str | None = None
    due_at: UTCDatetime | None = None
    tz: str | None = None
    course_id: int | None = None
    list_id: int | None = None
    important: bool = False
    repeat: dict | None = None
    steps: list[StepIn] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    """字段不传 = 不动，传 null = 清掉。"""

    title: str | None = None
    note: str | None = None
    due_date: str | None = None
    due_at: UTCDatetime | None = None
    tz: str | None = None
    course_id: int | None = None
    list_id: int | None = None
    important: bool | None = None
    repeat: dict | None = None
    completed: bool | None = None
    #: 整份替换
    steps: list[StepIn] | None = None


class TaskUpdateResult(BaseModel):
    item: PlannerItemOut
    #: 勾掉重复任务时生成的下一次
    spawned: PlannerItemOut | None = None


class TaskListOut(BaseModel):
    id: int
    name: str
    color: str | None = None
    sort_order: int = 0
    task_count: int = 0


class TaskListIn(BaseModel):
    name: str | None = None
    color: str | None = None
