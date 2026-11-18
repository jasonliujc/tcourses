import { useEffect, useState } from "react";

import {
  useCourses,
  useCreateTask,
  useDeleteTask,
  useTaskLists,
  useUpdatePlannerItem,
} from "@/api/hooks";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import { useI18n } from "@/lib/i18n";
import {
  dayKey,
  fromLocalInput,
  itemDay,
  longDay,
  timeLabel,
  toLocalInput,
  todayKey,
} from "@/lib/planner";
import type { PlannerItem, PlannerItemUpdate, Repeat, TaskInput, TaskStep } from "@/types";

export function StatusBadges({ item }: { item: PlannerItem }) {
  const { t } = useI18n();
  const badges: { key: string; label: string; tone: string }[] = [];
  if (item.missing) badges.push({ key: "m", label: t("planner.status.missing"), tone: "red" });
  if (item.late) badges.push({ key: "l", label: t("planner.status.late"), tone: "amber" });
  if (item.excused) badges.push({ key: "e", label: t("planner.status.excused"), tone: "gray" });
  if (item.graded) badges.push({ key: "g", label: t("planner.status.graded"), tone: "green" });
  else if (item.submitted)
    badges.push({ key: "s", label: t("planner.status.submitted"), tone: "green" });
  if (item.has_feedback)
    badges.push({ key: "f", label: t("planner.status.feedback"), tone: "blue" });
  if (item.deleted_on_canvas)
    badges.push({ key: "d", label: t("planner.status.deleted"), tone: "gray" });
  const tones: Record<string, string> = {
    red: "bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300",
    amber: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300",
    green: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300",
    blue: "bg-sky-50 text-sky-700 dark:bg-sky-950/50 dark:text-sky-300",
    gray: "bg-neutral-100 text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300",
  };
  return (
    <>
      {badges.map((b) => (
        <span key={b.key} className={`shrink-0 rounded px-1.5 py-px text-[11px] ${tones[b.tone]}`}>
          {b.label}
        </span>
      ))}
    </>
  );
}

export function StarIcon({ on, className = "size-4" }: { on: boolean; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill={on ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinejoin="round"
    >
      <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />
    </svg>
  );
}

const ICON = "mt-0.5 size-4 shrink-0 text-neutral-400";

function ClockIcon() {
  return (
    <svg viewBox="0 0 24 24" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" strokeLinecap="round" />
    </svg>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z" />
      <circle cx="12" cy="10" r="2.3" />
    </svg>
  );
}

function NoteIcon() {
  return (
    <svg viewBox="0 0 24 24" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M5 6h14M5 11h14M5 16h9" strokeLinecap="round" />
    </svg>
  );
}

const FIELD =
  "w-full rounded-md bg-transparent px-1.5 py-1 -mx-1.5 outline-none hover:bg-neutral-100 focus:bg-neutral-100 dark:hover:bg-neutral-700/60 dark:focus:bg-neutral-700/60";

/** 一条日历 / 待办的卡片。日历和任务页共用，浮层里放的就是它。
 *
 *  改动即存（失焦或回车），没有「保存」按钮 —— 浮层点外面就关，
 *  要求用户记得先点保存，等于让他白改。
 */
export default function PlannerCard({
  item,
  onDeleted,
}: {
  item: PlannerItem;
  /** 自建任务被删掉之后，外面要把浮层关掉 */
  onDeleted?: () => void;
}) {
  return item.source === "user" ? (
    <TaskCard item={item} onDeleted={onDeleted} />
  ) : (
    <CanvasCard item={item} />
  );
}

function CanvasCard({ item }: { item: PlannerItem }) {
  const i18n = useI18n();
  const { t, zone, lang } = i18n;
  const update = useUpdatePlannerItem();
  const toast = useToast();

  const [title, setTitle] = useState(item.title);
  const [location, setLocation] = useState(item.location_name ?? "");
  const [note, setNote] = useState(item.note ?? "");
  const [editingTime, setEditingTime] = useState(false);

  useEffect(() => setTitle(item.title), [item.title]);
  useEffect(() => setLocation(item.location_name ?? ""), [item.location_name]);
  useEffect(() => setNote(item.note ?? ""), [item.note]);

  const apply = async (patch: PlannerItemUpdate) => {
    try {
      const res = await update.mutateAsync({ item, patch });
      if (res.writeback === "failed") toast(t("planner.writebackFailed"), "error");
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  const commitTitle = () => {
    const next = title.trim();
    if (next === item.title) return;
    // 改回 Canvas 原名或清空 = 恢复原名
    apply({ title: !next || next === item.canvas_title ? null : next });
  };
  const commitLocation = () => {
    const next = location.trim();
    if (next === (item.location_name ?? "")) return;
    apply({ location: !next || next === item.canvas_location ? null : next });
  };
  const commitNote = () => {
    if (note.trim() === (item.note ?? "")) return;
    apply({ note: note.trim() || null });
  };

  const isEvent = item.kind === "event";
  const day = itemDay(item, zone);
  const timeText = (() => {
    if (!day) return t("planner.noDate");
    const d = longDay(day, lang);
    if (isEvent && item.all_day) return `${d} · ${t("planner.allDay")}`;
    if (isEvent && item.start_at) {
      const end = item.end_at ? ` – ${timeLabel(item.end_at, zone)}` : "";
      return `${d} ${timeLabel(item.start_at, zone)}${end}`;
    }
    return item.date ? `${d} ${timeLabel(item.date, zone)}` : d;
  })();

  const canvasTime = (() => {
    if (isEvent && item.canvas_start_at) {
      const k = dayKey(item.canvas_start_at, zone);
      const end = item.canvas_end_at ? ` – ${timeLabel(item.canvas_end_at, zone)}` : "";
      return `${longDay(k, lang)} ${timeLabel(item.canvas_start_at, zone)}${end}`;
    }
    if (item.due_at) {
      return `${longDay(dayKey(item.due_at, zone), lang)} ${timeLabel(item.due_at, zone)}`;
    }
    return null;
  })();

  const itemDue =
    item.item_due_at &&
    `${longDay(dayKey(item.item_due_at, zone), lang)} ${timeLabel(item.item_due_at, zone)}`;

  return (
    <div className="text-sm">
      <div className="flex items-start gap-2.5 px-4 pt-4">
        <span
          className="mt-2 size-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: item.color ?? "#a3a3a3" }}
        />
        <div className="min-w-0 flex-1">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={commitTitle}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setTitle(item.title);
            }}
            className={`${FIELD} text-base font-semibold`}
          />
          {item.title !== item.canvas_title && (
            <p className="truncate text-xs text-neutral-400">Canvas · {item.canvas_title}</p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-neutral-500">
            {item.course_code && <span className="truncate">{item.course_code}</span>}
            <span>· {t(`planner.kind.${item.kind}`)}</span>
            <StatusBadges item={item} />
          </div>
        </div>
        <button
          onClick={() => apply({ important: !item.important })}
          title={t("planner.important")}
          className={`mt-1 shrink-0 ${
            item.important ? "text-amber-500" : "text-neutral-300 hover:text-amber-500 dark:text-neutral-500"
          }`}
        >
          <StarIcon on={item.important} />
        </button>
      </div>

      <div className="space-y-3 px-4 py-4">
        <div className="flex gap-2.5">
          <ClockIcon />
          <div className="min-w-0 flex-1">
            {editingTime && !(isEvent && item.all_day) ? (
              <TimeEditor item={item} onDone={() => setEditingTime(false)} onApply={apply} />
            ) : (
              <button
                onClick={() => setEditingTime(true)}
                className={`${FIELD} text-left`}
                disabled={isEvent && item.all_day}
              >
                {timeText}
              </button>
            )}
            {item.time_modified && canvasTime && (
              <p className="mt-0.5 flex items-center gap-2 text-xs text-neutral-400">
                <span className="truncate">Canvas · {canvasTime}</span>
                <button
                  onClick={() => apply({ due_at: null, start_at: null, end_at: null })}
                  className="shrink-0 hover:text-neutral-700 dark:hover:text-neutral-200"
                >
                  {t("planner.restore")}
                </button>
              </p>
            )}
            {itemDue && (
              <p className="mt-0.5 text-xs text-neutral-400">
                {t("planner.dueItem")} · {itemDue}
              </p>
            )}
          </div>
        </div>

        {(isEvent || item.location_name) && (
          <div className="flex gap-2.5">
            <PinIcon />
            <input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              onBlur={commitLocation}
              onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
              placeholder={t("planner.addLocation")}
              className={FIELD}
            />
          </div>
        )}

        <div className="flex gap-2.5">
          <NoteIcon />
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={commitNote}
            placeholder={t("planner.addNote")}
            rows={Math.min(6, Math.max(1, note.split("\n").length))}
            className={`${FIELD} resize-none`}
          />
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-neutral-200 px-4 py-3 dark:border-neutral-700">
        <button
          onClick={() => apply({ completed: !item.completed })}
          disabled={update.isPending}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
            item.completed
              ? "bg-emerald-600 text-white hover:bg-emerald-500"
              : "bg-neutral-900 text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          }`}
        >
          {item.completed ? `✓ ${t("planner.done")}` : t("planner.markDone")}
        </button>
        {item.completed_override !== null && (
          <button
            onClick={() => apply({ completed: null })}
            className="text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            {t("planner.followCanvas")}
          </button>
        )}
        <span className="flex-1" />
        <button
          onClick={() => apply({ hidden: !item.hidden })}
          className="rounded-lg px-2.5 py-1.5 text-xs text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:hover:bg-neutral-700 dark:hover:text-neutral-100"
        >
          {item.hidden ? t("planner.unhide") : t("planner.hide")}
        </button>
        {item.html_url && (
          <a
            href={item.html_url}
            target="_blank"
            rel="noreferrer noopener"
            className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-sky-600 hover:bg-sky-50 dark:text-sky-400 dark:hover:bg-sky-950/40"
          >
            Canvas ↗
          </a>
        )}
      </div>
    </div>
  );
}

/** 改时间。截止类的只有一个时刻，事件是起止两个 */
function TimeEditor({
  item,
  onDone,
  onApply,
}: {
  item: PlannerItem;
  onDone: () => void;
  onApply: (patch: PlannerItemUpdate) => Promise<void>;
}) {
  const { zone } = useI18n();
  const isEvent = item.kind === "event";
  const [start, setStart] = useState(toLocalInput(isEvent ? item.start_at : item.date, zone));
  const [end, setEnd] = useState(toLocalInput(item.end_at, zone));

  const commit = async () => {
    if (isEvent) {
      const s = fromLocalInput(start, zone);
      const e = fromLocalInput(end, zone);
      // 结束早于开始的时候按原时长顺延，别存出一个倒着的事件
      let endIso = e;
      if (s && e && e <= s && item.start_at && item.end_at) {
        const len = new Date(item.end_at).getTime() - new Date(item.start_at).getTime();
        endIso = new Date(new Date(s).getTime() + len).toISOString();
      }
      await onApply({ start_at: s, end_at: endIso });
    } else {
      await onApply({ due_at: fromLocalInput(start, zone) });
    }
    onDone();
  };

  const input =
    "h-8 rounded-md border border-neutral-300 bg-white px-2 text-sm dark:border-neutral-600 dark:bg-neutral-900";
  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") onDone();
      }}
    >
      <input
        type="datetime-local"
        value={start}
        autoFocus
        onChange={(e) => setStart(e.target.value)}
        className={input}
      />
      {isEvent && (
        <>
          <span className="text-neutral-400">→</span>
          <input
            type="datetime-local"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className={input}
          />
        </>
      )}
    </div>
  );
}

// ---------- 自建任务 ----------

type RepeatPreset = "none" | "daily" | "weekdays" | "weekly" | "biweekly" | "monthly" | "yearly";

const REPEAT_PRESETS: Record<Exclude<RepeatPreset, "none">, Repeat> = {
  daily: { freq: "daily" },
  weekdays: { freq: "weekly", weekdays: [0, 1, 2, 3, 4] },
  weekly: { freq: "weekly" },
  biweekly: { freq: "weekly", interval: 2 },
  monthly: { freq: "monthly" },
  yearly: { freq: "yearly" },
};

function presetOf(repeat: Repeat | null): RepeatPreset {
  if (!repeat) return "none";
  if (repeat.freq === "weekly") {
    if (repeat.weekdays?.length === 5 && repeat.weekdays.every((d) => d < 5)) return "weekdays";
    return (repeat.interval ?? 1) === 2 ? "biweekly" : "weekly";
  }
  return repeat.freq;
}

function RepeatIcon() {
  return (
    <svg viewBox="0 0 24 24" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        d="M4 12a8 8 0 0 1 13.7-5.6L20 9M20 4v5h-5M20 12a8 8 0 0 1-13.7 5.6L4 15M4 20v-5h5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function StepsIcon() {
  return (
    <svg viewBox="0 0 24 24" className={ICON} fill="none" stroke="currentColor" strokeWidth="1.8">
      <path
        d="M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const SELECT =
  "h-8 min-w-0 flex-1 rounded-md bg-transparent px-1.5 -mx-1.5 outline-none hover:bg-neutral-100 focus:bg-neutral-100 dark:hover:bg-neutral-700/60 dark:focus:bg-neutral-700/60";

function TaskCard({ item, onDeleted }: { item: PlannerItem; onDeleted?: () => void }) {
  const i18n = useI18n();
  const { t, zone, lang } = i18n;
  const update = useUpdatePlannerItem();
  const remove = useDeleteTask();
  const { data: courses } = useCourses();
  const { data: lists } = useTaskLists();
  const toast = useToast();

  const [title, setTitle] = useState(item.title);
  const [note, setNote] = useState(item.note ?? "");
  const [editingTime, setEditingTime] = useState(false);
  useEffect(() => setTitle(item.title), [item.title]);
  useEffect(() => setNote(item.note ?? ""), [item.note]);

  const apply = async (patch: TaskInput) => {
    try {
      await update.mutateAsync({ item, patch });
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  const day = itemDay(item, zone);
  const timeText = !day
    ? t("planner.noDate")
    : item.all_day
      ? `${longDay(day, lang)} · ${t("planner.allDay")}`
      : `${longDay(day, lang)} ${timeLabel(item.date!, zone)}`;
  const color = item.color ?? "#64748b";

  return (
    <div className="text-sm">
      <div className="flex items-start gap-2.5 px-4 pt-4">
        <button
          onClick={() => apply({ completed: !item.completed })}
          aria-label={t("planner.markDone")}
          className="mt-1.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border-2 text-[10px] text-white"
          style={{ borderColor: color, backgroundColor: item.completed ? color : "transparent" }}
        >
          {item.completed && "✓"}
        </button>
        <div className="min-w-0 flex-1">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => {
              const next = title.trim();
              if (next && next !== item.title) apply({ title: next });
              else setTitle(item.title);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setTitle(item.title);
            }}
            className={`${FIELD} text-base font-semibold ${item.completed ? "line-through opacity-60" : ""}`}
          />
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-neutral-500">
            <span>{t("planner.kind.task")}</span>
            {item.course_code && <span className="truncate">· {item.course_code}</span>}
            {item.list_name && <span className="truncate">· {item.list_name}</span>}
          </div>
        </div>
        <button
          onClick={() => apply({ important: !item.important })}
          title={t("planner.important")}
          className={`mt-1 shrink-0 ${
            item.important
              ? "text-amber-500"
              : "text-neutral-300 hover:text-amber-500 dark:text-neutral-500"
          }`}
        >
          <StarIcon on={item.important} />
        </button>
      </div>

      <div className="space-y-2.5 px-4 py-4">
        <div className="flex gap-2.5">
          <ClockIcon />
          <div className="min-w-0 flex-1">
            {editingTime ? (
              <DueEditor item={item} onDone={() => setEditingTime(false)} onApply={apply} />
            ) : (
              <button onClick={() => setEditingTime(true)} className={`${FIELD} text-left`}>
                {timeText}
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <RepeatIcon />
          <select
            value={presetOf(item.repeat)}
            onChange={(e) => {
              const v = e.target.value as RepeatPreset;
              apply({ repeat: v === "none" ? null : REPEAT_PRESETS[v], tz: zone });
            }}
            className={SELECT}
          >
            {(
              ["none", "daily", "weekdays", "weekly", "biweekly", "monthly", "yearly"] as RepeatPreset[]
            ).map((v) => (
              <option key={v} value={v}>
                {t(`task.repeat.${v}`)}
              </option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2.5">
          <FolderIcon />
          <select
            value={item.course_id ?? ""}
            onChange={(e) => apply({ course_id: e.target.value ? Number(e.target.value) : null })}
            className={SELECT}
          >
            <option value="">{t("task.noCourse")}</option>
            {(courses ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.course_code || c.name}
              </option>
            ))}
          </select>
          <select
            value={item.list_id ?? ""}
            onChange={(e) => apply({ list_id: e.target.value ? Number(e.target.value) : null })}
            className={SELECT}
          >
            <option value="">{t("task.noList")}</option>
            {(lists ?? []).map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </div>

        <div className="flex gap-2.5">
          <StepsIcon />
          <StepsEditor steps={item.steps} onChange={(steps) => apply({ steps })} />
        </div>

        <div className="flex gap-2.5">
          <NoteIcon />
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => {
              if (note.trim() !== (item.note ?? "")) apply({ note: note.trim() || null });
            }}
            placeholder={t("planner.addNote")}
            rows={Math.min(6, Math.max(1, note.split("\n").length))}
            className={`${FIELD} resize-none`}
          />
        </div>
      </div>

      <div className="flex items-center gap-2 border-t border-neutral-200 px-4 py-3 dark:border-neutral-700">
        <button
          onClick={() => apply({ completed: !item.completed })}
          disabled={update.isPending}
          className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
            item.completed
              ? "bg-emerald-600 text-white hover:bg-emerald-500"
              : "bg-neutral-900 text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
          }`}
        >
          {item.completed ? `✓ ${t("planner.done")}` : t("planner.markDone")}
        </button>
        <span className="flex-1" />
        <button
          onClick={async () => {
            await remove.mutateAsync(item.id);
            onDeleted?.();
          }}
          className="rounded-lg px-2.5 py-1.5 text-xs text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
        >
          {t("task.delete")}
        </button>
      </div>
    </div>
  );
}

/** 改截止：全天只选日期，否则选到几点。「全天」是一个开关 */
function DueEditor({
  item,
  onDone,
  onApply,
}: {
  item: PlannerItem;
  onDone: () => void;
  onApply: (patch: TaskInput) => Promise<void>;
}) {
  const { t, zone } = useI18n();
  const [allDay, setAllDay] = useState(item.all_day || !item.date);
  const [day, setDay] = useState(item.all_day_date ?? (item.date ? dayKey(item.date, zone) : ""));
  const [at, setAt] = useState(
    item.all_day || !item.date ? (day ? `${day}T23:59` : "") : toLocalInput(item.date, zone),
  );

  const commit = async () => {
    if (allDay) await onApply({ due_date: day || null, due_at: null });
    else await onApply({ due_at: fromLocalInput(at, zone), due_date: null, tz: zone });
    onDone();
  };

  const input =
    "h-8 rounded-md border border-neutral-300 bg-white px-2 text-sm dark:border-neutral-600 dark:bg-neutral-900";
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {allDay ? (
          <input
            type="date"
            value={day}
            autoFocus
            onChange={(e) => setDay(e.target.value)}
            className={input}
          />
        ) : (
          <input
            type="datetime-local"
            value={at}
            autoFocus
            onChange={(e) => setAt(e.target.value)}
            className={input}
          />
        )}
        <label className="flex items-center gap-1.5 text-xs text-neutral-500">
          <input
            type="checkbox"
            checked={allDay}
            onChange={(e) => {
              setAllDay(e.target.checked);
              if (!e.target.checked && !at) {
                setAt(`${day || todayKey(zone)}T23:59`);
              }
              if (e.target.checked && at) setDay(at.slice(0, 10));
            }}
          />
          {t("planner.allDay")}
        </label>
      </div>
      <div className="flex gap-1.5">
        <button
          onClick={commit}
          className="rounded-md bg-neutral-900 px-2.5 py-1 text-xs text-white dark:bg-white dark:text-neutral-900"
        >
          {t("common.confirm")}
        </button>
        <button
          onClick={async () => {
            await onApply({ due_date: null, due_at: null });
            onDone();
          }}
          className="rounded-md px-2.5 py-1 text-xs text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-700"
        >
          {t("task.clearDate")}
        </button>
        <button
          onClick={onDone}
          className="rounded-md px-2.5 py-1 text-xs text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-700"
        >
          {t("common.cancel")}
        </button>
      </div>
    </div>
  );
}

/** 子步骤。每次改动把整份列表发回去 */
function StepsEditor({
  steps,
  onChange,
}: {
  steps: TaskStep[];
  onChange: (steps: TaskStep[]) => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState("");
  // 本地留一份：连着加两步时，第二步不能基于还没刷新回来的旧列表
  const [local, setLocal] = useState(steps);
  useEffect(() => setLocal(steps), [steps]);
  const change = (next: TaskStep[]) => {
    setLocal(next);
    onChange(next);
  };
  const add = () => {
    const title = draft.trim();
    if (!title) return;
    change([...local, { title, done: false }]);
    setDraft("");
  };
  return (
    <div className="min-w-0 flex-1 space-y-0.5">
      {local.map((s, i) => (
        <div key={s.id ?? `new-${i}`} className="group flex items-center gap-2 py-0.5">
          <input
            type="checkbox"
            checked={s.done}
            onChange={() => change(local.map((x, j) => (j === i ? { ...x, done: !x.done } : x)))}
            className="size-3.5 shrink-0"
          />
          <span className={`min-w-0 flex-1 truncate ${s.done ? "text-neutral-400 line-through" : ""}`}>
            {s.title}
          </span>
          <button
            onClick={() => change(local.filter((_, j) => j !== i))}
            className="shrink-0 px-1 text-neutral-400 opacity-0 hover:text-red-600 group-hover:opacity-100"
            aria-label={t("task.delete")}
          >
            ×
          </button>
        </div>
      ))}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") add();
        }}
        onBlur={add}
        placeholder={t("task.addStep")}
        className={FIELD}
      />
    </div>
  );
}

/** 新建任务的草稿卡片。写了标题才真正创建 —— 点空了关掉，什么都不会留下 */
export function DraftTaskCard({
  defaults,
  onCreated,
}: {
  defaults: TaskInput;
  onCreated: (item: PlannerItem) => void;
}) {
  const i18n = useI18n();
  const { t, zone, lang } = i18n;
  const create = useCreateTask();
  const toast = useToast();
  const [title, setTitle] = useState("");

  const when = defaults.due_date
    ? `${longDay(defaults.due_date, lang)} · ${t("planner.allDay")}`
    : defaults.due_at
      ? `${longDay(dayKey(defaults.due_at, zone), lang)} ${timeLabel(defaults.due_at, zone)}`
      : t("planner.noDate");

  const submit = async () => {
    const value = title.trim();
    if (!value || create.isPending) return;
    try {
      onCreated(await create.mutateAsync({ ...defaults, title: value, tz: zone }));
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  return (
    <div className="space-y-3 p-4 text-sm">
      <input
        value={title}
        autoFocus
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
        placeholder={t("task.newTitle")}
        className={`${FIELD} text-base font-semibold`}
      />
      <div className="flex items-center gap-2.5 text-neutral-500">
        <ClockIcon />
        <span>{when}</span>
      </div>
      <div className="flex justify-end">
        <button
          onClick={submit}
          disabled={!title.trim() || create.isPending}
          className="rounded-lg bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
        >
          {t("task.create")}
        </button>
      </div>
    </div>
  );
}
