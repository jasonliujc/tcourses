import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { api, qs, toApiError } from "@/lib/api";
import type { FilterQuery } from "@/lib/fileFilter";
import { useI18n } from "@/lib/i18n";
import type {
  AppSettings,
  CanvasConnectionCheck,
  CanvasConnectionInput,
  CanvasConnectionResult,
  CanvasItem,
  Course,
  CourseBucket,
  CourseChoice,
  CourseFile,
  CourseSelectionResult,
  FileGroup,
  GlobalFilterOptions,
  GroupDimension,
  Medium,
  NotificationEvent,
  PlannerItem,
  PlannerItemUpdate,
  PlannerStatus,
  TaskInput,
  TaskList,
  ResourceSummary,
  SettingsUpdateResult,
  CourseSyncStatus,
  SortBy,
  SortOrder,
  SourceNode,
  SourceTreeKind,
  SyncStatus,
  UserGroup,
} from "@/types";

export const keys = {
  courses: ["courses"] as const,
  course: (id: number) => ["courses", id] as const,
  items: (courseId: number, kind?: string) => ["courses", courseId, "items", kind] as const,
  files: (courseId: number, groupId?: number) => ["courses", courseId, "files", groupId] as const,
  groups: (courseId: number, dim?: string) => ["courses", courseId, "groups", dim] as const,
  events: (unreadOnly: boolean) => ["events", unreadOnly] as const,
  unread: ["events", "unread-count"] as const,
  syncStatus: ["sync", "status"] as const,
};

// ---------- 课程 ----------

/** 边栏用的课程列表，已经按用户在「课程管理」里排的顺序返回。
 *  `includeHidden` 只有课程管理页会用 —— 它要连藏起来的课一起列出来才能改回去。 */
export const useCourses = (includeHidden = false) =>
  useQuery({
    queryKey: includeHidden ? (["courses", "all"] as const) : keys.courses,
    queryFn: () =>
      api.get<Course[]>(`/courses${qs({ include_hidden: includeHidden || undefined })}`),
  });

/** 保存边栏的课程显示与顺序。数组顺序就是边栏顺序 */
export function useSaveSidebarLayout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (items: { course_id: number; is_hidden: boolean }[]) =>
      api.put<{ visible: number; hidden: number }>("/courses/sidebar", { items }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.courses }),
  });
}

export const useCourse = (id: number) =>
  useQuery({ queryKey: keys.course(id), queryFn: () => api.get<Course>(`/courses/${id}`) });

export const useCourseItems = (courseId: number, kind?: string) =>
  useQuery({
    queryKey: keys.items(courseId, kind),
    queryFn: () => api.get<CanvasItem[]>(`/courses/${courseId}/items${qs({ kind })}`),
    enabled: !!courseId,
  });

// ---------- 文件 & 分组 ----------

export interface FileQuery {
  groupId?: number;
  sourceKind?: SourceTreeKind;
  sourceKey?: string;
  /** 用户分组；STARRED_GROUP_ID 表示星标 */
  userGroupId?: number;
  /** 按载体筛选。「按资源类型」的一级节点（文件 / 链接）和筛选面板最上面那一行都用它 */
  medium?: Medium;
  // ---- 筛选面板的几个轴。每一个都是多选，空数组 = 这一项不筛 ----
  board?: string[];
  container?: string[];
  kind?: string[];
  /** ISO 时刻，已经按用户所选时区把"某一天"换算成了起止 */
  date_from?: string;
  date_to?: string;
  q?: string;
  sortBy?: SortBy;
  order?: SortOrder;
}

export const useCourseFiles = (courseId: number, query: FileQuery = {}) =>
  useQuery({
    queryKey: ["courses", courseId, "files", query],
    // 兜底：同步完成会主动刷新（useRefreshOnSyncComplete），但万一那条路径断了，
    // 或者数据被别处改动，两分钟也会自己拉一次，页面不会一直停在旧数据上
    refetchInterval: 120_000,
    queryFn: () =>
      api.get<CourseFile[]>(
        `/courses/${courseId}/files${qs({
          group_id: query.groupId,
          source_kind: query.sourceKind,
          source_key: query.sourceKey,
          user_group_id: query.userGroupId,
          medium: query.medium,
          board: query.board,
          container: query.container,
          kind: query.kind,
          date_from: query.date_from,
          date_to: query.date_to,
          q: query.q,
          sort_by: query.sortBy,
          order: query.order,
        })}`,
      ),
    enabled: !!courseId,
  });

/** 来源导航树。空数组说明这门课一个文件都没扫到。
 *
 *  **跟着筛选走**：有条件生效时，树上只剩命中的节点，计数也只数命中的 ——
 *  否则边栏写着 28、点进去是"没有资源符合筛选条件"，那个数字就没法信了。 */
export const useSourceTree = (
  courseId: number,
  filter?: FilterQuery,
  /** 每个板块都展开容器。侧边栏不要（几百个平铺节点当不了导航），筛选面板要 */
  expandAll = false,
) =>
  useQuery({
    queryKey: ["courses", courseId, "sources", filter, expandAll],
    refetchInterval: 120_000,
    queryFn: () =>
      api.get<SourceNode[]>(
        `/courses/${courseId}/sources${qs({ ...filter, expand: expandAll ? "all" : undefined })}`,
      ),
    enabled: !!courseId,
  });

/** 自动分组。同样跟着筛选走，被筛空的分组不会返回 */
export const useGroups = (
  courseId: number,
  dimension?: GroupDimension,
  filter?: FilterQuery,
) =>
  useQuery({
    queryKey: [...keys.groups(courseId, dimension), filter],
    queryFn: () =>
      api.get<FileGroup[]>(`/courses/${courseId}/groups${qs({ dimension, ...filter })}`),
    enabled: !!courseId,
  });

// ---------- 用户自己的整理：星标与手动分组 ----------
//
// 和自动分组是两套接口。自动分组每轮同步推倒重建，这里的东西同步不会碰。

/** 用户自建分组。计数跟着筛选走，但**分组本身照常列出来**，哪怕被筛成 0 ——
 *  自己建的组因为一次临时筛选从边栏消失，只会让人以为组被删了 */
export const useUserGroups = (courseId: number, filter?: FilterQuery) =>
  useQuery({
    queryKey: ["courses", courseId, "user-groups", filter],
    queryFn: () =>
      api.get<UserGroup[]>(`/courses/${courseId}/user-groups${qs({ ...filter })}`),
    enabled: !!courseId,
  });

/** 星标 / 加组 / 移出都会改文件行上的标记，所以统一把这门课的查询全作废重拉 */
function useCourseMutation<TArgs, TResult>(
  courseId: number,
  fn: (args: TArgs) => Promise<TResult>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["courses", courseId] }),
  });
}

export const useCreateUserGroup = (courseId: number) =>
  useCourseMutation(courseId, (name: string) =>
    api.post<UserGroup>(`/courses/${courseId}/user-groups`, { name }),
  );

export const useRenameUserGroup = (courseId: number) =>
  useCourseMutation(courseId, ({ id, name }: { id: number; name: string }) =>
    api.patch<UserGroup>(`/user-groups/${id}`, { name }),
  );

export const useDeleteUserGroup = (courseId: number) =>
  useCourseMutation(courseId, (id: number) => api.del<void>(`/user-groups/${id}`));

export const useSetStar = (courseId: number) =>
  useCourseMutation(courseId, ({ fileIds, starred }: { fileIds: number[]; starred: boolean }) =>
    api.post<{ changed: number }>(`/courses/${courseId}/star`, {
      file_ids: fileIds,
      starred,
    }),
  );

export const useAddToUserGroup = (courseId: number) =>
  useCourseMutation(courseId, ({ groupId, fileIds }: { groupId: number; fileIds: number[] }) =>
    api.post<{ changed: number }>(`/user-groups/${groupId}/files`, { file_ids: fileIds }),
  );

// ---------- 全局资源（跨课程） ----------
//
// 和课程页共用后端那一套筛选与搜索，但**没有分组** —— 跨课程的分组没有意义
// （"Week 3"在六门课里各指一件事），所以这边只有星标。

/** 全局列表的查询。筛选那几个轴和课程页完全一样，多出四个只有全局才有的开关 */
export interface ResourceQuery extends Partial<FilterQuery> {
  q?: string;
  /** 只看星标 */
  starred?: boolean;
  /** 只看我还没看过的（按课程水位线判定） */
  unseen?: boolean;
  /** 只看这个时刻之后变动过的。今天 / 本周就是靠它 */
  since?: string;
  sortBy?: SortBy | "changed";
  order?: SortOrder;
  /** 只要前 N 条。首页那两个框用 */
  limit?: number;
}

export const useResources = (query: ResourceQuery = {}, enabled = true) =>
  useQuery({
    queryKey: ["resources", "list", query],
    refetchInterval: 120_000,
    queryFn: () =>
      api.get<CourseFile[]>(
        `/resources${qs({
          q: query.q,
          starred: query.starred || undefined,
          unseen: query.unseen || undefined,
          since: query.since,
          sort_by: query.sortBy,
          order: query.order,
          limit: query.limit,
          medium: query.medium,
          board: query.board,
          container: query.container,
          kind: query.kind,
          course: query.course,
          date_from: query.date_from,
          date_to: query.date_to,
        })}`,
      ),
    enabled,
  });

/** 首页两个框上的数字。时间窗口的边界由前端按用户所选时区算 */
export const useResourceSummary = (todaySince?: string, weekSince?: string) =>
  useQuery({
    queryKey: ["resources", "summary", todaySince, weekSince],
    refetchInterval: 120_000,
    queryFn: () =>
      api.get<ResourceSummary>(
        `/resources/summary${qs({ today_since: todaySince, week_since: weekSince })}`,
      ),
  });

/** 查找页左边那一列。**跟着筛选走**，理由和课程页的来源树一样：
 *  写着 28 点进去是空的，那个数字就没法信了 */
export const useResourceCourses = (filter?: Partial<FilterQuery>, starred = false) =>
  useQuery({
    queryKey: ["resources", "courses", filter, starred],
    queryFn: () =>
      api.get<CourseBucket[]>(
        `/resources/courses${qs({ ...filter, starred: starred || undefined })}`,
      ),
  });

/** 筛选面板的可选项。**不跟筛选走** —— 跟着走的话，取消一个条件之后
 *  那个选项就从面板上消失、再也选不回来 */
export const useResourceFilterOptions = () =>
  useQuery({
    queryKey: ["resources", "filter-options"],
    queryFn: () => api.get<GlobalFilterOptions>("/resources/filters"),
  });

/** 全局页的星标开关。课程页那个接口按课程收 id，这里没有"当前这门课" */
export function useSetResourceStar() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fileIds, starred }: { fileIds: number[]; starred: boolean }) =>
      api.post<{ changed: number }>("/resources/star", { file_ids: fileIds, starred }),
    // 星标改了，课程页行上的星也要跟着变，所以两边都作废
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["resources"] });
      qc.invalidateQueries({ queryKey: ["courses"] });
    },
  });
}

/** 把某几份资料标成"专门看过了"。下载 / 打开链接 / 看详情时调。
 *
 *  只作废 resources 那一串：课程页不显示"看过没有"，把它一起作废等于
 *  每下载一个文件就重拉整页列表。 */
export function useMarkSeen() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fileIds: number[]) =>
      api.post<{ changed: number }>("/resources/seen", { file_ids: fileIds }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["resources"] }),
  });
}

export function useMarkAllSeen() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ changed: number }>("/resources/seen-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["resources"] }),
  });
}

/** 打开某门课的资源页 = 这门课在这一刻之前的变动都算看过。
 *
 *  **这是"已看过"判定的主要入口**：用户常常是在课程页看文件的，那里看完的
 *  东西不能还堵在全局「更新」里。所以课程页一挂载就调它一次。 */
export function useMarkCourseSeen(courseId: number) {
  const qc = useQueryClient();
  const seen = useRef<number | null>(null);
  const mark = useMutation({
    mutationFn: (id: number) => api.post<{ changed: number }>(`/courses/${id}/seen`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["resources"] }),
  });
  useEffect(() => {
    // 每门课只标一次：课程页不会因为切课重新挂载，而 mutate 进依赖会把
    // 这个 effect 变成每次渲染都跑
    if (!courseId || seen.current === courseId) return;
    seen.current = courseId;
    mark.mutate(courseId);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mark 每次渲染都是新对象
  }, [courseId]);
}

// ---------- 日历与待办 ----------

export interface PlannerQuery {
  /** ISO 时刻。不给就是全部 */
  start?: string;
  end?: string;
  includeHidden?: boolean;
  /** 重复任务往后几次的预览。日历要，任务页不要 */
  projections?: boolean;
}

export const usePlannerItems = (query: PlannerQuery = {}) =>
  useQuery({
    queryKey: ["planner", "items", query],
    refetchInterval: 120_000,
    queryFn: () =>
      api.get<PlannerItem[]>(
        `/planner/items${qs({
          start: query.start,
          end: query.end,
          include_hidden: query.includeHidden || undefined,
          projections: query.projections === false ? false : undefined,
        })}`,
      ),
  });

/** 改一条。同步来的和自建的走两个接口，但调用方不用管 —— 按 source 分发 */
export function useUpdatePlannerItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      item,
      patch,
    }: {
      item: PlannerItem;
      patch: PlannerItemUpdate | TaskInput;
    }): Promise<{ item: PlannerItem; writeback: "off" | "ok" | "failed" }> => {
      if (item.source === "user") {
        const res = await api.patch<{ item: PlannerItem; spawned: PlannerItem | null }>(
          `/tasks/${item.id}`,
          patch,
        );
        return { item: res.item, writeback: "off" };
      }
      return api.patch(`/planner/items/${item.id}`, patch);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["planner"] }),
  });
}

export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: TaskInput & { title: string }) => api.post<PlannerItem>("/tasks", input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["planner"] });
      qc.invalidateQueries({ queryKey: ["task-lists"] });
    },
  });
}

export function useDeleteTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del<void>(`/tasks/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["planner"] });
      qc.invalidateQueries({ queryKey: ["task-lists"] });
    },
  });
}

export const useTaskLists = () =>
  useQuery({ queryKey: ["task-lists"], queryFn: () => api.get<TaskList[]>("/task-lists") });

function useListMutation<A>(fn: (args: A) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task-lists"] });
      qc.invalidateQueries({ queryKey: ["planner"] });
    },
  });
}

export const useCreateTaskList = () =>
  useListMutation((body: { name: string; color?: string | null }) =>
    api.post<TaskList>("/task-lists", body),
  );

export const useUpdateTaskList = () =>
  useListMutation(({ id, ...body }: { id: number; name?: string; color?: string | null }) =>
    api.patch<TaskList>(`/task-lists/${id}`, body),
  );

export const useDeleteTaskList = () =>
  useListMutation((id: number) => api.del<void>(`/task-lists/${id}`));

export const usePlannerStatus = () =>
  useQuery({
    queryKey: ["planner", "status"],
    queryFn: () => api.get<PlannerStatus>("/planner/status"),
    refetchInterval: 30_000,
  });

/** 改课程颜色。传空串 = 恢复 Canvas 里的颜色 */
export function useSetCourseColor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, color }: { id: number; color: string }) =>
      api.patch<Course>(`/courses/${id}`, { color }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["courses"] });
      qc.invalidateQueries({ queryKey: ["planner"] });
    },
  });
}

// ---------- 下载 ----------
//
// 单个和批量走同一套：fetch 拿到 blob，再用一个隐藏的 <a download> 存盘。
//
// 单个文件本来是 `<a href target="_blank">` 直接指到接口上的，问题是浏览器会
// 开一个新窗口跳过去 —— 按钮没法转圈（页面根本不知道下载开始了没有）、失败
// 也只能让用户对着 Canvas 的报错页发呆。走 fetch 之后成功失败都在我们手里。

/** Content-Disposition 里取文件名。后端用 RFC 5987 的 `filename*` 发中文名 */
function filenameFrom(res: Response, fallback: string): string {
  const header = res.headers.get("content-disposition") ?? "";
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded);
  const plain = header.match(/filename="?([^";]+)"?/i)?.[1];
  return plain ? decodeURIComponent(plain) : fallback;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  // 立刻 revoke 会让 Safari 来不及取数据，给一拍
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 下载走的是裸 fetch（要拿 blob），所以错误也得自己包成 ApiError ——
 *  这样界面才能按错误码翻成当前语言，而不是把后端那句话原样弹出来 */
async function failure(res: Response, fallbackCode: string): Promise<Error> {
  const body = await res.json().catch(() => null);
  const err = toApiError(body, res.status, fallbackCode);
  if (!err.code) err.code = fallbackCode;
  return err;
}

/** 下载单个文件。后端会先用库里那条链接，过期了自己换一条重试，
 *  两次都不行才返回错误 —— 到这里就是真下不下来了，弹提示。 */
export function useDownloadFile() {
  const markSeen = useMarkSeen();
  return useMutation({
    mutationFn: async (file: { id: number; display_name: string }) => {
      const res = await fetch(`/api/v1/files/${file.id}/download`);
      if (!res.ok) throw await failure(res, "download");
      saveBlob(await res.blob(), filenameFrom(res, file.display_name));
      // 下载过的东西就是"专门看过了"，不该再出现在全局「更新」里。
      // 失败不影响下载本身 —— 标记没成功远没有下载没成功重要
      markSeen.mutate([file.id]);
    },
  });
}

/** 批量下载：后端打成 zip 返回。
 *
 *  用 POST 而不是 `window.open(url)` —— 选中的 id 可能上百个，塞不进 query string。
 *  返回失败条数：包里有说明文件，但用户不会先去解压才知道少了东西。 */
export interface BundleOutcome {
  written: number;
  failed: number;
  /** 链接和失败分开报："3 个链接不能下载"和"3 个文件下载失败"
   *  是两件完全不同的事，混成一个数字用户没法判断要不要重试 */
  links: number;
}

/** 打包下载的那一次请求。**课程页和全局资源页共用** ——
 *  两个页面的同一个按钮必须发同样的请求、按同样的方式报结果。 */
async function postBundle(
  url: string,
  fileIds: number[],
  fallbackName: string,
): Promise<BundleOutcome> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_ids: fileIds }),
  });
  if (!res.ok) throw await failure(res, "bundle");
  saveBlob(await res.blob(), filenameFrom(res, fallbackName));
  return {
    written: Number(res.headers.get("x-bundle-written") ?? fileIds.length),
    failed: Number(res.headers.get("x-bundle-failed") ?? 0),
    links: Number(res.headers.get("x-bundle-links") ?? 0),
  };
}

export function useDownloadBundle(courseId: number) {
  // 包里那份说明也要跟着界面语言走 —— 后端不知道"当前用户选了哪种语言"
  const { lang, zone } = useI18n();
  return useMutation({
    mutationFn: (fileIds: number[]) =>
      postBundle(
        `/api/v1/courses/${courseId}/files/bundle${qs({ lang, tz: zone })}`,
        fileIds,
        `course-${courseId}.zip`,
      ),
  });
}

/** 跨课程打包。后端那边也是同一段实现，只多一件事：按课程分子目录 ——
 *  六门课各有一份 syllabus.pdf 是常态，平铺在一个包里认不出哪个是哪门课的。 */
export function useDownloadResourceBundle() {
  const { lang, zone } = useI18n();
  const markSeen = useMarkSeen();
  return useMutation({
    mutationFn: async (fileIds: number[]) => {
      const out = await postBundle(
        `/api/v1/resources/bundle${qs({ lang, tz: zone })}`,
        fileIds,
        "resources.zip",
      );
      // 下过的东西就是"专门看过了"，和单个下载同一条规则
      markSeen.mutate(fileIds);
      return out;
    },
  });
}

export const useRemoveFromUserGroup = (courseId: number) =>
  useCourseMutation(courseId, ({ groupId, fileIds }: { groupId: number; fileIds: number[] }) =>
    api.post<{ changed: number }>(`/user-groups/${groupId}/files/remove`, { file_ids: fileIds }),
  );

/** 手动刷新这门课的页面数据。
 *
 *  同时做两件事：重跑一次自动整理（保证分组和文件一致），再把这门课的所有
 *  查询作废重拉。用户点「刷新」时想要的是"把我看到的东西弄成最新的"，
 *  而不是只重算分组。 */
export function useRefreshCourse(courseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api.post<{ organized: number; skipped_locked: number }>(`/courses/${courseId}/organize`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["courses", courseId] }),
  });
}

// ---------- 更新提醒 ----------

export const useEvents = (unreadOnly = false) =>
  useQuery({
    queryKey: keys.events(unreadOnly),
    queryFn: () => api.get<NotificationEvent[]>(`/events${qs({ unread_only: unreadOnly })}`),
    // 第一阶段用轮询拿更新；后端换成 SSE 后这里改成订阅即可
    refetchInterval: 60_000,
  });

export const useUnreadCount = () =>
  useQuery({
    queryKey: keys.unread,
    queryFn: () => api.get<{ unread: number }>("/events/unread-count"),
    refetchInterval: 60_000,
  });

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post<NotificationEvent>(`/events/${id}/read`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}

export function useMarkAllRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ marked: number }>("/events/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["events"] }),
  });
}

// ---------- 同步 ----------

export const useSyncStatus = () =>
  useQuery({
    queryKey: keys.syncStatus,
    queryFn: () => api.get<SyncStatus>("/sync/status"),
    // 同步进行中要频繁刷新，否则进度条是静止的；空闲时降下来省资源
    refetchInterval: (query) => {
      const d = query.state.data;
      return d?.is_running || (d?.syncing_course_ids?.length ?? 0) > 0 ? 2_000 : 20_000;
    },
    // 默认只在标签页可见时轮询。同步状态必须一直跟，否则切走再回来
    // 界面还停在"同步中"，而后端早就跑完了
    refetchIntervalInBackground: true,
  });

export function useRunSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ status: string }>("/sync/run"),
    onSuccess: () => {
      // 后端在返回前就把 is_running 置上了，这里立刻拉状态，
      // 界面不会有"点了没反应"的空档
      qc.invalidateQueries({ queryKey: keys.syncStatus });
      qc.invalidateQueries({ queryKey: ["sync", "courses"] });
    },
  });
}

/** 同步是否正在进行（全量或单课）。
 *  返回值同时用于把轮询提速 —— 空闲时没必要每 2 秒打一次接口 */
export function useSyncActive(): boolean {
  const { data } = useSyncStatus();
  return (data?.is_running ?? false) || (data?.syncing_course_ids?.length ?? 0) > 0;
}

/** 同步从"进行中"变成"结束"的那一刻刷新数据。
 *
 *  没有这一下的话：轮询在同步结束时降回低频，而最后一次拉到的往往还是
 *  "同步中"的旧状态，界面就会一直停在那儿直到下一次低频轮询。 */
export function useRefreshOnSyncComplete(): boolean {
  const qc = useQueryClient();
  const active = useSyncActive();
  const wasActive = useRef(active);

  useEffect(() => {
    if (wasActive.current && !active) {
      qc.invalidateQueries();
    }
    wasActive.current = active;
  }, [active, qc]);

  return active;
}

/** 只同步一门课。不影响自动同步的排期 */
export function useSyncCourse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (courseId: number) =>
      api.post<{ status: string }>(`/sync/courses/${courseId}`),
    onSuccess: () => {
      // 后端已经把状态点亮了，立刻拉一次就能看到"同步中"
      qc.invalidateQueries({ queryKey: keys.syncStatus });
      qc.invalidateQueries({ queryKey: ["sync", "courses"] });
    },
  });
}

/** 按课程看同步结果：哪门成功、哪门失败、失败原因。
 *  `active` 表示当前有同步在跑 —— 那时候要快刷，否则跑完了状态还停在旧值 */
export const useCourseSyncStatus = (active = false) =>
  useQuery({
    queryKey: ["sync", "courses"],
    queryFn: () => api.get<CourseSyncStatus[]>("/sync/courses"),
    // 快刷的条件不只看外部传进来的 active，也看数据自身还有没有 syncing 的课 ——
    // 这样即使外部状态的时序有偏差，也会一直刷到后端确实报完成为止
    refetchInterval: (query) => {
      const busy = active || (query.state.data ?? []).some((c) => c.status === "syncing");
      return busy ? 2_000 : 30_000;
    },
    refetchIntervalInBackground: true,
  });

// ---------- 设置 ----------

export const useSettings = () =>
  useQuery({ queryKey: ["settings"], queryFn: () => api.get<AppSettings>("/settings") });

export function useUpdateSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      body: Partial<
        Pick<
          AppSettings,
          | "auto_download_files"
          | "sync_inline_images"
          | "sync_links"
          | "sync_announcement_links"
          | "sync_discussions"
          | "sync_interval_minutes"
          | "planner_writeback"
        >
      >,
    ) => api.patch<SettingsUpdateResult>("/settings", body),
    // 改开关会删数据并触发全量同步，课程列表和文件数都得重新拉
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** 保存前先问：变没变、连上的是谁、是不是换了账号 */
export const useCheckCanvasConnection = () =>
  useMutation({
    mutationFn: (body: CanvasConnectionInput) =>
      api.post<CanvasConnectionCheck>("/settings/canvas/check", body),
  });

export function useSaveCanvasConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CanvasConnectionInput) =>
      api.put<CanvasConnectionResult>("/settings/canvas", body),
    // 换了账号会清数据，而且马上开始同步：所有东西都得重新拉
    onSuccess: () => qc.invalidateQueries(),
  });
}

// ---------- 选择同步课程 ----------

/** Canvas 上能看到的所有课程 + 当前勾选状态。
 *  `enabled` 让它只在对话框打开时才拉 —— 没打开就没必要占一次请求。 */
export const useCourseChoices = (enabled: boolean) =>
  useQuery({
    queryKey: ["settings", "courses"],
    queryFn: () => api.get<CourseChoice[]>("/settings/courses"),
    enabled,
  });

export function useSaveCourseSelection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (course_ids: number[]) =>
      api.put<CourseSelectionResult>("/settings/courses", { course_ids }),
    // 取消勾选会当场清空那门课，边栏、课程同步状态、文件数全要重拉
    onSuccess: () => qc.invalidateQueries(),
  });
}
