import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useCourses, useSaveSidebarLayout } from "@/api/hooks";
import { ConcludedTag } from "@/App";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { useI18n } from "@/lib/i18n";
import type { Course } from "@/types";

/** 边栏上一行课程的显示状态。顺序由数组本身表达 —— 拖完的那个数组就是答案 */
interface Row {
  course: Course;
  visible: boolean;
}

const displayName = (c: Course) => c.course_code || c.name;

/** 课程管理：选择哪些课程出现在边栏，并调整顺序。
 *
 *  和「设置 → 选择同步课程」是两件事，界面上也分开：那边决定**要不要拉这门课的
 *  资料**（取消会删数据），这边只决定**边栏上看不看得见**。藏起来的课照常同步，
 *  URL 进去照样能用 —— 用取消同步来给边栏"清场"会把资料一起删掉，那不是同一个需求。
 */
export default function CourseManager() {
  // 藏起来的课也要列出来，否则藏了就再也改不回来
  const { data: courses, isLoading } = useCourses(true);
  const save = useSaveSidebarLayout();
  const navigate = useNavigate();
  const toast = useToast();
  const { t } = useI18n();

  // 服务端返回的就是当前顺序，直接当基准
  const server: Row[] = useMemo(
    () => (courses ?? []).map((c) => ({ course: c, visible: !c.is_hidden })),
    [courses],
  );
  // null = 用户还没动过，跟着服务端走（后台刷新也能带进来）
  const [draft, setDraft] = useState<Row[] | null>(null);
  const rows = draft ?? server;

  const [dragIndex, setDragIndex] = useState<number | null>(null);

  const signature = (list: Row[]) =>
    list.map((r) => `${r.course.id}:${r.visible ? 1 : 0}`).join(",");
  const dirty = draft !== null && signature(draft) !== signature(server);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= rows.length || from === to) return;
    const next = [...rows];
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setDraft(next);
  };

  const toggle = (id: number) =>
    setDraft(rows.map((r) => (r.course.id === id ? { ...r, visible: !r.visible } : r)));

  const sortAlphabetically = () =>
    setDraft(
      [...rows].sort((a, b) =>
        displayName(a.course).toLowerCase().localeCompare(displayName(b.course).toLowerCase()),
      ),
    );

  const submit = async () => {
    // 没改动就什么都不做 —— 一次"保存"没有任何后果却弹一条"已保存"，
    // 只会让人怀疑自己误改了什么
    if (!dirty) return;
    await save.mutateAsync(
      rows.map((r) => ({ course_id: r.course.id, is_hidden: !r.visible })),
    );
    setDraft(null);
    toast(t("manager.saved"));
  };

  const visibleCount = rows.filter((r) => r.visible).length;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* 页面本身已经说明了一切：一列课、一排勾选框、能拖。再写一段解释只是噪音 */}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("manager.title")}</h1>
        <button
          onClick={() => navigate(-1)}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {t("common.back")}
        </button>
      </header>

      <Panel
        title={t("manager.panel")}
        action={
          <span className="whitespace-nowrap text-xs text-neutral-400">
            {t("manager.visible", { n: visibleCount, total: rows.length })}
          </span>
        }
      >
        {isLoading && <p className="p-5 text-sm text-neutral-400">{t("common.loading")}</p>}
        {!isLoading && rows.length === 0 && (
          <p className="p-5 text-sm text-neutral-400">
            {t("manager.empty")}
          </p>
        )}
        {rows.map((row, index) => (
          <div
            key={row.course.id}
            draggable
            onDragStart={() => setDragIndex(index)}
            onDragEnd={() => setDragIndex(null)}
            onDragOver={(e) => {
              // 不 preventDefault 的话这一行根本不接受放置，拖上去是禁止光标
              e.preventDefault();
              if (dragIndex === null || dragIndex === index) return;
              move(dragIndex, index);
              setDragIndex(index);
            }}
            className={`flex items-center gap-3 border-t border-neutral-200 px-5 py-3 first:border-t-0 dark:border-neutral-800 ${
              dragIndex === index ? "bg-neutral-100 dark:bg-neutral-800" : ""
            }`}
          >
            <span
              title={t("manager.drag")}
              className="cursor-grab select-none text-neutral-300 active:cursor-grabbing dark:text-neutral-600"
            >
              ⠿
            </span>
            <input
              type="checkbox"
              checked={row.visible}
              onChange={() => toggle(row.course.id)}
              title={t("manager.showInSidebar")}
              className="size-4 shrink-0"
            />
            <span className="min-w-0 flex-1">
              {/* 不截断：Canvas 的课程名常常只在结尾才分得出彼此 */}
              <span
                className={`block text-sm leading-snug [overflow-wrap:anywhere] ${
                  row.visible ? "" : "text-neutral-400"
                }`}
              >
                {displayName(row.course)}
                {row.course.is_concluded && <ConcludedTag />}
              </span>
              <span className="text-xs text-neutral-400">
                {row.course.term_name || "—"} ·{" "}
                {t("course.resourceCount", { n: row.course.file_count })}
                {!row.visible && ` · ${t("manager.hidden")}`}
              </span>
            </span>
          </div>
        ))}
      </Panel>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          onClick={sortAlphabetically}
          disabled={rows.length === 0}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {t("manager.sortAlpha")}
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDraft(null)}
            disabled={!dirty || save.isPending}
            className="rounded-lg border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {t("manager.revert")}
          </button>
          {/* 有改动才变成实心 —— 颜色就是"这次点下去有没有后果" */}
          <button
            onClick={submit}
            disabled={save.isPending}
            className={`rounded-lg px-4 py-1.5 text-sm disabled:opacity-50 ${
              dirty
                ? "bg-neutral-900 text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900"
                : "bg-neutral-200 text-neutral-500 dark:bg-neutral-700 dark:text-neutral-300"
            }`}
          >
            {save.isPending ? t("common.saving") : t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
