import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/lib/i18n";
import { isOverdue } from "@/lib/planner";
import type { Course, PlannerItem, PlannerKind, TaskList } from "@/types";

const BTN =
  "inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-sm transition";
const BTN_IDLE =
  "border-neutral-300 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800";
const BTN_ACTIVE =
  "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900";

function Caret() {
  return (
    <svg viewBox="0 0 24 24" className="size-3.5 opacity-60" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** 点外面关掉。下拉菜单都用它 */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** 「课程」和「类别」两个按钮，点哪个都打开同一个面板 —— 两样一起选，不用点两次 */
export function CourseKindFilter({
  courses,
  lists = [],
  kindOptions,
  selectedCourses,
  selectedKinds,
  selectedLists = [],
  defaultKinds,
  onChange,
}: {
  courses: Course[];
  /** 用户自建的清单。一个都没有时这一列不出现 */
  lists?: TaskList[];
  kindOptions: PlannerKind[];
  /** 这个页面默认显示哪些类别。选中的正好是它时，按钮就叫「类别」、不高亮 */
  defaultKinds: PlannerKind[];
  /** 空 = 全部课程 */
  selectedCourses: number[];
  selectedKinds: PlannerKind[];
  selectedLists?: number[];
  onChange: (next: { courses: number[]; kinds: PlannerKind[]; lists: number[] }) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));

  const atDefault =
    selectedKinds.length === defaultKinds.length &&
    defaultKinds.every((k) => selectedKinds.includes(k));
  const single = selectedCourses.length === 1 ? courses.find((c) => c.id === selectedCourses[0]) : null;
  const courseLabel =
    selectedLists.length > 0 && selectedCourses.length === 0
      ? selectedLists.length === 1
        ? (lists.find((l) => l.id === selectedLists[0])?.name ?? t("planner.lists"))
        : t("planner.nLists", { n: selectedLists.length })
      : selectedCourses.length === 0
        ? t("planner.allCourses")
        : single
          ? single.course_code || single.name
          : t("planner.nCourses", { n: selectedCourses.length });
  const scoped = selectedCourses.length > 0 || selectedLists.length > 0;
  const emit = (p: { courses?: number[]; kinds?: PlannerKind[]; lists?: number[] }) =>
    onChange({
      courses: p.courses ?? selectedCourses,
      kinds: p.kinds ?? selectedKinds,
      lists: p.lists ?? selectedLists,
    });
  const kindLabel = atDefault
    ? t("planner.kinds")
    : selectedKinds.length === 1
      ? t(`planner.kind.${selectedKinds[0]}`)
      : t("planner.nKinds", { n: selectedKinds.length });

  return (
    <div ref={ref} className="relative flex items-center gap-2">
      <button
        onClick={() => setOpen(!open)}
        className={`${BTN} ${scoped ? BTN_ACTIVE : BTN_IDLE}`}
      >
        {single && (
          <span className="size-2 rounded-full" style={{ backgroundColor: single.color ?? "#a3a3a3" }} />
        )}
        <span className="max-w-[12rem] truncate">{courseLabel}</span>
        <Caret />
      </button>
      <button onClick={() => setOpen(!open)} className={`${BTN} ${atDefault ? BTN_IDLE : BTN_ACTIVE}`}>
        {kindLabel}
        <Caret />
      </button>

      {open && (
        <div
          className={`absolute left-0 top-10 z-40 grid max-w-[92vw] gap-4 rounded-xl border border-neutral-200 bg-white p-3 shadow-xl dark:border-neutral-600 dark:bg-neutral-800 ${
            lists.length ? "w-[46rem] grid-cols-[3fr_2fr_2fr]" : "w-[34rem] grid-cols-[3fr_2fr]"
          }`}
        >
          <section>
            <MenuHeader
              title={t("planner.courses")}
              action={t("planner.selectAll")}
              onAction={() => emit({ courses: [] })}
            />
            <div className="max-h-72 space-y-0.5 overflow-y-auto">
              {courses.map((c) => (
                <CheckRow
                  key={c.id}
                  checked={selectedCourses.length === 0 || selectedCourses.includes(c.id)}
                  onClick={() => {
                    // "全部"时点一门 = 只看这一门；之后再点是加减
                    const next =
                      selectedCourses.length === 0 ? [c.id] : toggle(selectedCourses, c.id);
                    emit({ courses: next.length === courses.length ? [] : next });
                  }}
                >
                  <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: c.color ?? "#a3a3a3" }} />
                  <span className="truncate">{c.course_code || c.name}</span>
                </CheckRow>
              ))}
            </div>
          </section>
          <section>
            <MenuHeader
              title={t("planner.kinds")}
              action={t("planner.selectAll")}
              onAction={() => emit({ kinds: kindOptions })}
            />
            <div className="space-y-0.5">
              {kindOptions.map((k) => (
                <CheckRow
                  key={k}
                  checked={selectedKinds.includes(k)}
                  onClick={() => {
                    const next = toggle(selectedKinds, k);
                    // 一个都不选等于什么都看不到，没有意义 —— 退回全选
                    emit({
                      kinds: next.length ? kindOptions.filter((x) => next.includes(x)) : kindOptions,
                    });
                  }}
                >
                  {t(`planner.kind.${k}`)}
                </CheckRow>
              ))}
            </div>
          </section>
          {lists.length > 0 && (
            <section>
              <MenuHeader
                title={t("planner.lists")}
                action={t("planner.selectAll")}
                onAction={() => emit({ lists: [] })}
              />
              <div className="max-h-72 space-y-0.5 overflow-y-auto">
                {lists.map((l) => (
                  <CheckRow
                    key={l.id}
                    checked={selectedLists.length === 0 || selectedLists.includes(l.id)}
                    onClick={() => {
                      const next =
                        selectedLists.length === 0 ? [l.id] : toggle(selectedLists, l.id);
                      emit({ lists: next.length === lists.length ? [] : next });
                    }}
                  >
                    <span
                      className="size-2 shrink-0 rounded-full"
                      style={{ backgroundColor: l.color ?? "#64748b" }}
                    />
                    <span className="truncate">{l.name}</span>
                  </CheckRow>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function MenuHeader({
  title,
  action,
  onAction,
}: {
  title: string;
  action: string;
  onAction: () => void;
}) {
  return (
    <div className="mb-1 flex items-center justify-between px-2">
      <span className="text-xs font-medium text-neutral-400">{title}</span>
      <button onClick={onAction} className="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100">
        {action}
      </button>
    </div>
  );
}

function CheckRow({
  checked,
  onClick,
  children,
}: {
  checked: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-neutral-100 dark:hover:bg-neutral-700"
    >
      <span
        className={`flex size-4 shrink-0 items-center justify-center rounded border text-[10px] ${
          checked
            ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
            : "border-neutral-300 dark:border-neutral-500"
        }`}
      >
        {checked && "✓"}
      </span>
      {children}
    </button>
  );
}

/** 单选下拉：状态、时间范围、排序 */
export function SelectMenu<T extends string>({
  value,
  options,
  onChange,
  isDefault,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  /** 不是默认值时按钮变实心 —— 列表变短了总得有个看得见的原因 */
  isDefault: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, () => setOpen(false));
  const current = options.find((o) => o.value === value) ?? options[0];
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} className={`${BTN} ${isDefault ? BTN_IDLE : BTN_ACTIVE}`}>
        {current.label}
        <Caret />
      </button>
      {open && (
        <div className="absolute left-0 top-10 z-40 min-w-[9rem] rounded-xl border border-neutral-200 bg-white p-1 shadow-xl dark:border-neutral-600 dark:bg-neutral-800">
          {options.map((o) => (
            <button
              key={o.value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-neutral-100 dark:hover:bg-neutral-700 ${
                o.value === value ? "font-medium" : ""
              }`}
            >
              {o.label}
              {o.value === value && <span className="text-xs">✓</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export type Source = "all" | "canvas" | "user";
/** 完成状态 */
export type State = "all" | "incomplete" | "completed" | "overdue";
/** 用户打的标记。和完成状态是两件事，所以分成两个下拉 */
export type Flag = "all" | "important" | "hidden";

/** 日历和任务页共用的那一组条件。任务页在此之上再加时间范围和排序 */
export interface PlannerFilter {
  /** 空 = 全部课程 */
  courses: number[];
  kinds: PlannerKind[];
  /** 空 = 不按清单筛。选了清单就只看这些清单里的自建任务 */
  lists: number[];
  source: Source;
  state: State;
  flag: Flag;
}

export function applyPlannerFilter(
  items: PlannerItem[],
  f: PlannerFilter,
  timeZone: string,
  today: string,
): PlannerItem[] {
  return items.filter((i) => {
    if (f.courses.length && (i.course_id === null || !f.courses.includes(i.course_id))) return false;
    if (!f.kinds.includes(i.kind)) return false;
    if (f.lists?.length && (i.list_id === null || !f.lists.includes(i.list_id))) return false;
    if (f.source !== "all" && i.source !== f.source) return false;
    // 隐藏的只在「已隐藏」里出现
    if (f.flag === "hidden" ? !i.hidden : i.hidden) return false;
    if (f.flag === "important" && !i.important) return false;
    if (f.state === "incomplete" && i.completed) return false;
    if (f.state === "completed" && !i.completed) return false;
    if (f.state === "overdue" && !isOverdue(i, timeZone, today)) return false;
    return true;
  });
}

/** 筛选条：课程 + 类别（同一个面板）、来源、完成状态、标记。页面自己的控件放 children */
export function PlannerFilterBar({
  courses,
  lists,
  kindOptions,
  defaultKinds,
  value,
  onChange,
  children,
}: {
  courses: Course[];
  lists?: TaskList[];
  kindOptions: PlannerKind[];
  defaultKinds: PlannerKind[];
  value: PlannerFilter;
  onChange: (next: PlannerFilter) => void;
  children?: React.ReactNode;
}) {
  const { t } = useI18n();
  const patch = (p: Partial<PlannerFilter>) => onChange({ ...value, ...p });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <CourseKindFilter
        courses={courses}
        lists={lists}
        kindOptions={kindOptions}
        defaultKinds={defaultKinds}
        selectedCourses={value.courses}
        selectedKinds={value.kinds}
        selectedLists={value.lists ?? []}
        onChange={(next) => patch(next)}
      />
      <SelectMenu<Source>
        value={value.source}
        isDefault={value.source === "all"}
        onChange={(source) => patch({ source })}
        options={(["all", "canvas", "user"] as Source[]).map((v) => ({
          value: v,
          label: t(`planner.source.${v}`),
        }))}
      />
      <SelectMenu<State>
        value={value.state}
        isDefault={value.state === "all"}
        onChange={(state) => patch({ state })}
        options={(["all", "incomplete", "completed", "overdue"] as State[]).map((v) => ({
          value: v,
          label: t(`planner.state.${v}`),
        }))}
      />
      <SelectMenu<Flag>
        value={value.flag}
        isDefault={value.flag === "all"}
        onChange={(flag) => patch({ flag })}
        options={(["all", "important", "hidden"] as Flag[]).map((v) => ({
          value: v,
          label: t(`planner.flag.${v}`),
        }))}
      />
      {children}
    </div>
  );
}
