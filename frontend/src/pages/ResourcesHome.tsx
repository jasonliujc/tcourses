import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  useDownloadFile,
  useMarkAllSeen,
  useMarkSeen,
  useResourceSummary,
  useResources,
} from "@/api/hooks";
import { CourseBadge, TypeBadge } from "@/components/FileList";
import Pagination from "@/components/Pagination";
import Spinner from "@/components/Spinner";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import { startOfToday, startOfWeek } from "@/lib/fileFilter";
import { useI18n } from "@/lib/i18n";
import { loadPageSize, savePageSize } from "@/lib/prefs";
import type { CourseFile } from "@/types";

/** 框里默认列几行。**十条是一眼能扫完的量** —— 再多就不是"最新"而是一份列表了，
 *  剩下的走「展开」。 */
const ROWS = 10;

type Latest = "unseen" | "today" | "week";

/** 全局资源页的主页：左边**最新**、右边**我的星标**。
 *
 *  这两个框回答的是打开这个页面的两个理由 —— "有什么新东西"和"我收的那几份在哪"。
 *  左右并排而不是上下叠：两件事分量相同，上下排的话下面那个要滚才看得到，
 *  等于告诉用户它次要。
 *
 *  行上只给一个主操作（下载 / 打开）：主页是用来看和取的，整理放到二级页面去做。
 */
export default function ResourcesHome() {
  const { t, zone } = useI18n();
  const [tab, setTab] = useState<Latest>("unseen");
  const [expanded, setExpanded] = useState(false);
  // 「今天 / 本周」的边界按用户所选时区算。后端不猜时区 —— 猜一个只会在跨日的
  // 那几个小时里给出和列表对不上的数字
  const windows = useMemo(
    () => ({ today: startOfToday(zone), week: startOfWeek(zone) }),
    [zone],
  );
  const { data: summary } = useResourceSummary(windows.today, windows.week);
  const markAllSeen = useMarkAllSeen();

  const scope = useMemo(
    () =>
      tab === "unseen"
        ? { unseen: true }
        : { since: tab === "today" ? windows.today : windows.week },
    [tab, windows],
  );
  const { data: latest, isLoading } = useResources({ ...scope, sortBy: "changed", limit: ROWS });
  const { data: starred, isLoading: starredLoading } = useResources({
    starred: true,
    sortBy: "changed",
    limit: ROWS,
  });

  const counts: Record<Latest, number> = {
    unseen: summary?.unseen ?? 0,
    today: summary?.today ?? 0,
    week: summary?.week ?? 0,
  };
  const hidden = counts[tab] - (latest?.length ?? 0);

  return (
    <>
      {/* 一列放不下两个框的窄屏上自动叠成上下 */}
      <div className="grid gap-5 lg:grid-cols-2">
        <Card
          title={t("resources.latest")}
          action={
            // 只有「更新」那一栏才有"标记已看"可言：今天 / 本周是时间窗口，
            // 和看过没看过无关
            tab === "unseen" && counts.unseen > 0 ? (
              <button
                onClick={() => markAllSeen.mutate()}
                disabled={markAllSeen.isPending}
                className="flex items-center gap-1.5 text-xs text-neutral-500 hover:text-neutral-900 disabled:opacity-50 dark:hover:text-neutral-100"
              >
                {markAllSeen.isPending && <Spinner className="size-3" />}
                {t("resources.markAllSeen")}
              </button>
            ) : null
          }
        >
          <div className="mb-3 flex flex-wrap gap-1.5">
            {(["unseen", "today", "week"] as Latest[]).map((key) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`rounded-full border px-3 py-1 text-xs transition ${
                  tab === key
                    ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
                    : "border-neutral-300 text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                }`}
              >
                {t(LATEST_LABELS[key])}
                <span className="ml-1.5 tabular-nums opacity-70">{counts[key]}</span>
              </button>
            ))}
          </div>
          <RowList
            files={latest}
            isLoading={isLoading}
            showChanged
            empty={t("resources.emptyUpdates")}
          />
          {hidden > 0 && (
            <button
              onClick={() => setExpanded(true)}
              className="mt-3 w-full rounded-lg border border-neutral-300 py-1.5 text-xs text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            >
              {t("resources.expand", { n: hidden })}
            </button>
          )}
        </Card>

        <Card
          title={t("resources.starred")}
          count={summary?.starred}
          action={
            // 「全部」做成看得见的按钮：它是这个框唯一的出口，做成一行小灰字
            // 根本不像能点
            <Link
              to="/resources/starred"
              className="rounded-lg border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-neutral-100 dark:border-neutral-600 dark:text-neutral-200 dark:hover:bg-neutral-800"
            >
              {t("resources.viewAll")}
            </Link>
          }
        >
          <RowList
            files={starred}
            isLoading={starredLoading}
            empty={t("resources.emptyStarred")}
          />
        </Card>
      </div>

      {expanded && (
        <ExpandedLatest
          scope={scope}
          title={`${t("resources.latest")} · ${t(LATEST_LABELS[tab])}`}
          onClose={() => setExpanded(false)}
        />
      )}
    </>
  );
}

const LATEST_LABELS: Record<Latest, "resources.latestUnseen" | "resources.latestToday" | "resources.latestWeek"> = {
  unseen: "resources.latestUnseen",
  today: "resources.latestToday",
  week: "resources.latestWeek",
};

/** 「展开」弹窗：当前这一栏的**全部**内容，只有分页。
 *
 *  刻意不给搜索、筛选、排序 —— 那些在「查找资源」里，那才是为翻找设计的页面。
 *  这里回答的是"刚才那十条之外还有什么"，多给一排控件只会让人以为这是另一个
 *  功能入口。 */
function ExpandedLatest({
  scope,
  title,
  onClose,
}: {
  scope: { unseen?: boolean; since?: string };
  title: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(loadPageSize);
  const { data: files, isLoading } = useResources({ ...scope, sortBy: "changed" });

  const total = files?.length ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const visible = (files ?? []).slice((page - 1) * pageSize, page * pageSize);

  // 内容变少（比如标记了已看）时别停在一张空白页上
  useEffect(() => {
    setPage((p) => Math.min(p, Math.max(1, Math.ceil(total / pageSize))));
  }, [total, pageSize]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-neutral-200 bg-white shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-baseline justify-between gap-3 border-b border-neutral-200 p-5 dark:border-neutral-800">
          <h3 className="text-lg font-semibold">
            {title}
            <span className="ml-2 text-xs font-normal text-neutral-400 tabular-nums">{total}</span>
          </h3>
          <button
            onClick={onClose}
            className="rounded-lg border border-neutral-300 px-3 py-1 text-xs hover:bg-neutral-100 dark:border-neutral-600 dark:hover:bg-neutral-800"
          >
            {t("common.close")}
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5">
          <RowList files={visible} isLoading={isLoading} showChanged empty={t("resources.emptyUpdates")} />
        </div>
        {total > 0 && (
          <div className="border-t border-neutral-200 p-5 dark:border-neutral-800">
            <Pagination
              page={page}
              totalPages={totalPages}
              total={total}
              pageSize={pageSize}
              onChange={setPage}
              onPageSizeChange={(size) => {
                setPageSize(size);
                savePageSize(size);
                setPage(1);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function Card({
  title,
  count,
  action,
  children,
}: {
  title: string;
  count?: number;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col rounded-xl border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900">
      <header className="mb-3 flex min-h-7 items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">
          {title}
          {count !== undefined && (
            <span className="ml-2 text-xs font-normal text-neutral-400 tabular-nums">{count}</span>
          )}
        </h2>
        {action}
      </header>
      {children}
    </section>
  );
}

/** 主页上的紧凑行。和列表页那一行是**两种东西**：
 *  这里只回答"是什么、哪门课的、什么时候、怎么拿"，四项一行放得下；
 *  整理（星标、分组）和全部来源留给二级页面。 */
function RowList({
  files,
  isLoading,
  empty,
  showChanged,
}: {
  files: CourseFile[] | undefined;
  isLoading?: boolean;
  empty: string;
  /** 「最新」那个框显示的是**变动时间**，星标框显示资料自己的日期 ——
   *  前者问"什么时候变的"，后者问"这是哪一天的东西" */
  showChanged?: boolean;
}) {
  const { t, dateParts } = useI18n();
  if (isLoading) {
    return <p className="py-6 text-center text-sm text-neutral-400">{t("common.loading")}</p>;
  }
  if (!files || files.length === 0) {
    return <p className="py-6 text-center text-sm text-neutral-400">{empty}</p>;
  }
  return (
    <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
      {files.map((f) => {
        const when = dateParts(showChanged ? f.changed_at : (f.source_date ?? f.changed_at));
        return (
          <li key={f.id} className="flex items-center gap-3 py-2">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{f.display_name}</p>
              <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-neutral-500">
                {f.course_code && <CourseBadge code={f.course_code} name={f.course_name} />}
                <span className="shrink-0 tabular-nums">
                  <span className="text-neutral-600 dark:text-neutral-300">{when.date}</span>
                  {when.time && <span className="ml-1 text-neutral-400">{when.time}</span>}
                </span>
                <TypeBadge medium={f.medium} label={f.kind_label} />
              </div>
            </div>
            <CompactAction file={f} />
          </li>
        );
      })}
    </ul>
  );
}

/** 主页行尾的取用按钮。比列表页那个小一号 —— 这里一屏要放两个框 */
function CompactAction({ file }: { file: CourseFile }) {
  const i18n = useI18n();
  const download = useDownloadFile();
  const markSeen = useMarkSeen();
  const toast = useToast();

  if (file.medium === "link") {
    return (
      <a
        href={file.external_url ?? "#"}
        target="_blank"
        rel="noreferrer noopener"
        onClick={() => markSeen.mutate([file.id])}
        className="shrink-0 rounded-lg bg-sky-600 px-3 py-1 text-xs font-medium text-white hover:bg-sky-500 dark:bg-sky-500 dark:hover:bg-sky-400"
      >
        {i18n.t("files.open")}
      </a>
    );
  }
  // Canvas 上已经没了的东西点不动。星标框里会出现这种行：用户收藏过，
  // 所以记录留着，但下不下来
  if (file.is_deleted_on_canvas) {
    return (
      <span className="shrink-0 text-xs text-neutral-400">{i18n.t("files.deletedShort")}</span>
    );
  }
  return (
    <button
      onClick={() => {
        if (download.isPending) return;
        download.mutate(file, { onError: (err) => toast(apiMessage(err, i18n), "error") });
      }}
      disabled={download.isPending}
      className="inline-flex w-[4.5rem] shrink-0 items-center justify-center gap-1 rounded-lg bg-neutral-900 px-3 py-1 text-xs font-medium text-white hover:bg-neutral-700 disabled:opacity-60 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
    >
      {download.isPending && <Spinner className="size-3" />}
      {i18n.t(download.isPending ? "files.downloading" : "files.download")}
    </button>
  );
}
