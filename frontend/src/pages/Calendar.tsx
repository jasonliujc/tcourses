import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { useCourses, usePlannerItems, useTaskLists } from "@/api/hooks";
import PlannerCard, { DraftTaskCard } from "@/components/PlannerCard";
import {
  PlannerFilterBar,
  applyPlannerFilter,
  type PlannerFilter,
} from "@/components/PlannerFilters";
import Popover from "@/components/Popover";
import { useI18n } from "@/lib/i18n";
import {
  ALL_KINDS,
  CALENDAR_KINDS,
  addDays,
  fromLocalInput,
  isTimedBlock,
  itemDay,
  loadFilter,
  loadPref,
  longDay,
  minuteOfDay,
  monthGrid,
  saveFilter,
  savePref,
  startOfWeekKey,
  timeLabel,
  tint,
  todayKey,
} from "@/lib/planner";
import type { PlannerItem, TaskInput } from "@/types";

type View = "month" | "week" | "day";

const DEFAULT_FILTER: PlannerFilter = {
  courses: [],
  kinds: CALENDAR_KINDS,
  lists: [],
  source: "all",
  state: "all",
  flag: "all",
};

const HOUR_PX = 48;
/** 打开周 / 日视图时滚到几点 */
const SCROLL_TO_HOUR = 8;
const CHIP_PX = 20;

type Open = (item: PlannerItem, anchor: DOMRect) => void;
type Create = (defaults: TaskInput, anchor: DOMRect) => void;

/** 来源用样式区分：Canvas 同步来的是实心色块，自己建的是描边，
 *  重复任务往后的预览是虚线。颜色只表示课程（或清单） */
function itemStyle(item: PlannerItem, strength: number): React.CSSProperties {
  const color = item.color ?? "#a3a3a3";
  if (item.source === "user") {
    return {
      border: `1px ${item.virtual ? "dashed" : "solid"} ${color}`,
      backgroundColor: tint(color, item.virtual ? 0.03 : 0.08),
    };
  }
  return { backgroundColor: tint(color, strength), borderLeft: `3px solid ${color}` };
}

/** 一个时刻在屏幕上的位置，草稿卡片贴着它弹出 */
function pointRect(e: React.MouseEvent): DOMRect {
  return new DOMRect(e.clientX, e.clientY, 1, 1);
}

export default function Calendar() {
  const { t, zone, lang } = useI18n();
  const { data: courses } = useCourses();
  const { data: items } = usePlannerItems({ includeHidden: true });
  const { data: lists } = useTaskLists();
  const [view, setView] = useState<View>(() => loadPref("calendar.view", "month"));
  const [anchor, setAnchor] = useState(() => todayKey(zone));
  const [filter, setFilter] = useState<PlannerFilter>(() =>
    loadFilter("calendar.filter.v3", DEFAULT_FILTER, ALL_KINDS),
  );
  const [open, setOpen] = useState<{ key: string; rect: DOMRect } | null>(null);
  const [draft, setDraft] = useState<{ defaults: TaskInput; rect: DOMRect } | null>(null);

  useEffect(() => savePref("calendar.view", view), [view]);
  useEffect(() => saveFilter("calendar.filter.v3", filter, ALL_KINDS), [filter]);

  const today = todayKey(zone);
  const byDay = useMemo(() => {
    const map = new Map<string, PlannerItem[]>();
    for (const item of applyPlannerFilter(items ?? [], filter, zone, today)) {
      const day = itemDay(item, zone);
      if (!day) continue;
      map.set(day, [...(map.get(day) ?? []), item]);
    }
    for (const list of map.values()) {
      list.sort((a, b) => (a.date ?? "").localeCompare(b.date ?? ""));
    }
    return map;
  }, [items, filter, zone, today]);

  const step = (dir: number) => {
    if (view === "day") setAnchor(addDays(anchor, dir));
    else if (view === "week") setAnchor(addDays(anchor, dir * 7));
    else {
      const [y, m] = anchor.split("-").map(Number);
      setAnchor(new Date(Date.UTC(y, m - 1 + dir, 1, 12)).toISOString().slice(0, 10));
    }
  };

  const locale = lang === "zh" ? "zh-CN" : "en-US";
  const label =
    view === "month"
      ? new Intl.DateTimeFormat(locale, { year: "numeric", month: "long", timeZone: "UTC" }).format(
          new Date(`${anchor.slice(0, 7)}-15T12:00:00Z`),
        )
      : view === "week"
        ? (() => {
            const s = startOfWeekKey(anchor);
            return `${longDay(s, lang)} – ${longDay(addDays(s, 6), lang)}`;
          })()
        : longDay(anchor, lang);

  const openItem: Open = useCallback(
    (item, rect) => {
      // 重复任务往后的预览不是真的一条：点开的是当前那一次
      const real = item.virtual
        ? (items ?? []).find((i) => i.source === "user" && !i.virtual && i.id === item.id)
        : item;
      setDraft(null);
      setOpen({ key: (real ?? item).item_key, rect });
    },
    [items],
  );
  const create: Create = useCallback((defaults, rect) => {
    setOpen(null);
    setDraft({ defaults, rect });
  }, []);
  const close = useCallback(() => setOpen(null), []);
  const closeDraft = useCallback(() => setDraft(null), []);
  const current = open ? (items ?? []).find((i) => i.item_key === open.key) : undefined;

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-4">
          <h1 className="text-2xl font-semibold tracking-tight">{t("calendar.title")}</h1>
          <span className="text-xl font-semibold text-neutral-700 dark:text-neutral-200">
            {label}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={(e) =>
              create(
                { due_date: view === "day" ? anchor : today },
                e.currentTarget.getBoundingClientRect(),
              )
            }
            className="h-8 whitespace-nowrap rounded-lg bg-neutral-900 px-3 text-sm font-medium text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          >
            + {t("task.new")}
          </button>
          <div className="flex overflow-hidden rounded-lg border border-neutral-300 dark:border-neutral-700">
            {(["month", "week", "day"] as View[]).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1.5 text-sm ${
                  view === v
                    ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                    : "hover:bg-neutral-100 dark:hover:bg-neutral-800"
                }`}
              >
                {t(`calendar.${v}`)}
              </button>
            ))}
          </div>
          <NavButton onClick={() => step(-1)}>‹</NavButton>
          <button
            onClick={() => setAnchor(today)}
            className="h-8 whitespace-nowrap rounded-lg border border-neutral-300 px-3 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {t("calendar.today")}
          </button>
          <NavButton onClick={() => step(1)}>›</NavButton>
        </div>
      </header>

      <PlannerFilterBar
        courses={courses ?? []}
        lists={lists ?? []}
        kindOptions={ALL_KINDS}
        defaultKinds={CALENDAR_KINDS}
        value={filter}
        onChange={setFilter}
      />

      <div className="min-h-0 flex-1">
        {view === "month" ? (
          <MonthView
            anchor={anchor}
            today={today}
            byDay={byDay}
            selected={open?.key}
            onOpen={openItem}
            onCreate={create}
            onDay={(day) => {
              setAnchor(day);
              setView("day");
            }}
          />
        ) : (
          <TimeGrid
            days={
              view === "week"
                ? Array.from({ length: 7 }, (_, i) => addDays(startOfWeekKey(anchor), i))
                : [anchor]
            }
            today={today}
            byDay={byDay}
            selected={open?.key}
            onOpen={openItem}
            onCreate={create}
          />
        )}
      </div>

      {open && current && (
        <Popover anchor={open.rect} onClose={close}>
          <PlannerCard item={current} onDeleted={close} />
        </Popover>
      )}
      {draft && (
        <Popover anchor={draft.rect} onClose={closeDraft}>
          <DraftTaskCard
            defaults={draft.defaults}
            onCreated={(item) => {
              setDraft(null);
              setOpen({ key: item.item_key, rect: draft.rect });
            }}
          />
        </Popover>
      )}
    </div>
  );
}

function NavButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className="flex size-8 items-center justify-center rounded-lg border border-neutral-300 text-lg leading-none hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
    >
      {children}
    </button>
  );
}

function useWeekdayNames(): string[] {
  const { lang } = useI18n();
  const locale = lang === "zh" ? "zh-CN" : "en-US";
  // 2024-01-01 是周一
  return Array.from({ length: 7 }, (_, i) =>
    new Intl.DateTimeFormat(locale, { weekday: "short", timeZone: "UTC" }).format(
      new Date(Date.UTC(2024, 0, 1 + i, 12)),
    ),
  );
}

/** 一条在格子里的样子。颜色 = 课程；完成的划掉变淡；悬停和选中都有反应 */
function Chip({
  item,
  selected,
  onOpen,
}: {
  item: PlannerItem;
  selected: boolean;
  onOpen: Open;
}) {
  const { zone } = useI18n();
  const time = item.all_day || !item.date ? null : timeLabel(item.date, zone);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onOpen(item, e.currentTarget.getBoundingClientRect());
      }}
      onDoubleClick={(e) => e.stopPropagation()}
      title={item.title}
      className={`flex w-full min-w-0 items-center gap-1 rounded px-1.5 text-left text-[11px] leading-[18px] transition hover:-translate-y-px hover:shadow-md hover:brightness-110 ${
        item.completed || item.hidden || item.virtual ? "opacity-50" : ""
      } ${selected ? "ring-2 ring-neutral-900 dark:ring-white" : ""}`}
      style={itemStyle(item, 0.18)}
    >
      {item.important && <span className="text-amber-500">★</span>}
      {time && <span className="shrink-0 tabular-nums text-neutral-500 dark:text-neutral-400">{time}</span>}
      <span className={`truncate ${item.completed ? "line-through" : ""}`}>{item.title}</span>
    </button>
  );
}

function MonthView({
  anchor,
  today,
  byDay,
  selected,
  onOpen,
  onCreate,
  onDay,
}: {
  anchor: string;
  today: string;
  byDay: Map<string, PlannerItem[]>;
  selected?: string;
  onOpen: Open;
  onCreate: Create;
  onDay: (day: string) => void;
}) {
  const weekdays = useWeekdayNames();
  const [y, m] = anchor.split("-").map(Number);
  const days = monthGrid(y, m);
  const month = anchor.slice(0, 7);

  // 一格放几条由格子实际多高决定：窗口高就多放，矮就少放，多出来的折成 "+N"
  const gridRef = useRef<HTMLDivElement>(null);
  const [limit, setLimit] = useState(3);
  useLayoutEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const measure = () => {
      const row = el.clientHeight / 6;
      setLimit(Math.max(1, Math.floor((row - 30) / (CHIP_PX + 2))));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
      <div className="grid grid-cols-7 border-b border-neutral-200 text-xs text-neutral-500 dark:border-neutral-800">
        {weekdays.map((w) => (
          <div key={w} className="px-2 py-1.5">
            {w}
          </div>
        ))}
      </div>
      <div ref={gridRef} className="grid min-h-0 flex-1 grid-cols-7 grid-rows-6">
        {days.map((day) => {
          const list = byDay.get(day) ?? [];
          const outside = !day.startsWith(month);
          const shown = list.length > limit ? list.slice(0, limit - 1) : list;
          return (
            <div
              key={day}
              onDoubleClick={() => onDay(day)}
              className={`group min-h-0 cursor-default select-none overflow-hidden border-b border-r border-neutral-100 p-1 transition-colors hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/30 ${
                outside ? "bg-neutral-50/60 dark:bg-neutral-950/40" : ""
              }`}
            >
              <div className="mb-0.5 flex items-center justify-between">
                <button
                  onClick={(e) => onCreate({ due_date: day }, e.currentTarget.getBoundingClientRect())}
                  onDoubleClick={(e) => e.stopPropagation()}
                  aria-label="+"
                  className="flex size-6 items-center justify-center rounded-full text-neutral-400 opacity-0 transition hover:bg-neutral-200 hover:text-neutral-900 group-hover:opacity-100 dark:hover:bg-neutral-700 dark:hover:text-neutral-100"
                >
                  +
                </button>
                <button
                  onClick={() => onDay(day)}
                  className={`flex size-6 items-center justify-center rounded-full text-xs tabular-nums transition hover:bg-neutral-200 dark:hover:bg-neutral-700 ${
                    day === today
                      ? "bg-red-500 font-semibold text-white hover:bg-red-500"
                      : outside
                        ? "text-neutral-300 dark:text-neutral-600"
                        : "text-neutral-600 dark:text-neutral-300"
                  }`}
                >
                  {Number(day.slice(8))}
                </button>
              </div>
              <div className="space-y-0.5">
                {shown.map((item) => (
                  <Chip key={item.item_key} item={item} selected={item.item_key === selected} onOpen={onOpen} />
                ))}
                {list.length > shown.length && (
                  <button
                    onClick={() => onDay(day)}
                    className="w-full rounded px-1.5 text-left text-[11px] leading-[18px] text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
                  >
                    +{list.length - shown.length}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 同一天里时间重叠的事件并排摆：贪心分道，每一簇按最多的道数均分宽度 */
function layoutBlocks(blocks: { item: PlannerItem; start: number; end: number }[]) {
  const sorted = [...blocks].sort((a, b) => a.start - b.start || b.end - a.end);
  const placed: { item: PlannerItem; start: number; end: number; lane: number; lanes: number }[] =
    [];
  let cluster: typeof placed = [];
  let clusterEnd = -1;
  const flush = () => {
    const lanes = Math.max(1, ...cluster.map((c) => c.lane + 1));
    for (const c of cluster) c.lanes = lanes;
    placed.push(...cluster);
    cluster = [];
  };
  for (const b of sorted) {
    if (b.start >= clusterEnd && cluster.length) flush();
    const used = new Set(cluster.filter((c) => c.end > b.start).map((c) => c.lane));
    let lane = 0;
    while (used.has(lane)) lane += 1;
    cluster.push({ ...b, lane, lanes: 1 });
    clusterEnd = Math.max(clusterEnd, b.end);
  }
  if (cluster.length) flush();
  return placed;
}

/** 周视图和日视图共用：只是列数不同 */
function TimeGrid({
  days,
  today,
  byDay,
  selected,
  onOpen,
  onCreate,
}: {
  days: string[];
  today: string;
  byDay: Map<string, PlannerItem[]>;
  selected?: string;
  onOpen: Open;
  onCreate: Create;
}) {
  const { zone } = useI18n();
  const weekdays = useWeekdayNames();
  const hours = Array.from({ length: 24 }, (_, i) => i);
  const scrollRef = useRef<HTMLDivElement>(null);
  const cols = `3.5rem repeat(${days.length}, minmax(0, 1fr))`;

  const columns = days.map((day) => {
    const list = byDay.get(day) ?? [];
    const timed = list.filter(isTimedBlock).map((item) => {
      const start = minuteOfDay(item.start_at!, zone);
      const endMin = minuteOfDay(item.end_at!, zone);
      // 跨过午夜的按当天结束处理
      return { item, start, end: Math.max(start + 15, endMin > start ? endMin : 1440) };
    });
    return { day, top: list.filter((i) => !isTimedBlock(i)), blocks: layoutBlocks(timed) };
  });

  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = SCROLL_TO_HOUR * HOUR_PX;
  }, [days.length]);

  const [now, setNow] = useState(() => new Date().toISOString());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date().toISOString()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const nowMin = minuteOfDay(now, zone);

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
      {/* 三块都预留滚动条的位置：只有时间网格会出滚动条，不预留的话它的列
          比上面两块窄一个滚动条的宽度，竖线就对不上 */}
      <div
        className="grid overflow-y-hidden border-b border-neutral-200 [scrollbar-gutter:stable] dark:border-neutral-800"
        style={{ gridTemplateColumns: cols }}
      >
        <div />
        {days.map((day) => {
          const wd = (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
          return (
            <div key={day} className="flex items-center gap-1.5 border-l border-neutral-100 px-2 py-1.5 dark:border-neutral-800">
              <span className="text-xs text-neutral-500">{weekdays[wd]}</span>
              <span
                className={`flex size-6 items-center justify-center rounded-full text-sm tabular-nums ${
                  day === today ? "bg-red-500 font-semibold text-white" : ""
                }`}
              >
                {Number(day.slice(8))}
              </span>
            </div>
          );
        })}
      </div>

      {/* 截止、全天事件放在顶上那一条：它们是一个时刻，不是一段时间 */}
      <div
        className="grid max-h-32 overflow-y-auto border-b border-neutral-200 [scrollbar-gutter:stable] dark:border-neutral-800"
        style={{ gridTemplateColumns: cols }}
      >
        <div />
        {columns.map(({ day, top }) => (
          <div
            key={day}
            // 点顶上这一条的空白处：建一个那天的全天任务
            onClick={(e) => onCreate({ due_date: day }, pointRect(e))}
            className="min-h-9 cursor-pointer space-y-0.5 border-l border-neutral-100 p-1 dark:border-neutral-800"
          >
            {top.map((item) => (
              <Chip key={item.item_key} item={item} selected={item.item_key === selected} onOpen={onOpen} />
            ))}
          </div>
        ))}
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto [scrollbar-gutter:stable]">
        <div className="relative grid" style={{ gridTemplateColumns: cols }}>
          <div>
            {hours.map((h) => (
              <div
                key={h}
                className="-translate-y-1.5 pr-2 text-right text-[10px] tabular-nums text-neutral-400"
                style={{ height: HOUR_PX }}
              >
                {h === 0 ? "" : `${String(h).padStart(2, "0")}:00`}
              </div>
            ))}
          </div>
          {columns.map(({ day, blocks }) => (
            <div
              key={day}
              // 点空白处：在那个时刻建一个任务，按半小时取整
              onClick={(e) => {
                const y = e.clientY - e.currentTarget.getBoundingClientRect().top;
                const minutes = Math.min(1410, Math.floor(((y / HOUR_PX) * 60) / 30) * 30);
                const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
                const mm = String(minutes % 60).padStart(2, "0");
                onCreate({ due_at: fromLocalInput(`${day}T${hh}:${mm}`, zone) }, pointRect(e));
              }}
              className="relative cursor-pointer border-l border-neutral-100 dark:border-neutral-800"
            >
              {hours.map((h) => (
                <div key={h} className="border-t border-neutral-100 dark:border-neutral-800" style={{ height: HOUR_PX }} />
              ))}
              {day === today && (
                <div
                  className="pointer-events-none absolute inset-x-0 z-20 border-t-2 border-red-500"
                  style={{ top: (nowMin / 60) * HOUR_PX }}
                >
                  <span className="absolute -left-1 -top-[5px] size-2 rounded-full bg-red-500" />
                </div>
              )}
              {blocks.map(({ item, start, end, lane, lanes }) => (
                <button
                  key={item.item_key}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpen(item, e.currentTarget.getBoundingClientRect());
                  }}
                  title={item.title}
                  className={`absolute overflow-hidden rounded-md px-1.5 py-0.5 text-left text-[11px] leading-tight transition hover:z-10 hover:shadow-lg hover:brightness-110 ${
                    item.completed || item.hidden ? "opacity-50" : ""
                  } ${item.item_key === selected ? "z-10 ring-2 ring-neutral-900 dark:ring-white" : ""}`}
                  style={{
                    top: (start / 60) * HOUR_PX + 1,
                    height: Math.max(18, ((end - start) / 60) * HOUR_PX - 2),
                    left: `calc(${(lane / lanes) * 100}% + 2px)`,
                    width: `calc(${100 / lanes}% - 4px)`,
                    ...itemStyle(item, 0.22),
                  }}
                >
                  <div className="truncate font-medium">
                    {item.important && <span className="text-amber-500">★ </span>}
                    {item.title}
                  </div>
                  <div className="truncate text-neutral-500 dark:text-neutral-400">
                    {timeLabel(item.start_at!, zone)}
                    {item.location_name && ` · ${item.location_name}`}
                  </div>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
