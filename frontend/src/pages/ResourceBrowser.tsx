import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  useDownloadResourceBundle,
  useResourceCourses,
  useResourceFilterOptions,
  useResources,
  useSetResourceStar,
} from "@/api/hooks";
import ConfirmDialog from "@/components/ConfirmDialog";
import FileDetail from "@/components/FileDetail";
import FileList, { BAR_BTN, BAR_BTN_OUTLINE, StarIcon } from "@/components/FileList";
import FilterPanel from "@/components/FilterPanel";
import Pagination from "@/components/Pagination";
import Spinner from "@/components/Spinner";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import {
  activeCount,
  toQuery as filterToQuery,
  EMPTY_FILTER,
  type FileFilter,
} from "@/lib/fileFilter";
import { useI18n } from "@/lib/i18n";
import { loadPageSize, savePageSize } from "@/lib/prefs";
import type { CourseFile, SortBy, SortOrder } from "@/types";

/** 控件统一高度，和课程页那一排是同一个尺寸 */
const CONTROL_H = "h-9";

function FilterGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3 5h18M6 12h12M10 19h4" />
    </svg>
  );
}

/**
 * 跨课程的资源列表。两个页面共用：
 *
 * - `/resources/find`（查找资源）：左边按**课程**分组，右边搜索 + 筛选 + 列表
 * - `/resources/starred`（全部星标）：同一套东西，只看星标，不要左边那一列
 *   —— 星标本来就是"我挑出来的那几份"，再按课程切一刀只会多一步点击；
 *   真要按课程收窄，筛选面板里有课程这个轴。
 *
 * **没有分组，但有批量操作。** 跨课程的分组没有意义（"Week 3"在六门课里各指
 * 一件事）；而批量下载和批量星标是对"我选中的这几份"的操作，跟哪门课无关，
 * 所以照常给。打包走的是课程页同一个后端实现，只多一件事：跨课程时按课程
 * 分子目录 —— 六门课各有一份 syllabus.pdf 是常态。
 */
export default function ResourceBrowser({ starred = false }: { starred?: boolean }) {
  const i18n = useI18n();
  const { t, zone } = i18n;
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("date");
  const [order, setOrder] = useState<SortOrder>("desc");
  const [filter, setFilter] = useState<FileFilter>(EMPTY_FILTER);
  const [filterOpen, setFilterOpen] = useState(false);
  // 左边选中的课程。null = 全部课程。它和筛选面板里的课程轴是两件事：
  // 这个是"当前在看哪一门"（导航），那个是"只要这几门"（条件）
  const [course, setCourse] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(loadPageSize);
  const [detail, setDetail] = useState<CourseFile | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [confirm, setConfirm] = useState<{ title: string; run: () => Promise<void> } | null>(
    null,
  );

  const filterQuery = useMemo(() => filterToQuery(filter, zone), [filter, zone]);
  const setStar = useSetResourceStar();
  const bundle = useDownloadResourceBundle();
  const toast = useToast();

  const query = useMemo(
    () => ({
      ...filterQuery,
      // 导航选中的那门课叠加在筛选之上，不是覆盖
      course: course === null ? filterQuery.course : [course],
      q: search,
      starred: starred || undefined,
      sortBy,
      order,
    }),
    [filterQuery, course, search, starred, sortBy, order],
  );

  const { data: files, isLoading } = useResources(query);
  const { data: buckets } = useResourceCourses(filterQuery, starred);
  const { data: options } = useResourceFilterOptions();

  // 换任何条件都回到第一页，否则会停在一张空白页上
  useEffect(() => {
    setPage(1);
  }, [query]);

  // 开始搜索就按相关度排，清空搜索回到按日期。只在这两个转折点上动一次 ——
  // 搜索期间用户自己改过排序就不再被抢回去（和课程页同一条规则）
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
  const filterCount = activeCount(filter);
  const allVisibleSelected = visible.length > 0 && visible.every((f) => selected.has(f.id));

  const star = (fileIds: number[], next: boolean) => {
    setStar.mutate({ fileIds, starred: next });
    if (fileIds.length > 1) setSelected(new Set());
  };

  /** 取消星标要确认：星标是用户自己攒起来的，批量误清没有撤销 */
  const askUnstar = (fileIds: number[]) =>
    setConfirm({
      title: t("confirm.unstar", { n: fileIds.length }),
      run: async () => {
        await setStar.mutateAsync({ fileIds, starred: false });
        setSelected(new Set());
      },
    });

  const downloadSelected = async () => {
    if (bundle.isPending) return;
    try {
      const { written, failed, links } = await bundle.mutateAsync([...selected]);
      // 打包是长操作，成功之后保持选中反而让人不确定是不是真下完了
      setSelected(new Set());
      // 有东西没进包就得说清楚 —— 清单在包里的说明文件里，没有这条提示的话
      // 用户得先解压才发现少了。提示语和课程页一字不差
      const notes = [
        links > 0 ? t("bundle.links", { n: links }) : "",
        failed > 0 ? t("bundle.failed", { n: failed }) : "",
      ].filter(Boolean);
      if (notes.length > 0) {
        toast(
          t("bundle.partial", {
            n: written,
            notes: notes.join(i18n.lang === "zh" ? "、" : ", "),
          }),
          "error",
        );
      } else {
        toast(t("bundle.done", { n: written }));
      }
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  return (
    <div className="space-y-4">
      {starred && (
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-lg font-semibold">{t("resources.starred")}</h2>
          {/* 二级页面要有回去的路：浏览器的后退键当然能用，但一个页面不该
              只靠浏览器按钮才回得去。做成按钮而不是一行小灰字 —— 小灰字不像能点 */}
          <Link
            to="/resources"
            className="rounded-lg border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-neutral-100 dark:border-neutral-600 dark:text-neutral-200 dark:hover:bg-neutral-800"
          >
            ← {t("common.back")}
          </Link>
        </div>
      )}

      <div className={starred ? "" : "flex gap-6"}>
        {/* 查找页左边按课程分组。计数跟着筛选走，被筛空的课整个不出现 ——
            空筐不是导航 */}
        {!starred && (
          <nav className="w-56 shrink-0 space-y-1">
            <button
              onClick={() => setCourse(null)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm transition ${
                course === null
                  ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                  : "text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-neutral-800"
              }`}
            >
              <span>{t("resources.allCourses")}</span>
              <span className="tabular-nums opacity-70">
                {(buckets ?? []).reduce((n, b) => n + b.file_count, 0)}
              </span>
            </button>
            {(buckets ?? []).map((b) => (
              <button
                key={b.course_id}
                onClick={() => setCourse(b.course_id)}
                title={b.name}
                className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
                  course === b.course_id
                    ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                    : "text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-neutral-800"
                }`}
              >
                <span className="min-w-0 truncate">{b.course_code || b.name}</span>
                <span className="shrink-0 tabular-nums opacity-70">{b.file_count}</span>
              </button>
            ))}
            {buckets?.length === 0 && (
              <p className="px-3 text-xs text-neutral-400">{t("resources.noCourses")}</p>
            )}
          </nav>
        )}

        <section className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {/* 全选在最左边：勾选是对"下面这一页"的操作，入口该挨着列表 ——
                和课程页那一排是同一个顺序 */}
            <button
              onClick={() =>
                setSelected(allVisibleSelected ? new Set() : new Set(visible.map((f) => f.id)))
              }
              disabled={visible.length === 0}
              className={`${CONTROL_H} shrink-0 rounded-lg border border-neutral-300 px-3 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800`}
            >
              {allVisibleSelected ? t("files.clearAll") : t("files.selectAll")}
            </button>
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
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("files.searchPlaceholder")}
              title={t("files.searchHint")}
              className={`${CONTROL_H} min-w-40 flex-1 rounded-lg border border-neutral-300 px-3 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900`}
            />
            {/* 选中之后，搜索框右边整块换成批量操作 —— 排序控件这时候没人用。
                和课程页同一套：星标 / 取消星标 / 下载，外加一个退出选择 */}
            {selected.size > 0 ? (
              <div
                className={`${CONTROL_H} flex shrink-0 items-center gap-2 rounded-lg bg-neutral-100 px-2.5 dark:bg-neutral-800`}
              >
                <span className="text-xs text-neutral-500">
                  {t("files.selectedCount", { n: selected.size })}
                </span>
                <button
                  onClick={() => star([...selected], true)}
                  className={BAR_BTN_OUTLINE}
                >
                  <StarIcon filled />
                  {t("files.star")}
                </button>
                <button onClick={() => askUnstar([...selected])} className={BAR_BTN_OUTLINE}>
                  {t("files.unstar")}
                </button>
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
              {/* 选项和课程页一字不差 —— 同一件事在两个页面上必须是同一个
                  说法。「最新」那个框按变动时间排，那是它的定义，不是这里的一个选项 */}
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

          <FileList
            files={visible}
            isLoading={isLoading}
            selected={selected}
            onToggle={(id) =>
              setSelected((prev) => {
                const next = new Set(prev);
                if (!next.delete(id)) next.add(id);
                return next;
              })
            }
            onOpenDetail={setDetail}
            onStar={star}
            emptyText={
              filterCount > 0 || hasSearch
                ? t("filter.emptyResult")
                : t(starred ? "resources.emptyStarred" : "resources.emptyFind")
            }
          />

          {total > 0 && (
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
          )}
        </section>
      </div>

      {filterOpen && (
        <FilterPanel
          value={filter}
          // 全局页的轴只有三个：文件/链接、课程、Canvas 来源（外加日期范围）。
          // 容器（Week 3 / Lectures）和类型不给：容器名在不同课里重名是常态，
          // 跨课程平铺出来是一串认不出归属的词
          options={{
            boards: (options?.boards ?? []).map((b) => ({ id: b.id, name: b.label })),
            containers: [],
            kinds: [],
            courses: (options?.courses ?? []).map((c) => ({
              id: c.course_id,
              name: c.course_code || c.name,
            })),
          }}
          onCancel={() => setFilterOpen(false)}
          onApply={(next) => {
            setFilter(next);
            setFilterOpen(false);
          }}
        />
      )}
      {detail && <FileDetail file={detail} onClose={() => setDetail(null)} />}
      {confirm && (
        <ConfirmDialog
          title={confirm.title}
          confirmLabel={t("confirm.unstarAction")}
          pending={setStar.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            await confirm.run();
            setConfirm(null);
          }}
        />
      )}
    </div>
  );
}
