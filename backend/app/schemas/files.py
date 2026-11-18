
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.types import UTCDatetime


class FileSourceOut(BaseModel):
    """文件在 Canvas 上出现的一个位置。前端用它给每个文件打"从哪儿来的"标注。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source_kind: str
    source_key: str
    container_name: str | None = None
    item_title: str | None = None
    label: str | None = None
    html_url: str | None = None
    source_date: UTCDatetime | None = None
    sort_hint: int = 0


class MatchHit(BaseModel):
    """搜索命中落在文件名以外的地方时，说清是哪儿。

    搜 "week 3" 出来一个叫 `l3.pdf` 的文件，用户第一反应是"搜错了" ——
    行上多一句「命中 模块：Week 3」就说得通了。名字上的命中不返回：
    名字本来就在行上摆着。
    """

    field: str  # folder / page / module / announcement / …
    text: str  # 原样的那段文本，例如 "course files/Lectures/Week 3"


class TagRef(BaseModel):
    """行上那个来源标签。id 和侧边栏节点、筛选面板的板块是同一套词 ——
    三处说的必须是同一件事，否则用户会看到标着 A 的资料出现在筛 B 的结果里。"""

    id: str
    label: str


class FileOut(BaseModel):
    """列表和详情共用。四要素（tag / source / date / kind）在任何视图都恒定显示，
    不用点进去才知道这文件是什么、从哪来。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    canvas_id: int
    course_id: int
    display_name: str
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    canvas_folder_path: str | None = None
    download_url: str | None = None
    local_path: str | None = None
    downloaded_at: UTCDatetime | None = None
    canvas_updated_at: UTCDatetime | None = None
    organize_locked: bool = False
    features: dict | None = None
    group_ids: list[int] = Field(default_factory=list)
    sources: list[FileSourceOut] = Field(default_factory=list)

    # ---- 恒定显示的四要素，兜底保证永不为空 ----
    #: **一个文件可以属于多个板块**，所以这里是一个列表，主来源那个排第一。
    #: 只给一个的话，显示（"它在 Pages 里"）和筛选（"它出现在哪些板块"）
    #: 说的就不是一件事了
    tags: list[TagRef] = Field(default_factory=list)
    source_label: str = "—"  # 完整路径，例如 "Week 3 · Slides from Class"
    source_date: UTCDatetime | None = None
    # 载体：这条记录是一个文件还是一个外部链接。界面上两者的类型标签长得不一样，
    # 用户得能一眼看出"点下去是下载还是跳出去"。外部链接接进来之前恒为 file。
    medium: str = "file"  # file / link
    # 链接指向哪儿。medium == "link" 时必有值，前端拿它开新标签页
    external_url: str | None = None
    kind: str = "other"  # pdf / docs / code / pictures / …（链接则是链接类型）
    kind_label: str = "其他"
    # 主来源在 Canvas 上的位置（可能为空）
    html_url: str | None = None
    # 文件本身在 Canvas 上的固定地址 —— 永远有值，详情页的兜底入口
    canvas_url: str | None = None

    # ---- 用户自己的整理结果，和四要素一样在任何视图都跟着文件走 ----
    is_starred: bool = False
    user_groups: list["UserGroupRef"] = Field(default_factory=list)
    # 文件在 Canvas 上已经没了，但用户整理过它，所以记录留着
    is_deleted_on_canvas: bool = False

    # ---- 全局资源页要的两样东西，只有全局那几个接口会填 ----
    #: 哪门课的。全局列表里一行必须自己说清这一点 —— 课程页里它是既定前提
    course_code: str | None = None
    course_name: str | None = None
    #: "这份资料最后一次变动"= max(第一次被收到, Canvas 上的更新时间)。
    #: 「最新」那一栏按它排、按它判定新不新，而**不是** source_date ——
    #: 后者是承载它的那个东西的日期（公告的发布时间可能是去年的）
    changed_at: UTCDatetime | None = None

    # ---- 搜索命中，只在带 q 的请求里有值 ----
    #: 相关度。前端按它排序时不用自己再算一遍分
    match_score: float | None = None
    #: 命中落在文件名以外的位置时的说明，最多两条 —— 一行上放不下更多
    match_hits: list[MatchHit] = Field(default_factory=list)


class UserGroupRef(BaseModel):
    """文件所属的用户分组，跟着文件一起返回 —— 列表和详情都要显示。"""

    id: int
    name: str


class CourseBucket(BaseModel):
    """全局资源页左边那一列：一门课一行，带命中筛选的条数。

    计数跟着筛选走，理由和课程页的来源树一样：边栏写着 28、点进去是空的，
    那个 28 就是假数字。被筛空的课整个不出现 —— 空筐不是导航。
    """

    course_id: int
    name: str
    course_code: str | None = None
    is_concluded: bool = False
    file_count: int = 0


class BoardOption(BaseModel):
    """全局筛选面板上的一个 Canvas 来源选项。label 全站统一英文（板块名是数据）。"""

    id: str
    label: str


class GlobalFilterOptions(BaseModel):
    """全局筛选面板的可选项。**来自没筛过的数据** ——
    跟着筛选走的话，取消一个条件之后它就从面板上消失、再也选不回来。"""

    courses: list[CourseBucket] = Field(default_factory=list)
    boards: list[BoardOption] = Field(default_factory=list)


class ResourceSummary(BaseModel):
    """全局首页那两个框上的数字。

    三个口径回答的是三个不同的问题，所以都得给：
    「更新」= 我还没看过的（按课程水位线判定），「今天 / 本周」= 时间窗口内变动过的
    （不管看过没有 —— 用户问的是"这周有啥新东西"，不是"有啥我没看"）。
    """

    unseen: int = 0
    today: int = 0
    week: int = 0
    starred: int = 0
    #: 在同步的课程里一共有多少份资料。页面标题下那一行用它，
    #: 和课程页标题下的「{n} 个资源」是同一个说法
    total: int = 0


class SeenRequest(BaseModel):
    """把这几份资料标成"专门看过了"。下载、打开链接、看详情都算。"""

    file_ids: list[int]


class SourceNode(BaseModel):
    """来源导航树的一个节点。

    Canvas Files 展开成文件夹，Modules 展开成各个模块，公告则平铺 ——
    所以这里既能表示"一个来源大类"，也能表示大类下的一个容器。
    """

    kind: str  # files / module / announcement / page / assignment
    key: str  # 该来源下的容器标识（文件夹路径、module 名、"" 表示整个大类）
    name: str
    file_count: int = 0
    children: list["SourceNode"] = Field(default_factory=list)


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    name: str
    kind: str
    rule_key: str | None = None
    description: str | None = None
    color: str | None = None
    sort_order: int = 0
    file_count: int = 0
    # rule_key 的前缀，前端用它做"按文件夹 / 按周次 / 按类别"的视图切换
    dimension: str | None = None


class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    sort_order: int = 0


class GroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    sort_order: int | None = None


class GroupMemberRequest(BaseModel):
    file_ids: list[int]


# ---------- 用户自己的整理结果 ----------


class UserGroupOut(BaseModel):
    """用户手建的分组。和自动分组是两套东西，所以单独一个 schema。"""

    id: int  # 星标是伪分组，id 为 STARRED_GROUP_ID
    course_id: int
    name: str
    sort_order: int = 0
    file_count: int = 0
    # 星标分组：界面上和用户自建的组分开摆，也不能改名/删除
    is_starred_group: bool = False


class UserGroupCreate(BaseModel):
    name: str


class UserGroupUpdate(BaseModel):
    name: str


class StarRequest(BaseModel):
    file_ids: list[int]
    starred: bool = True


class UserActionResult(BaseModel):
    """星标 / 加组 / 移出实际影响了几条。前端拿它决定要不要提示。"""

    changed: int = 0


class OrganizeResult(BaseModel):
    course_id: int
    organized: int
    skipped_locked: int
