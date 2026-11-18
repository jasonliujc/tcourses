import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import {
  type FileQuery,
  useAddToUserGroup,
  useCourse,
  useCourseFiles,
  useCreateUserGroup,
  useDeleteUserGroup,
  useGroups,
  useMarkCourseSeen,
  useRefreshOnSyncComplete,
  useRemoveFromUserGroup,
  useRenameUserGroup,
  useCourseSyncStatus,
  useRefreshCourse,
  useSetStar,
  useSourceTree,
  useSyncCourse,
  useDownloadBundle,
  useSyncStatus,
  useUserGroups,
} from "@/api/hooks";
import ConfirmDialog from "@/components/ConfirmDialog";
import FileDetail from "@/components/FileDetail";
import FilterPanel, { type FilterOptions } from "@/components/FilterPanel";
import FileList, {
  AddToGroupMenu,
  BAR_BTN,
  BAR_BTN_OUTLINE,
  GroupActionsMenu,
  StarIcon,
} from "@/components/FileList";
import Pagination from "@/components/Pagination";
import Spinner from "@/components/Spinner";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import {
  activeCount,
  toQuery as filterToQuery,
  type FileFilter,
  EMPTY_FILTER,
} from "@/lib/fileFilter";
import { loadFilter, saveFilter } from "@/lib/filterMemory";
import { loadCourseView, saveCourseView } from "@/lib/courseView";
import { useI18n } from "@/lib/i18n";
import { loadPageSize, savePageSize } from "@/lib/prefs";
import {
  STARRED_GROUP_ID,
  type CourseFile,
  type FileGroup,
  type GroupDimension,
  type Medium,
  type SortBy,
  type SortOrder,
  type SourceTreeKind,
  type UserGroup,
} from "@/types";

/** 一级：自动整理 vs 我的分组 */
type TopTab = "auto" | "manual";

/** 二级：自动整理下的整理方式。"default" 是按 Canvas 原始来源看，其余是规则分组 */
type AutoTab = "default" | GroupDimension;

const AUTO_TABS: { key: AutoTab; labelKey: "files.dim.default" | "files.dim.type" }[] = [
  { key: "default", labelKey: "files.dim.default" },
  { key: "type", labelKey: "files.dim.type" },
];


/** 全选、搜索框、批量操作栏、排序控件在同一行上，高度必须完全一致 ——
 *  它们各自的内边距和字号不一样，只靠 py-* 对不齐 */
const CONTROL_H = "h-9";

function FilterGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 5h18l-7 8v6l-4 2v-8z" strokeLinejoin="round" />
    </svg>
  );
}

/** 左侧选中的来源。null 表示"全部资源" */
interface SourceSelection {
  kind: SourceTreeKind;
  key: string;
}

export default function CourseFiles() {
  const courseId = Number(useParams().courseId);

  const { data: course } = useCourse(courseId);

  const setStar = useSetStar(courseId);
  const bundle = useDownloadBundle(courseId);
  const addToGroup = useAddToUserGroup(courseId);
  const removeFromGroup = useRemoveFromUserGroup(courseId);
  const createUserGroup = useCreateUserGroup(courseId);
  const renameUserGroup = useRenameUserGroup(courseId);
  const deleteUserGroup = useDeleteUserGroup(courseId);

  // 每门课分别记住上次停在哪个视图。课程页切课时不会重新挂载，
  // 不按课程隔离的话状态会被带到下一门课
  const [topTab, setTopTab] = useState<TopTab>(() => loadCourseView(courseId).topTab);
  const [autoTab, setAutoTab] = useState<AutoTab>(
    () => loadCourseView(courseId).autoTab as AutoTab,
  );
  const [source, setSource] = useState<SourceSelection | null>(null);
  const [activeGroup, setActiveGroup] = useState<number | undefined>();
  // 「按资源类型」是两级的：一级是载体（文件 / 链接），二级才是具体类型。
  // 选了一级就看那个载体的全部，选了二级就只看那一类 —— 两者互斥
  const [activeMedium, setActiveMedium] = useState<Medium | undefined>();
  const [sortBy, setSortBy] = useState<SortBy>("date");
  const [order, setOrder] = useState<SortOrder>("desc");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [page, setPage] = useState(1);
  // 每页几个是跨课程的个人习惯，不跟着课程走
  const [pageSize, setPageSize] = useState(loadPageSize);
  const [detail, setDetail] = useState<CourseFile | null>(null);
  // 「我的分组」里选中的组。null = 还没选（空状态）
  const [activeUserGroup, setActiveUserGroup] = useState<number | null>(null);
  // 新建 / 重命名分组的弹框。用页面内的框而不是 window.prompt ——
  // 浏览器原生框在不同系统上长得完全不一样，也没法做重名校验和提示。
  // `pending` 记着"这个组是给哪些文件建的"：从「加入」菜单里点新建时，
  // 用户的意思显然是"建完把这几个文件放进去"，而不是建一个空组。
  const [groupDialog, setGroupDialog] = useState<{
    id?: number;
    name: string;
    pending?: number[];
  } | null>(null);
  const [groupError, setGroupError] = useState<string | null>(null);
  const toast = useToast();
  const i18n = useI18n();
  const { t, formatDate } = i18n;

  // 筛选条件按视图分别记（见 lib/filterMemory）。这里的 key 就是"当前是哪个视图"
  const viewKey = topTab === "manual" ? "manual" : autoTab;
  const [filter, setFilter] = useState<FileFilter>(EMPTY_FILTER);
  const [filterOpen, setFilterOpen] = useState(false);
  // 换课或换视图时，把那个视图上次留下的条件调出来
  useEffect(() => {
    setFilter(loadFilter(courseId, viewKey));
  }, [courseId, viewKey]);

  // 接口认的那几个参数。**侧边栏的三份数据也带上它** —— 筛完之后边栏里的
  // 分组和计数必须跟着变，否则写着"Lectures 28"点进去却是空的，那个数字就没法信
  const filterCount = activeCount(filter);
  const picked = filterToQuery(filter, i18n.zone);
  const pickedKey = JSON.stringify(picked);
  // 一条条件都没有时传 undefined：这样侧边栏和筛选面板落在同一个缓存键上，
  // 不会为同一份数据打两次请求
  const sidebarFilter = filterCount > 0 ? picked : undefined;

  const { data: sourceTree } = useSourceTree(courseId, sidebarFilter);
  const { data: userGroups } = useUserGroups(courseId, sidebarFilter);
  // 星标是伪分组，单独摆；下拉菜单里只列用户自建的组
  const ownGroups = (userGroups ?? []).filter((g) => !g.is_starred_group);
  const starredGroup = (userGroups ?? []).find((g) => g.is_starred_group);

  const openCreateGroup = (pending?: number[]) => {
    setGroupDialog({ name: "", pending });
    setGroupError(null);
  };

  /** 清掉"当前在看哪一堆"的全部状态。
   *
   *  换课、换一级 tab、换二级 tab 都得走这里。每个视图各有一套选中状态
   *  （默认视图看 source，类型视图看 activeMedium/activeGroup，我的分组看
   *  activeUserGroup），少清一个的表现就是：从「按资源类型」切回「默认」，
   *  列表明明是全部资源，边栏的「全部资源」却不高亮 —— 因为 activeMedium
   *  还留着。所以只留这一个出口，不在各处各写一遍。 */
  const resetSelection = () => {
    setSource(null);
    setActiveGroup(undefined);
    setActiveMedium(undefined);
    setSelected(new Set());
    setPage(1);
  };

  useEffect(() => {
    const view = loadCourseView(courseId);
    setTopTab(view.topTab);
    setAutoTab(view.autoTab as AutoTab);
    // 上一门课的筛选、选中、页码不能带过来
    resetSelection();
    setActiveUserGroup(null);
    setSearch("");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- resetSelection 只调 setState，恒等
  }, [courseId]);

  // 进「我的分组」时默认落在星标上：那是唯一一个一定存在的分组
  useEffect(() => {
    if (topTab === "manual" && activeUserGroup === null && userGroups?.length) {
      setActiveUserGroup(STARRED_GROUP_ID);
    }
  }, [topTab, activeUserGroup, userGroups]);

  // 视图一变就记下来，下次回到这门课还在原处
  useEffect(() => {
    saveCourseView(courseId, { topTab, autoTab });
  }, [courseId, topTab, autoTab]);

  const inDefaultView = topTab === "auto" && autoTab === "default";

  const dimension: GroupDimension | undefined =
    topTab === "auto" && autoTab !== "default" ? autoTab : undefined;
  const { data: groups } = useGroups(courseId, dimension ?? "type", sidebarFilter);

  // **筛选面板用没筛过的那一份。** 面板要列出这门课所有能选的条件，包括当前
  // 正被筛掉的那些 —— 跟着筛选走的话，取消掉一个条件之后它就从面板上消失了，
  // 用户只能先清空、应用、再从头选一遍
  // expand=all：来源那一栏要列出**每个板块**下的容器，不只是侧边栏展开的那两个。
  // 否则资料全在课程大纲里的课，来源那一栏就是空的
  const { data: allSources } = useSourceTree(courseId, undefined, true);
  const { data: allGroups } = useGroups(courseId, "type");

  // 「按资源类型」的两级结构。链接的分组 rule_key 是 `type:link_*`，
  // 就靠这个前缀把两边分开 —— 组名本身两边都可能叫 Others
  const typeTree = useMemo(() => {
    if (!dimension) return [];
    const all = groups ?? [];
    const build = (medium: Medium, label: string, isLink: boolean) => {
      const own = all
        .filter((g) => (g.rule_key ?? "").startsWith("type:link_") === isLink)
        // 二级按首字母排。后端的 sort_order 表达的是"这个类型有多常见"，
        // 而侧栏是拿来**找**东西的 —— 找的时候字母序才有用。
        // 兜底分组（Other / Others）永远排最后：它不是一个类型，是"剩下的"，
        // 混在字母序中间会让人以为那也是一类东西
        .sort((a, b) => {
          const fallback = (g: FileGroup) => ((g.rule_key ?? "").endsWith("other") ? 1 : 0);
          return fallback(a) - fallback(b) || a.name.localeCompare(b.name);
        });
      return {
        medium,
        label,
        groups: own,
        count: own.reduce((n, g) => n + g.file_count, 0),
      };
    };
    // 一级用英文，和二级（PDF / Docs / Video）以及默认视图的
    // Canvas Files / Modules & Pages 是同一套词
    // 一个都没有的载体不占一行 —— 没有链接的课不该看到一个空的「Links」节点
    return [build("file", "Files", false), build("link", "Links", true)].filter(
      (node) => node.groups.length > 0,
    );
  }, [dimension, groups]);

  // 侧边栏在「按资源类型」的一级节点上也会限定载体。它和筛选面板最上面那一行
  // 说的是同一件事，而接口上 medium 只放得下一个值 —— 两边选了相反的载体时
  // 交集本来就是空的，下面用 mediumConflict 直接给出空列表
  const sideMedium =
    topTab === "auto" && autoTab !== "default" && !activeGroup ? activeMedium : undefined;
  const mediumConflict = !!picked.medium && !!sideMedium && picked.medium !== sideMedium;

  const query: FileQuery = useMemo(() => {
    // 筛选和"当前在看哪一堆"是两回事：后者决定从哪儿取，前者在取到的那堆上再切一刀，
    // 所以两者叠加，不是互相覆盖
    const base: FileQuery = {
      q: search,
      sortBy,
      order,
      medium: picked.medium,
      board: picked.board,
      container: picked.container,
      kind: picked.kind,
      date_from: picked.date_from,
      date_to: picked.date_to,
    };
    if (topTab === "manual") {
      // 没选中任何分组时不查 —— 「我的分组」的空状态是"去建一个组"，不是"全部资源"
      return { ...base, userGroupId: activeUserGroup ?? undefined };
    }
    if (autoTab !== "default") {
      return { ...base, groupId: activeGroup, medium: picked.medium ?? sideMedium };
    }
    // 默认视图：按 Canvas 来源筛选
    return source ? { ...base, sourceKind: source.kind, sourceKey: source.key || undefined } : base;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- picked 每次渲染都是新对象，用它的内容当依赖
  }, [
    topTab,
    autoTab,
    activeGroup,
    sideMedium,
    activeUserGroup,
    source,
    search,
    sortBy,
    order,
    pickedKey,
  ]);

  const { data: rawFiles, isLoading } = useCourseFiles(courseId, query);
  const files = mediumConflict ? [] : rawFiles;
  const refresh = useRefreshCourse(courseId);
  const syncCourse = useSyncCourse();
  const { data: syncStatus } = useSyncStatus();
  const { data: courseSync } = useCourseSyncStatus();
  const thisCourseSync = courseSync?.find((c) => c.course_id === courseId);
  // 同步跑完自动刷新这页的文件和分组
  useRefreshOnSyncComplete();
  // 打开这门课的资源页 = 这门课在这一刻之前的变动都算看过。
  // 用户常常是在这里看文件的，那些东西不能还堵在全局「更新」里
  useMarkCourseSeen(courseId);

  // 只同步这一门课，不影响自动同步的排期
  const isSyncing = (syncStatus?.syncing_course_ids ?? []).includes(courseId);
  // 全量同步期间不允许单课同步，两者会抢同一批数据
  const syncingAll = syncStatus?.is_running ?? false;

  // 换任何筛选条件都回到第一页，否则会停在一张空白页上
  useEffect(() => {
    setPage(1);
  }, [query]);

  // 开始搜索就按相关度排，清空搜索就回到按日期。**只在这两个转折点上动一次** ——
  // 搜索期间用户自己改过排序（比如就想按日期看）就不再被抢回去。
  // 相关度对没有搜索词的列表没有意义，所以也不能一直留着
  const hasSearch = search.trim().length > 0;
  const hadSearch = useRef(hasSearch);
  useEffect(() => {
    if (hasSearch === hadSearch.current) return;
    hadSearch.current = hasSearch;
    setSortBy((s) => {
      if (hasSearch) return s === "date" ? "relevance" : s;
      return s === "relevance" ? "date" : s;
    });
  }, [hasSearch]);

  const total = files?.length ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const visible = (files ?? []).slice((page - 1) * pageSize, page * pageSize);

  const switchTop = (tab: TopTab) => {
    setTopTab(tab);
    resetSelection();
  };


  const switchAuto = (tab: AutoTab) => {
    setAutoTab(tab);
    resetSelection();
  };

  const star = (fileIds: number[], starred: boolean) => {
    setStar.mutate({ fileIds, starred });
    if (fileIds.length > 1) setSelected(new Set());
  };

  const addTo = (groupId: number, fileIds: number[]) => {
    addToGroup.mutate({ groupId, fileIds });
    if (fileIds.length > 1) setSelected(new Set());
  };

  const submitGroupDialog = async () => {
    if (!groupDialog) return;
    const name = groupDialog.name.trim();
    if (!name) {
      setGroupError(t("files.groupNameEmpty"));
      return;
    }
    try {
      if (groupDialog.id === undefined) {
        const created = await createUserGroup.mutateAsync(name);
        // 建完就把待加入的文件放进去。**不切视图** —— 用户是在当前视图里
        // 整理文件，把他甩到「我的分组」去反而打断了手上的事
        if (groupDialog.pending?.length) {
          await addToGroup.mutateAsync({ groupId: created.id, fileIds: groupDialog.pending });
          setSelected(new Set());
        }
      } else {
        await renameUserGroup.mutateAsync({ id: groupDialog.id, name });
      }
      setGroupDialog(null);
      setGroupError(null);
    } catch (err) {
      setGroupError(apiMessage(err, i18n));
    }
  };

  // 会删东西的操作统一走确认弹框。`run` 是点确认之后真正干的事
  const [confirm, setConfirm] = useState<{
    title: string;
    body?: string;
    confirmLabel: string;
    run: () => Promise<void>;
  } | null>(null);
  const [confirming, setConfirming] = useState(false);

  const runConfirm = async () => {
    if (!confirm) return;
    setConfirming(true);
    try {
      await confirm.run();
      setConfirm(null);
    } finally {
      setConfirming(false);
    }
  };

  const askDeleteGroup = (group: UserGroup) =>
    setConfirm({
      title: t("confirm.deleteGroup", { name: group.name }),
      body: t("confirm.deleteGroupBody"),
      confirmLabel: t("confirm.deleteGroupAction"),
      run: async () => {
        await deleteUserGroup.mutateAsync(group.id);
        if (activeUserGroup === group.id) setActiveUserGroup(STARRED_GROUP_ID);
      },
    });

  const askRemoveFromGroup = (groupId: number, fileIds: number[], what: string) => {
    const group = ownGroups.find((g) => g.id === groupId);
    setConfirm({
      title: t("confirm.removeFrom", { what, group: group?.name ?? t("confirm.thatGroup") }),
      confirmLabel: t("confirm.removeAction"),
      run: async () => {
        await removeFromGroup.mutateAsync({ groupId, fileIds });
        setSelected(new Set());
      },
    });
  };

  const downloadSelected = async () => {
    if (bundle.isPending) return;
    try {
      const { written, failed, links } = await bundle.mutateAsync([...selected]);
      // 打包是长操作，成功之后保持选中反而让人不确定是不是真下完了
      setSelected(new Set());
      // 有东西没进包就得说清楚 —— 清单在包里的说明文件里，没有这条提示的话
      // 用户得先解压才发现少了。链接和失败分开说：前者是正常的（链接本来
      // 就没有本体可下），后者才需要考虑重试
      const notes = [
        links > 0 ? t("bundle.links", { n: links }) : "",
        failed > 0 ? t("bundle.failed", { n: failed }) : "",
      ].filter(Boolean);
      if (notes.length > 0) {
        const joined = notes.join(i18n.lang === "zh" ? "、" : ", ");
        toast(t("bundle.partial", { n: written, notes: joined }), "error");
      } else {
        toast(t("bundle.done", { n: written }));
      }
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  const askUnstar = (fileIds: number[]) =>
    setConfirm({
      title: t("confirm.unstar", { n: fileIds.length }),
      confirmLabel: t("confirm.unstarAction"),
      run: async () => {
        await setStar.mutateAsync({ fileIds, starred: false });
        setSelected(new Set());
      },
    });

  /** 面板上能选的东西。全部来自这个页面已经拉到的数据 —— 不为筛选另开接口，
   *  选项和侧边栏看到的也就永远一致 */
  const filterOptions: FilterOptions = useMemo(
    () => ({
      boards: (allSources ?? []).map((n) => ({ id: n.kind, name: n.name })),
      // 容器按名字去重再排字母序。**同一个容器可能挂在两个板块下** ——
      // Canvas 会把同一份东西同时算成 Assignment 和 Quiz（实测见过），
      // 不去重的话面板上是两个一模一样的 chip。筛选本来就是按名字匹配的，
      // 两个板块下的同名容器命中的也是同一批资源
      containers: [
        ...new Map(
          (allSources ?? [])
            .flatMap((n) => n.children)
            .map((c) => [c.key, { key: c.key, name: c.name }]),
        ).values(),
      ].sort((a, b) => a.name.localeCompare(b.name)),
      kinds: (allGroups ?? []).map((g) => {
        const key = (g.rule_key ?? "").replace(/^type:/, "");
        return {
          key,
          name: g.name,
          medium: (key.startsWith("link_") ? "link" : "file") as Medium,
        };
      }),
      // courses 不传：一门课是这个页面的既定前提。全局文件管理会传，那时面板上
      // 自动多出一节 —— 组件本身不用改
    }),
    [allSources, allGroups],
  );

  /** 「全部资源」上的那个数。有筛选时不能再显示课程总数 —— 那是没筛之前的数字。
   *  类型维度是一次**完整划分**（每个文件恰好属于一个类型分组），所以把这些分组
   *  加起来就是命中的总数，不用为此再开一个接口 */
  const totalCount =
    filterCount > 0
      ? (groups ?? []).reduce((n, g) => n + g.file_count, 0)
      : (course?.file_count ?? 0);

  const showingAll = source === null && activeGroup === undefined && !activeMedium;
  // 当前页可见的文件是否已全选 —— 全选按钮要能在"全选/取消全选"之间切
  const allVisibleSelected = visible.length > 0 && visible.every((f) => selected.has(f.id));

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {course?.name ?? t("course.untitled")}
          </h1>
          <p className="text-sm text-neutral-500">
            {t("course.resourceCount", { n: course?.file_count ?? 0 })}
            {course?.is_concluded && ` · ${t("course.concluded")}`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* 上次同步时间放在按钮左边 —— 决定要不要点同步的第一依据 */}
          {thisCourseSync?.last_synced_at && (
            <span className="whitespace-nowrap text-xs text-neutral-400">
              {thisCourseSync.status === "failed" ? (
                <span className="text-red-500">{t("files.lastSyncFailed")}</span>
              ) : (
                t("files.lastSync", { when: formatDate(thisCourseSync.last_synced_at) })
              )}
            </span>
          )}
          <button
            onClick={() => syncCourse.mutate(courseId)}
            disabled={isSyncing || syncingAll}
            title={syncingAll ? t("settings.syncingAllTip") : t("settings.courseSyncTip")}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-60 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {isSyncing && <Spinner />}
            {isSyncing ? t("settings.syncing") : t("settings.manualSync")}
          </button>
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            title={t("files.refreshTip")}
            className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {refresh.isPending ? t("files.refreshing") : t("files.refresh")}
          </button>
        </div>
      </header>

      {/* 一级：只有两个 */}
      <div className="flex gap-1 border-b border-neutral-200 dark:border-neutral-800">
        {(
          [
            ["auto", t("files.tab.auto")],
            ["manual", t("files.tab.manual")],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => switchTop(key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm transition ${
              topTab === key
                ? "border-neutral-900 font-medium text-neutral-900 dark:border-white dark:text-white"
                : "border-transparent text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
            }`}
          >
            {label}
            {key === "manual" && ownGroups.length > 0 && (
              <span className="ml-1.5 text-xs text-neutral-400">{ownGroups.length}</span>
            )}
          </button>
        ))}
      </div>

      {/* 二级：自动整理下的整理方式 */}
      {topTab === "auto" && (
        <div className="flex flex-wrap gap-2">
          {AUTO_TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => switchAuto(tab.key)}
              className={`rounded-full px-3 py-1 text-sm transition ${
                autoTab === tab.key
                  ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                  : "bg-neutral-100 text-neutral-600 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-300"
              }`}
            >
              {t(tab.labelKey)}
            </button>
          ))}
        </div>
      )}

      {/* minmax(0,1fr) 而不是 1fr：1fr 的最小宽度是 auto，内容会把列撑开，
          超出容器后被 overflow-hidden 裁掉 —— 表现就是按钮和排序控件凭空消失 */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="space-y-3">
          {/* 「全部资源」是自动整理下的入口，做成实心按钮和下面的列表分开。
              「我的分组」里没有这一项 —— 那边只有用户自己建的组 */}
          {topTab === "auto" && (
            <button
              onClick={resetSelection}
              className={`flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-sm font-medium transition ${
                showingAll
                  ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
                  : "border-neutral-300 text-neutral-700 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800"
              }`}
            >
              <span>{t("files.allResources")}</span>
              <span className={showingAll ? "text-xs opacity-80" : "text-xs text-neutral-400"}>
                {totalCount}
              </span>
            </button>
          )}

          {/* 顶部这条线是用来和上面的「全部资源」按钮分隔的；「我的分组」下没有
              那个按钮，再画一条就成了悬空的横线 */}
          <div
            className={
              topTab === "auto"
                ? "space-y-1 border-t border-neutral-200 pt-3 dark:border-neutral-800"
                : "space-y-1"
            }
          >
            {/* 这里不放标题：上面的两级 tab 已经说清楚当前是哪个视图，
                侧栏里每个节点也自带名字，再顶一行只是重复 */}

            {/* 默认视图：按 Canvas 页面来源分类，Files 展开文件夹、Modules 展开模块 */}
            {inDefaultView &&
              sourceTree?.map((node) => (
                <div key={node.kind}>
                  <button
                    onClick={() => setSource({ kind: node.kind, key: "" })}
                    className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${
                      source?.kind === node.kind && !source.key
                        ? "bg-neutral-200 dark:bg-neutral-800"
                        : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    }`}
                  >
                    <span className="truncate">{node.name}</span>
                    <span className="ml-2 shrink-0 text-xs text-neutral-400">
                      {node.file_count}
                    </span>
                  </button>
                  {node.children.map((child) => (
                    <button
                      key={child.key}
                      onClick={() => setSource({ kind: node.kind, key: child.key })}
                      className={`flex w-full items-center justify-between rounded-lg py-1.5 pl-6 pr-3 text-left text-sm ${
                        source?.kind === node.kind && source.key === child.key
                          ? "bg-neutral-200 dark:bg-neutral-800"
                          : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                      }`}
                    >
                      <span className="truncate text-neutral-600 dark:text-neutral-400">
                        {child.name}
                      </span>
                      <span className="ml-2 shrink-0 text-xs text-neutral-400">
                        {child.file_count}
                      </span>
                    </button>
                  ))}
                </div>
              ))}

            {inDefaultView && sourceTree?.length === 0 && (
              <p className="px-3 py-4 text-xs text-neutral-400">
                {t("files.emptyTree")}
              </p>
            )}

            {/* 自动整理的规则分组。做成两级：一级是载体（文件 / 链接），
                二级才是具体类型 —— 两边都有「Others」，平铺的话并排两个同名
                分组，用户只会以为出了 bug */}
            {topTab === "auto" &&
              !inDefaultView &&
              typeTree.map((node) => (
                <div key={node.medium}>
                  <button
                    onClick={() => {
                      setActiveMedium(node.medium);
                      setActiveGroup(undefined);
                    }}
                    className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${
                      activeMedium === node.medium && activeGroup === undefined
                        ? "bg-neutral-200 dark:bg-neutral-800"
                        : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    }`}
                  >
                    <span className="truncate">{node.label}</span>
                    <span className="ml-2 shrink-0 text-xs text-neutral-400">{node.count}</span>
                  </button>
                  {node.groups.map((g) => (
                    <button
                      key={g.id}
                      onClick={() => {
                        setActiveGroup(g.id);
                        setActiveMedium(node.medium);
                      }}
                      className={`flex w-full items-center justify-between rounded-lg py-1.5 pl-6 pr-3 text-left text-sm ${
                        activeGroup === g.id
                          ? "bg-neutral-200 dark:bg-neutral-800"
                          : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                      }`}
                    >
                      <span className="truncate text-neutral-600 dark:text-neutral-400">
                        {g.name}
                      </span>
                      <span className="ml-2 shrink-0 text-xs text-neutral-400">
                        {g.file_count}
                      </span>
                    </button>
                  ))}
                </div>
              ))}

            {/* 我的分组：星标单独摆在最上面，和用户自建的组用一条线分开 */}
            {topTab === "manual" && (
              <>
                {starredGroup && (
                  <button
                    onClick={() => setActiveUserGroup(STARRED_GROUP_ID)}
                    className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm ${
                      activeUserGroup === STARRED_GROUP_ID
                        ? "bg-neutral-200 dark:bg-neutral-800"
                        : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <StarIcon filled />
                      <span className="truncate">{t("files.starred")}</span>
                    </span>
                    <span className="ml-2 shrink-0 text-xs text-neutral-400">
                      {starredGroup.file_count}
                    </span>
                  </button>
                )}

                {/* 星标和自建分组之间恒定一条线，有没有自建分组都一样 ——
                    分隔线的意思是"上面那个不是你建的"，和下面有几个组无关 */}
                <div className="my-2 border-t border-neutral-200 dark:border-neutral-800" />

                {ownGroups.map((g) => (
                  <div
                    key={g.id}
                    className={`group flex items-center rounded-lg pr-3 ${
                      activeUserGroup === g.id
                        ? "bg-neutral-200 dark:bg-neutral-800"
                        : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    }`}
                  >
                    <button
                      onClick={() => setActiveUserGroup(g.id)}
                      className="min-w-0 flex-1 truncate px-3 py-2 text-left text-sm"
                    >
                      {g.name}
                    </button>
                    {/* 「操作」只在 hover 时出现，占位不挤走计数 */}
                    <span className="shrink-0 opacity-0 transition group-hover:opacity-100">
                      <GroupActionsMenu
                        onRename={() => {
                          setGroupDialog({ id: g.id, name: g.name });
                          setGroupError(null);
                        }}
                        onDelete={() => askDeleteGroup(g)}
                      />
                    </span>
                    {/* 计数固定在最右边，和星标那一行对齐 */}
                    <span className="ml-2 shrink-0 text-xs text-neutral-400">{g.file_count}</span>
                  </div>
                ))}

                <button
                  onClick={() => openCreateGroup()}
                  className="mt-1 w-full rounded-lg border border-dashed border-neutral-300 px-3 py-2 text-sm text-neutral-500 hover:border-neutral-400 dark:border-neutral-700"
                >
                  {t("files.newGroup")}
                </button>
              </>
            )}
          </div>
        </aside>

        <section className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {/* 全选在搜索框左边：勾选是对"下面这一页"的操作，入口该挨着列表 */}
            <button
              onClick={() =>
                setSelected(allVisibleSelected ? new Set() : new Set(visible.map((f) => f.id)))
              }
              disabled={visible.length === 0}
              className={`${CONTROL_H} shrink-0 rounded-lg border border-neutral-300 px-3 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800`}
            >
              {allVisibleSelected ? t("files.clearAll") : t("files.selectAll")}
            </button>
            {/* 筛选紧挨着搜索框：两者都是"在这一堆里再找找"，只是一个按名字、
                一个按条件。生效之后按钮变实心并带上条数 —— 列表变短了总得有个
                看得见的原因，不然用户会以为资源丢了 */}
            <button
              onClick={() => setFilterOpen(true)}
              className={`${CONTROL_H} flex shrink-0 items-center gap-1.5 rounded-lg border px-3 text-sm transition ${
                filterCount > 0
                  ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
                  : "border-neutral-300 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
              }`}
            >
              <FilterGlyph />
              {t("filter.button")}
              {filterCount > 0 && <span className="text-xs opacity-80">{filterCount}</span>}
            </button>
            {/* 搜索是容错的（大小写、标点、驼峰、拼错一个字母都能中），范围也不止
                文件名 —— 但这些从输入框上看不出来，所以高级语法写在 title 里，
                占位符里点一下"不止搜名字" */}
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("files.searchPlaceholder")}
              title={t("files.searchHint")}
              className={`${CONTROL_H} min-w-40 flex-1 rounded-lg border border-neutral-300 px-3 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900`}
            />
            {/* 选中之后，搜索框右边整块换成批量操作 —— 排序控件这时候没人用。
                整条和全选/搜索框一样高，里面的按钮统一用 BAR_BTN 的尺寸 */}
            {selected.size > 0 ? (
              <div
                className={`${CONTROL_H} flex shrink-0 items-center gap-2 rounded-lg bg-neutral-100 px-2.5 dark:bg-neutral-800`}
              >
                <span className="text-xs text-neutral-500">
                  {t("files.selectedCount", { n: selected.size })}
                </span>
                <button onClick={() => star([...selected], true)} className={BAR_BTN_OUTLINE}>
                  <StarIcon filled />
                  {t("files.star")}
                </button>
                <button onClick={() => askUnstar([...selected])} className={BAR_BTN_OUTLINE}>
                  {t("files.unstar")}
                </button>
                {/* 批量这里只做加入，不做开关：选中的文件各自在不在某个组里并不一样，
                    "再点一次"到底是加还是移根本说不清 */}
                <AddToGroupMenu
                  groups={ownGroups}
                  label={t("files.addToGroup")}
                  buttonClass={BAR_BTN_OUTLINE}
                  onPick={(gid) => addTo(gid, [...selected])}
                  onCreateGroup={() => openCreateGroup([...selected])}
                />
                {/* 在某个用户分组里看的时候，还得能把文件移出去 */}
                {topTab === "manual" && activeUserGroup !== null && (
                  <button
                    onClick={() =>
                      activeUserGroup === STARRED_GROUP_ID
                        ? askUnstar([...selected])
                        : askRemoveFromGroup(
                            activeUserGroup,
                            [...selected],
                            t("confirm.selectedFiles", { n: selected.size }),
                          )
                    }
                    className={`${BAR_BTN_OUTLINE} text-red-600`}
                  >
                    {t("files.removeFromThisGroup")}
                  </button>
                )}
                {/* 批量下载做成实心主按钮，和文件行里的「下载」一个分量。
                    宽度定死，"下载"变"下载中"时整条不会抖 */}
                <button
                  onClick={downloadSelected}
                  disabled={bundle.isPending}
                  className={`${BAR_BTN} w-[4.75rem] bg-neutral-900 text-white disabled:opacity-60 dark:bg-white dark:text-neutral-900`}
                >
                  {bundle.isPending && <Spinner className="size-3" />}
                  {bundle.isPending ? t("files.downloading") : t("files.download")}
                </button>
                <button
                  onClick={() => setSelected(new Set())}
                  className="text-xs text-neutral-400 hover:text-neutral-600"
                >
                  {t("common.cancel")}
                </button>
              </div>
            ) : (
              <>
                <select
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value as SortBy)}
                  className={`${CONTROL_H} rounded-lg border border-neutral-300 px-2 text-sm dark:border-neutral-700 dark:bg-neutral-900`}
                >
                  {/* 相关度只在搜索时给：没有搜索词的话它对每一行都是空的 */}
                  {hasSearch && <option value="relevance">{t("files.sortByRelevance")}</option>}
                  <option value="date">{t("files.sortByDate")}</option>
                  <option value="name">{t("files.sortByName")}</option>
                </select>
                <button
                  onClick={() => setOrder(order === "asc" ? "desc" : "asc")}
                  className={`${CONTROL_H} rounded-lg border border-neutral-300 px-2.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800`}
                >
                  {order === "asc" ? t("files.orderAsc") : t("files.orderDesc")}
                </button>
              </>
            )}
          </div>

          {/* 「我的分组」还一个组都没有时，给的是"去建一个"，不是一堆文件 */}
          {topTab === "manual" && ownGroups.length === 0 && starredGroup?.file_count === 0 ? (
            <div className="rounded-lg border border-dashed border-neutral-300 p-12 text-center dark:border-neutral-700">
              <p className="text-sm font-medium">{t("files.noGroupsTitle")}</p>
              <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-neutral-500">
                {t("files.noGroupsHint")}
              </p>
              <button
                onClick={() => openCreateGroup()}
                className="mt-4 rounded-lg bg-neutral-900 px-4 py-1.5 text-sm text-white dark:bg-white dark:text-neutral-900"
              >
                {t("files.newGroupTitle")}
              </button>
            </div>
          ) : (
            <FileList
              files={visible}
              isLoading={isLoading}
              selected={selected}
              onToggle={(id) =>
                setSelected((prev) => {
                  const next = new Set(prev);
                  if (next.has(id)) next.delete(id);
                  else next.add(id);
                  return next;
                })
              }
              onOpenDetail={setDetail}
              groups={ownGroups}
              onStar={star}
              onAddToGroup={addTo}
              onRemoveFromGroup={(gid, file) =>
                askRemoveFromGroup(gid, [file.id], t("confirm.thisFile", { name: file.display_name }))
              }
              onCreateGroup={openCreateGroup}
              emptyText={filterCount > 0 ? t("filter.emptyResult") : undefined}
            />
          )}

          <Pagination
            page={page}
            totalPages={totalPages}
            total={total}
            pageSize={pageSize}
            onChange={setPage}
            onPageSizeChange={(size) => {
              setPageSize(size);
              savePageSize(size);
              // 每页条数变了，原来的页码大概率越界，回第一页最省心
              setPage(1);
            }}
          />
        </section>
      </div>

      {filterOpen && (
        <FilterPanel
          value={filter}
          options={filterOptions}
          onCancel={() => setFilterOpen(false)}
          onApply={(next) => {
            setFilter(next);
            // 记在"这门课的这个视图"上，切走再回来还在
            saveFilter(courseId, viewKey, next);
            setFilterOpen(false);
          }}
        />
      )}

      {detail && <FileDetail file={detail} onClose={() => setDetail(null)} />}

      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          body={confirm.body}
          confirmLabel={confirm.confirmLabel}
          pending={confirming}
          onCancel={() => setConfirm(null)}
          onConfirm={runConfirm}
        />
      )}

      {groupDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-sm space-y-4 rounded-xl border border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
            <h3 className="text-lg font-semibold">
              {groupDialog.id === undefined
                ? t("files.newGroupTitle")
                : t("files.renameGroupTitle")}
            </h3>
            <input
              autoFocus
              value={groupDialog.name}
              onChange={(e) => setGroupDialog({ ...groupDialog, name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitGroupDialog();
                if (e.key === "Escape") setGroupDialog(null);
              }}
              placeholder={t("files.groupNamePlaceholder")}
              className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-800"
            />
            {groupError && <p className="text-xs text-red-600">{groupError}</p>}
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setGroupDialog(null)}
                className="rounded-lg border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
              >
                {t("common.cancel")}
              </button>
              <button
                onClick={submitGroupDialog}
                disabled={createUserGroup.isPending || renameUserGroup.isPending}
                className="rounded-lg bg-neutral-900 px-4 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-neutral-900"
              >
                {t("common.save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
