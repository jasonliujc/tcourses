export interface Course {
  id: number;
  canvas_id: number;
  name: string;
  course_code: string | null;
  term_name: string | null;
  is_favorite: boolean;
  is_hidden: boolean;
  is_concluded: boolean;
  last_synced_at: string | null;
  file_count: number;
  group_count: number;
  unread_event_count: number;
  /** 显示用的颜色：用户改过的 > Canvas 里的 > 兜底色 */
  color: string | null;
  canvas_color: string | null;
  custom_color: boolean;
}

export type ItemKind = "assignment" | "announcement" | "page" | "message" | "quiz";

export interface CanvasItem {
  id: number;
  kind: ItemKind;
  canvas_id: string;
  course_id: number | null;
  title: string;
  html_url: string | null;
  author_name: string | null;
  due_at: string | null;
  posted_at: string | null;
  canvas_updated_at: string | null;
  is_read: boolean;
}

/** 文件在 Canvas 上出现的位置。教授把资料放哪儿全凭习惯，所以一个文件可能有多个来源。 */
export type SourceKind =
  | "files"
  | "module"
  | "announcement"
  | "page"
  | "assignment"
  /** 课程大纲。挂在课程对象上，不是页面也不是作业 —— 有的课资料全在这儿 */
  | "syllabus"
  /** 测验说明。教授常把考试范围、参考资料写在这儿 */
  | "quiz"
  /** 课程导航栏上的外部工具（Piazza / MarkUs / 教授自建的板块） */
  | "nav";

/** 导航树和筛选用的**板块** id —— 和设置页那份板块列表是同一套词。
 *  文件行上的 tag 不受影响：板块是"在课程的哪一栏"，tag 是"这东西本身是什么"。 */
export type SourceTreeKind =
  | "home"
  | "syllabus"
  | "announcements"
  | "files"
  | "assignments"
  | "quizzes"
  | "modules"
  | "pages"
  | "discussions"
  | "others";

export interface FileSource {
  id: number;
  source_kind: SourceKind;
  source_key: string;
  container_name: string | null;
  item_title: string | null;
  label: string | null;
  html_url: string | null;
  source_date: string | null;
  sort_hint: number;
}

/** 来源导航树：Canvas Files 展开成文件夹，Modules 和 Pages 合并成一棵，公告等平铺 */
export interface SourceNode {
  kind: SourceTreeKind;
  key: string;
  name: string;
  file_count: number;
  children: SourceNode[];
}

/** 载体：能下载的文件，还是要跳出去的外部链接 */
export type Medium = "file" | "link";

export interface CourseFile {
  id: number;
  canvas_id: number;
  course_id: number;
  display_name: string;
  filename: string | null;
  content_type: string | null;
  size_bytes: number | null;
  canvas_folder_path: string | null;
  download_url: string | null;
  local_path: string | null;
  downloaded_at: string | null;
  canvas_updated_at: string | null;
  organize_locked: boolean;
  features: { dimensions?: Record<string, string>; ext?: string; containers?: string[] } | null;
  group_ids: number[];
  sources: FileSource[];

  /** 恒定显示的四要素，后端保证永不为空。
   *  **tags 是一组**：一个文件同时挂在好几个板块下是常态，只显示一个的话，
   *  它会出现在另一个板块的筛选结果里，而行上完全看不出为什么。
   *  id 和侧边栏节点、筛选面板的板块是同一套词。 */
  tags: TagRef[];
  source_label: string;
  source_date: string | null;
  /** 这条记录是文件还是外部链接 —— 点下去是下载还是跳出去，界面上要分得开。
   *  外部链接接进来之前恒为 "file"。 */
  medium: Medium;
  /** 链接指向哪儿。medium === "link" 时必有值 */
  external_url: string | null;
  kind: string;
  kind_label: string;
  /** 主来源在 Canvas 上的位置（可能为空） */
  html_url: string | null;
  /** 文件本身在 Canvas 上的固定地址 —— 永远有值 */
  canvas_url: string | null;

  /** 用户自己的整理结果。和四要素一样，在任何视图都跟着文件走 */
  is_starred: boolean;
  user_groups: UserGroupRef[];
  /** 文件在 Canvas 上已经没了，但用户整理过它，所以记录留着 */
  is_deleted_on_canvas: boolean;

  /** 哪门课的。**只有全局资源那几个接口会带** —— 课程页里它是既定前提，
   *  所以行上那个课程标签就靠它在不在来决定要不要显示 */
  course_code?: string | null;
  course_name?: string | null;
  /** "这份资料最后一次变动" = max(第一次被收到, Canvas 更新时间)。
   *  全局页的「最新」按它排、按它判定新不新 —— 而不是 source_date，
   *  后者是承载它的那个东西的日期（公告的发布时间可能是去年的） */
  changed_at?: string | null;

  /** 相关度。只有带搜索词的请求才有值 */
  match_score: number | null;
  /** 命中落在文件名以外的地方时的说明。搜 "week 3" 出来一个叫 `l3.pdf` 的文件，
   *  行上得说清是「模块：Week 3」命中的，否则看着像搜错了 */
  match_hits: MatchHit[];
}

/** 一次搜索命中落在哪儿。field 是 folder / page / module / announcement / … */
export interface MatchHit {
  field: string;
  text: string;
}

export interface TagRef {
  id: string;
  label: string;
}

/** 全局资源页左边那一列：一门课一行。计数跟着筛选走 */
export interface CourseBucket {
  course_id: number;
  name: string;
  course_code: string | null;
  is_concluded: boolean;
  file_count: number;
}

/** 全局筛选面板的可选项。**不带数量** —— 数量只能是"没筛之前有几个" */
export interface GlobalFilterOptions {
  courses: CourseBucket[];
  boards: { id: string; label: string }[];
}

/** 全局首页两个框上的数字。三个口径回答三个不同的问题：
 *  「更新」= 我还没看过的，「今天 / 本周」= 时间窗口内变动过的（不管看过没有） */
export interface ResourceSummary {
  unseen: number;
  today: number;
  week: number;
  starred: number;
  /** 在同步的课程里一共多少份资料。页面标题下那一行用它 */
  total: number;
}

export interface UserGroupRef {
  id: number;
  name: string;
}

/** 星标在界面上是一个分组，库里是 UserFile 上的一个布尔值。
 *  用这个伪 id 表示"星标分组"。 */
export const STARRED_GROUP_ID = -1;

export interface UserGroup {
  id: number;
  course_id: number;
  name: string;
  sort_order: number;
  file_count: number;
  /** 星标分组：单独摆，不能改名/删除 */
  is_starred_group: boolean;
}

/** relevance 只在搜索时有意义：没有搜索词的话所有资源的相关度都是空的 */
export type SortBy = "date" | "name" | "relevance";
export type SortOrder = "asc" | "desc";

/** rule_key 的前缀。每个维度都是一次完整划分：一个文件在一个维度里恰好属于一个分组。
 *  按周次 / 按用途已移除，后续由高级筛选承担。 */
export type GroupDimension = "type" | "manual";

export interface FileGroup {
  id: number;
  course_id: number;
  name: string;
  kind: "auto" | "manual";
  rule_key: string | null;
  description: string | null;
  color: string | null;
  sort_order: number;
  file_count: number;
  dimension: GroupDimension | null;
}

export interface NotificationEvent {
  id: number;
  kind: string;
  course_id: number | null;
  course_name: string | null;
  canvas_item_id: number | null;
  file_id: number | null;
  title: string;
  summary: string | null;
  payload: Record<string, unknown> | null;
  is_read: boolean;
  created_at: string | null;
}

/** 一门课的一个板块（Canvas 课程左边那一列）扫下来是什么结果。
 *  命名和文件管理侧边栏是同一套（Home / Syllabus / Files / …） */
export interface BoardStatus {
  id: string;
  label: string;
  /** scanned / unavailable / disabled / unsupported。只有 unsupported 要用户注意。
   *  **没有 status_label**：说法在前端的词典里，后端只给状态本身 */
  status: string;
  count: number;
  /** status === "unsupported" 时列出具体是哪几个板块 */
  detail: string | null;
}

export interface CourseSyncStatus {
  course_id: number;
  name: string;
  course_code: string | null;
  is_concluded: boolean;
  /** ok / failed / never / syncing */
  status: string;
  last_synced_at: string | null;
  error: string | null;
  file_count: number;
  boards: BoardStatus[];
}

export interface SyncProgress {
  done: number;
  total: number;
  /** 同时在跑的那几门课 —— 同步是课程级并发的，不是一门一门来 */
  current_courses: string[];
}

export interface SyncStatus {
  canvas_configured: boolean;
  canvas_base_url: string;
  is_running: boolean;
  interval_minutes: number;
  interval_choices: number[];
  next_run_at: string | null;
  /** 上一次**全量**同步跑完的时刻。落库的那一份，重启也还在 */
  last_full_sync_at: string | null;
  last_report: Record<string, unknown> | null;
  progress: SyncProgress;
  /** 正在单独同步的课程，课程页据此显示转圈 */
  syncing_course_ids: number[];
}

export interface AppSettings {
  auto_download_files: boolean;
  /** 收不收页面/公告正文里的内联图片（banner、logo）。改动会触发一次全量同步 */
  sync_inline_images: boolean;
  /** 收不收教授贴在正文里的外部链接 */
  sync_links: boolean;
  /** 公告里的链接单独一个开关：公告贡献了 74% 的链接，而且多是行政通知 */
  sync_announcement_links: boolean;
  /** 普通讨论区（不是公告）。只收主题正文，回帖是学生内容 */
  sync_discussions: boolean;
  sync_interval_minutes: number;
  /** 完成状态回写 Canvas */
  planner_writeback: boolean;
  interval_choices: number[];
  canvas_base_url: string;
  canvas_configured: boolean;
  /** 令牌末四位。令牌本身不出接口 */
  canvas_token_hint: string | null;
  canvas_user_name: string | null;
  /** 演示模式：连的是模拟的 Canvas，连接不能改 */
  demo_mode: boolean;
  inline_image_count: number;
  selected_course_count: number;
  total_course_count: number;
}

/** 后端报回来的一条结果说明。**只给 key 和数字，不给句子** ——
 *  句子由前端按当前语言拼，否则英文界面里会冒出中文 */
export interface ResultNote {
  key: string;
  params?: Record<string, string | number>;
}

/** 改 Canvas 连接。token 留空 = 沿用当前令牌 */
export interface CanvasConnectionInput {
  base_url: string;
  token?: string;
}

export interface CanvasConnectionCheck {
  changed: boolean;
  user_name: string | null;
  /** 换成了另一个账号：上一个账号的数据会被清掉 */
  account_changed: boolean;
}

export interface CanvasConnectionResult extends CanvasConnectionCheck {
  sync_started: boolean;
  removed_courses: number;
}

export interface SettingsUpdateResult {
  auto_download_files: boolean;
  sync_inline_images: boolean;
  sync_links: boolean;
  sync_announcement_links: boolean;
  sync_discussions: boolean;
  sync_interval_minutes: number;
  next_run_at: string | null;
  removed_courses: number;
  removed_files: number;
  sync_started: boolean;
  notes: ResultNote[];
}

/** 「选择同步课程」里的一行：Canvas 上能看到的所有课，不只是在同步的那些。
 *  is_concluded 只是标签 —— 结课不影响同不同步。 */
export interface CourseChoice {
  course_id: number;
  canvas_id: number;
  name: string;
  course_code: string | null;
  term_name: string | null;
  is_concluded: boolean;
  sync_enabled: boolean;
  file_count: number;
}

export interface CourseSelectionResult {
  enabled: number;
  disabled: number;
  removed_courses: number;
  removed_files: number;
  sync_started: boolean;
  notes: ResultNote[];
}

// ---------- 日历与待办 ----------

export type PlannerKind =
  | "assignment"
  | "quiz"
  | "discussion"
  | "event"
  | "announcement"
  | "page"
  | "note"
  | "peer_review"
  | "other"
  /** 用户自己建的任务 */
  | "task";

/** 一条日历 / 待办。同步数据和用户改动已经在后端合成一份 */
export interface PlannerItem {
  id: number;
  item_key: string;
  kind: PlannerKind;
  plannable_type: string;
  /** canvas = 同步来的；user = 用户自己建的 */
  source: "canvas" | "user";
  course_id: number | null;
  course_code: string | null;
  course_name: string | null;
  color: string | null;
  title: string;
  canvas_title: string;
  /** 显示和排序用的时刻：自己的截止 > Canvas 截止 > 待办日期 > 开始时间 */
  date: string | null;
  due_at: string | null;
  my_due_at: string | null;
  /** 作业 / 测验本身的截止，作为备注对照 */
  item_due_at: string | null;
  todo_date: string | null;
  /** 事件的起止：用户改过就是改过的，canvas_* 是原样 */
  start_at: string | null;
  end_at: string | null;
  canvas_start_at: string | null;
  canvas_end_at: string | null;
  /** 用户改过时间（截止或起止） */
  time_modified: boolean;
  all_day: boolean;
  all_day_date: string | null;
  /** 地点：用户改过就是改过的，canvas_location 是原样 */
  location_name: string | null;
  canvas_location: string | null;
  location_address: string | null;
  points_possible: number | null;
  html_url: string | null;
  description: string | null;
  series_uuid: string | null;
  submitted: boolean;
  graded: boolean;
  missing: boolean;
  late: boolean;
  excused: boolean;
  has_feedback: boolean;
  new_activity: boolean;
  canvas_marked_complete: boolean;
  completed: boolean;
  /** 用户明确设过的完成状态；null = 跟随 Canvas */
  completed_override: boolean | null;
  hidden: boolean;
  note: string | null;
  important: boolean;
  /** Canvas 上已经没了，因为用户改过所以留着 */
  deleted_on_canvas: boolean;

  // ---- 只有自建任务有 ----
  list_id: number | null;
  list_name: string | null;
  steps: TaskStep[];
  repeat: Repeat | null;
  series_id: string | null;
  tz: string | null;
  completed_at: string | null;
  /** 日历里重复任务往后的预览，不是真的一条 */
  virtual: boolean;
}

export interface TaskStep {
  id?: number;
  title: string;
  done: boolean;
}

export type RepeatFreq = "daily" | "weekly" | "monthly" | "yearly";

export interface Repeat {
  freq: RepeatFreq;
  interval?: number;
  /** 周一 = 0 … 周日 = 6 */
  weekdays?: number[];
}

/** 新建 / 修改自建任务。字段不传 = 不动，传 null = 清掉 */
export interface TaskInput {
  title?: string;
  note?: string | null;
  /** 二选一：全天给 due_date，具体时刻给 due_at */
  due_date?: string | null;
  due_at?: string | null;
  tz?: string | null;
  course_id?: number | null;
  list_id?: number | null;
  important?: boolean;
  repeat?: Repeat | null;
  completed?: boolean;
  steps?: TaskStep[];
}

export interface TaskList {
  id: number;
  name: string;
  color: string | null;
  sort_order: number;
  task_count: number;
}

/** 改一条。字段不传 = 不动，传 null = 清掉 */
export interface PlannerItemUpdate {
  hidden?: boolean;
  completed?: boolean | null;
  title?: string | null;
  due_at?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  location?: string | null;
  note?: string | null;
  important?: boolean;
}

export interface PlannerSyncState {
  count: number;
  status: string;
  last_success_at: string | null;
  last_synced_at: string | null;
  error: string | null;
}

export interface PlannerStatus {
  items: PlannerSyncState;
  events: PlannerSyncState;
}
