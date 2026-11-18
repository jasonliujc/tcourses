import { useCallback, useEffect, useMemo, useState } from "react";

import {
  useCourses,
  useCreateTask,
  usePlannerItems,
  useTaskLists,
  useUpdatePlannerItem,
} from "@/api/hooks";
import PlannerCard, { StarIcon, StatusBadges } from "@/components/PlannerCard";
import {
  PlannerFilterBar,
  SelectMenu,
  applyPlannerFilter,
  type PlannerFilter,
} from "@/components/PlannerFilters";
import Popover from "@/components/Popover";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import { useI18n } from "@/lib/i18n";
import {
  ALL_KINDS,
  BUCKET_ORDER,
  TASK_KINDS,
  addDays,
  bucketOf,
  dayLabel,
  isOverdue,
  itemDay,
  loadFilter,
  loadPref,
  saveFilter,
  savePref,
  startOfWeekKey,
  timeLabel,
  todayKey,
} from "@/lib/planner";
import type { PlannerItem, TaskInput } from "@/types";

type Range = "all" | "today" | "week" | "next7" | "next30" | "past30";
type Sort = "date" | "course" | "kind" | "list";
type Order = "asc" | "desc";

interface TaskFilter extends PlannerFilter {
  range: Range;
  sort: Sort;
  order: Order;
}

/** 第一次进来什么都显示（包括已完成的）；之后记住用户的选择 */
const DEFAULT_FILTER: TaskFilter = {
  courses: [],
  kinds: TASK_KINDS,
  lists: [],
  source: "all",
  state: "all",
  flag: "all",
  range: "all",
  sort: "date",
  order: "asc",
};

interface Group {
  id: string;
  label: string;
  color?: string | null;
  items: PlannerItem[];
  tone?: "past";
}

const CONTROL =
  "inline-flex h-8 items-center gap-1.5 rounded-lg border border-neutral-300 px-3 text-sm transition hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800";

export default function Tasks() {
  const { t, zone } = useI18n();
  const { data: courses } = useCourses();
  const { data: items, isLoading } = usePlannerItems({ includeHidden: true, projections: false });
  const { data: lists } = useTaskLists();
  const [filter, setFilter] = useState<TaskFilter>(() =>
    loadFilter("tasks.filter.v3", DEFAULT_FILTER, ALL_KINDS),
  );
  const [collapsed, setCollapsed] = useState<string[]>(() => loadPref("tasks.collapsed", []));
  const [open, setOpen] = useState<{ key: string; rect: DOMRect } | null>(null);

  useEffect(() => saveFilter("tasks.filter.v3", filter, ALL_KINDS), [filter]);
  useEffect(() => savePref("tasks.collapsed", collapsed), [collapsed]);

  const today = todayKey(zone);

  const visible = useMemo(() => {
    const inRange = (item: PlannerItem) => {
      if (filter.range === "all") return true;
      const day = itemDay(item, zone);
      if (!day) return false;
      switch (filter.range) {
        case "today":
          return day === today;
        case "week":
          return day >= startOfWeekKey(today) && day <= addDays(startOfWeekKey(today), 6);
        case "next7":
          return day >= today && day <= addDays(today, 6);
        case "next30":
          return day >= today && day <= addDays(today, 29);
        case "past30":
          return day >= addDays(today, -30) && day < today;
      }
    };
    return applyPlannerFilter(items ?? [], filter, zone, today).filter(inRange);
  }, [items, filter, zone, today]);

  const groups: Group[] = useMemo(() => {
    // 没日期的永远排最后，正序倒序都一样
    const sign = filter.order === "asc" ? 1 : -1;
    const byDate = (a: PlannerItem, b: PlannerItem) => {
      if (!a.date || !b.date) return a.date ? -1 : b.date ? 1 : 0;
      return sign * a.date.localeCompare(b.date);
    };

    if (filter.sort === "date") {
      const map = new Map<string, PlannerItem[]>();
      for (const item of visible) {
        const b = bucketOf(item, zone, today);
        map.set(b, [...(map.get(b) ?? []), item]);
      }
      // 按日期分组时，组的先后也跟着正序 / 倒序走：整页就是一条时间线
      const dated = BUCKET_ORDER.filter((b) => b !== "undated" && map.has(b));
      const order = filter.order === "asc" ? dated : [...dated].reverse();
      if (map.has("undated")) order.push("undated");
      return order.map((b) => ({
        id: `date:${b}`,
        label: t(`planner.bucket.${b}`),
        items: map.get(b)!.sort(byDate),
        tone: b === "past" ? "past" : undefined,
      }));
    }
    if (filter.sort === "course") {
      const out: Group[] = (courses ?? []).map((c) => ({
        id: `course:${c.id}`,
        label: c.course_code || c.name,
        color: c.color,
        items: visible.filter((i) => i.course_id === c.id).sort(byDate),
      }));
      const rest = visible.filter((i) => !courses?.some((c) => c.id === i.course_id)).sort(byDate);
      if (rest.length) out.push({ id: "course:none", label: t("planner.noCourse"), items: rest });
      return out.filter((g) => g.items.length);
    }
    if (filter.sort === "list") {
      const mine = visible.filter((i) => i.source === "user");
      const out: Group[] = (lists ?? []).map((l) => ({
        id: `list:${l.id}`,
        label: l.name,
        color: l.color,
        items: mine.filter((i) => i.list_id === l.id).sort(byDate),
      }));
      out.push({
        id: "list:none",
        label: t("task.noList"),
        items: mine.filter((i) => i.list_id === null).sort(byDate),
      });
      out.push({
        id: "list:canvas",
        label: "Canvas",
        items: visible.filter((i) => i.source === "canvas").sort(byDate),
      });
      return out.filter((g) => g.items.length);
    }
    return ALL_KINDS.map((k) => ({
      id: `kind:${k}`,
      label: t(`planner.kind.${k}`),
      items: visible.filter((i) => i.kind === k).sort(byDate),
    })).filter((g) => g.items.length);
  }, [visible, filter.sort, filter.order, courses, lists, zone, today, t]);

  const close = useCallback(() => setOpen(null), []);
  const current = open ? (items ?? []).find((i) => i.item_key === open.key) : undefined;
  const toggleGroup = (id: string) =>
    setCollapsed(collapsed.includes(id) ? collapsed.filter((g) => g !== id) : [...collapsed, id]);

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("tasks.title")}</h1>
        <span className="text-sm tabular-nums text-neutral-500">
          {visible.length} / {items?.length ?? 0}
        </span>
      </div>

      <PlannerFilterBar
        courses={courses ?? []}
        lists={lists ?? []}
        kindOptions={ALL_KINDS}
        defaultKinds={TASK_KINDS}
        value={filter}
        onChange={(next) => setFilter({ ...filter, ...next })}
      >
        <SelectMenu<Range>
          value={filter.range}
          isDefault={filter.range === "all"}
          onChange={(range) => setFilter({ ...filter, range })}
          options={(["all", "today", "week", "next7", "next30", "past30"] as Range[]).map((v) => ({
            value: v,
            label: t(`tasks.range.${v}`),
          }))}
        />
        <span className="flex-1" />
        <SelectMenu<Sort>
          value={filter.sort}
          isDefault
          onChange={(sort) => setFilter({ ...filter, sort })}
          options={(["date", "course", "kind", "list"] as Sort[]).map((v) => ({
            value: v,
            label: t(`tasks.sort.${v}`),
          }))}
        />
        <button
          onClick={() => setFilter({ ...filter, order: filter.order === "asc" ? "desc" : "asc" })}
          className={CONTROL}
        >
          {filter.order === "asc" ? t("files.orderAsc") : t("files.orderDesc")}
        </button>
      </PlannerFilterBar>

      <QuickAdd
        defaults={{
          // 正在看某一门课 / 某一个清单 / 今天的时候，新建的就放进去
          course_id: filter.courses.length === 1 ? filter.courses[0] : undefined,
          list_id: filter.lists.length === 1 ? filter.lists[0] : undefined,
          due_date: filter.range === "today" ? today : undefined,
        }}
        onCreated={(item, rect) => setOpen({ key: item.item_key, rect })}
      />

      {isLoading ? (
        <p className="py-10 text-center text-sm text-neutral-400">{t("common.loading")}</p>
      ) : groups.length === 0 ? (
        <p className="py-10 text-center text-sm text-neutral-400">{t("tasks.empty")}</p>
      ) : (
        groups.map((g) => {
          const isCollapsed = collapsed.includes(g.id);
          const overdue = g.tone === "past" ? g.items.filter((i) => !i.completed).length : 0;
          return (
            <section key={g.id}>
              <button
                onClick={() => toggleGroup(g.id)}
                className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
              >
                <svg
                  viewBox="0 0 24 24"
                  className={`size-3 transition ${isCollapsed ? "-rotate-90" : ""}`}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                >
                  <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                {g.color && <span className="size-2 rounded-full" style={{ backgroundColor: g.color }} />}
                {g.label}
                <span className="font-normal tabular-nums">{g.items.length}</span>
                {overdue > 0 && (
                  <span className="font-normal normal-case text-red-600">
                    · {t("tasks.overdueCount", { n: overdue })}
                  </span>
                )}
              </button>
              {!isCollapsed && (
                <ul className="divide-y divide-neutral-100 overflow-hidden rounded-xl border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900">
                  {g.items.map((item) => (
                    <TaskRow
                      key={item.item_key}
                      item={item}
                      today={today}
                      showDate={filter.sort !== "date" || (g.id !== "date:today" && g.id !== "date:tomorrow")}
                      selected={item.item_key === open?.key}
                      onOpen={(rect) => setOpen({ key: item.item_key, rect })}
                    />
                  ))}
                </ul>
              )}
            </section>
          );
        })
      )}

      {open && current && (
        <Popover anchor={open.rect} onClose={close}>
          <PlannerCard item={current} onDeleted={close} />
        </Popover>
      )}
    </div>
  );
}

function TaskRow({
  item,
  today,
  showDate,
  selected,
  onOpen,
}: {
  item: PlannerItem;
  today: string;
  showDate: boolean;
  selected: boolean;
  onOpen: (rect: DOMRect) => void;
}) {
  const { t, zone } = useI18n();
  const update = useUpdatePlannerItem();
  const toast = useToast();
  const day = itemDay(item, zone);
  const time = item.all_day || !item.date ? null : timeLabel(item.date, zone);
  const overdue = isOverdue(item, zone, today);

  const toggle = async () => {
    const res = await update.mutateAsync({ item, patch: { completed: !item.completed } });
    if (res.writeback === "failed") toast(t("planner.writebackFailed"), "error");
  };

  return (
    <li
      className={`flex items-center gap-3 px-4 py-2.5 transition hover:bg-neutral-50 dark:hover:bg-neutral-800/60 ${
        selected ? "bg-neutral-50 dark:bg-neutral-800/60" : ""
      }`}
    >
      <button
        onClick={toggle}
        disabled={update.isPending}
        aria-label={t("planner.markDone")}
        className="flex size-[18px] shrink-0 items-center justify-center rounded-full border-2 text-[10px] text-white transition hover:scale-110"
        style={{
          borderColor: item.color ?? "#a3a3a3",
          backgroundColor: item.completed ? (item.color ?? "#a3a3a3") : "transparent",
        }}
      >
        {item.completed && "✓"}
      </button>
      <button
        onClick={(e) => {
          // 浮层从这一行的右端弹出：整行太宽，以整行为锚点它会无处可放
          const row = e.currentTarget.closest("li")!.getBoundingClientRect();
          onOpen(new DOMRect(row.right - 140, row.top, 140, row.height));
        }}
        className="flex min-w-0 flex-1 items-center gap-3 text-left"
      >
        <div className="min-w-0 flex-1">
          <p
            className={`flex items-center gap-1.5 truncate text-sm ${
              item.completed ? "text-neutral-400 line-through" : "font-medium"
            } ${item.hidden ? "opacity-50" : ""}`}
          >
            {item.important && <StarIcon on className="size-3.5 shrink-0 text-amber-500" />}
            <span className="truncate">{item.title}</span>
          </p>
          <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-neutral-500">
            {item.course_code && <span className="max-w-[12rem] truncate">{item.course_code}</span>}
            {item.list_name && <span className="max-w-[10rem] truncate">{item.list_name}</span>}
            <span className="shrink-0">{t(`planner.kind.${item.kind}`)}</span>
            <StatusBadges item={item} />
            {item.steps.length > 0 && (
              <span className="shrink-0 tabular-nums">
                {item.steps.filter((x) => x.done).length}/{item.steps.length}
              </span>
            )}
            {item.repeat && <span className="shrink-0">↻</span>}
            {item.note && <span className="shrink-0">✎</span>}
          </div>
        </div>
        <span
          className={`shrink-0 text-right text-xs tabular-nums ${
            overdue ? "text-red-600" : "text-neutral-500"
          }`}
        >
          {showDate && day && <span className="mr-1.5">{dayLabel(day)}</span>}
          {time}
        </span>
      </button>
    </li>
  );
}

/** 任务页顶上的快速添加。回车就建，建完直接打开它的卡片接着填 */
function QuickAdd({
  defaults,
  onCreated,
}: {
  defaults: TaskInput;
  onCreated: (item: PlannerItem, rect: DOMRect) => void;
}) {
  const i18n = useI18n();
  const { t, zone } = i18n;
  const create = useCreateTask();
  const toast = useToast();
  const [title, setTitle] = useState("");

  const submit = async (input: HTMLInputElement) => {
    const value = title.trim();
    if (!value || create.isPending) return;
    try {
      const item = await create.mutateAsync({ ...defaults, title: value, tz: zone });
      setTitle("");
      const r = input.getBoundingClientRect();
      onCreated(item, new DOMRect(r.right - 140, r.top, 140, r.height));
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  return (
    <div className="flex items-center gap-3 rounded-xl border border-dashed border-neutral-300 px-4 py-2.5 focus-within:border-solid focus-within:border-neutral-400 dark:border-neutral-700 dark:focus-within:border-neutral-500">
      <span className="text-lg leading-none text-neutral-400">+</span>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit(e.currentTarget);
        }}
        placeholder={t("task.quickAdd")}
        className="min-w-0 flex-1 bg-transparent text-sm outline-none"
      />
    </div>
  );
}
