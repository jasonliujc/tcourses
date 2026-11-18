import type { PlannerItem, PlannerKind } from "@/types";

/** 日期一律以 "YYYY-MM-DD" 的字符串在用户所选时区里讨论。
 *
 *  不用 Date 对象做日历运算：Date 只认浏览器本地时区，而用户可以在设置里选别的
 *  时区 —— 那样同一个截止会被画在两个不同的格子里。 */

/** 某时刻在某时区里是哪一天 */
export function dayKey(iso: string, timeZone: string): string {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date(iso));
  } catch {
    return iso.slice(0, 10);
  }
}

export function todayKey(timeZone: string): string {
  return dayKey(new Date().toISOString(), timeZone);
}

/** 某时刻在某时区里是几点几分（24 小时制） */
export function timeLabel(iso: string, timeZone: string): string {
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(new Date(iso));
  } catch {
    return iso.slice(11, 16);
  }
}

/** 某时刻在某时区里是当天的第几分钟。周视图按它摆事件块 */
export function minuteOfDay(iso: string, timeZone: string): number {
  const [h, m] = timeLabel(iso, timeZone).split(":").map(Number);
  return (h % 24) * 60 + m;
}

/** "2026-10-05" → "10/05"。和列表里其他日期同一个写法 */
export function dayLabel(key: string): string {
  return `${key.slice(5, 7)}/${key.slice(8, 10)}`;
}

/** 纯日期运算。用 UTC 正午做中转，不会碰上夏令时那一小时 */
export function addDays(key: string, n: number): string {
  const d = new Date(`${key}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** 周一 = 0 … 周日 = 6。学生的一周从周一开始，课表也是 */
export function weekdayOf(key: string): number {
  return (new Date(`${key}T12:00:00Z`).getUTCDay() + 6) % 7;
}

export function startOfWeekKey(key: string): string {
  return addDays(key, -weekdayOf(key));
}

/** 月视图的格子：从包含 1 号的那一周的周一开始，铺满 6 周 */
export function monthGrid(year: number, month: number): string[] {
  const first = `${year}-${String(month).padStart(2, "0")}-01`;
  const start = startOfWeekKey(first);
  return Array.from({ length: 42 }, (_, i) => addDays(start, i));
}

/** 这一条落在哪一天。全天事件用它自己的日期 —— 全天事件在 Canvas 里存成
 *  "那天零点"的时刻，换到别的时区会滑到前一天 */
export function itemDay(item: PlannerItem, timeZone: string): string | null {
  if (item.all_day && item.all_day_date) return item.all_day_date;
  return item.date ? dayKey(item.date, timeZone) : null;
}

/** 有起止时间的事件（office hours、讲座）才在周视图里画成时间块；
 *  截止类的只是一个时刻，放在顶上那一条 */
export function isTimedBlock(item: PlannerItem): boolean {
  return item.kind === "event" && !item.all_day && !!item.start_at && !!item.end_at;
}

export type Bucket = "past" | "today" | "tomorrow" | "week" | "later" | "undated";

/** 「已过」放最上面，而且逾期和已完成的放在同一组里 ——
 *  分成两组的话，勾掉一条逾期的，它会从顶上跳到最底下 */
export const BUCKET_ORDER: Bucket[] = ["past", "today", "tomorrow", "week", "later", "undated"];

export function bucketOf(item: PlannerItem, timeZone: string, today: string): Bucket {
  const day = itemDay(item, timeZone);
  if (!day) return "undated";
  if (day < today) return "past";
  if (day === today) return "today";
  if (day === addDays(today, 1)) return "tomorrow";
  if (day <= addDays(startOfWeekKey(today), 6)) return "week";
  return "later";
}

/** 过了时间还没完成 */
export function isOverdue(item: PlannerItem, timeZone: string, today: string): boolean {
  const day = itemDay(item, timeZone);
  return !!day && day < today && !item.completed;
}

/** 任务页默认显示的类型：要"做"的东西。事件是去参加的，公告是看的 */
export const TASK_KINDS: PlannerKind[] = [
  "task",
  "assignment",
  "quiz",
  "discussion",
  "page",
  "note",
  "peer_review",
  "other",
];

/** 日历默认显示的类型：要做的 + 事件。公告默认不进日历 —— 它没有"截止"可言 */
export const CALENDAR_KINDS: PlannerKind[] = [...TASK_KINDS, "event"];

export const ALL_KINDS: PlannerKind[] = [...CALENDAR_KINDS, "announcement"];

/** 把 #rrggbb 换成带透明度的 rgba，事件块的底色用 */
export function tint(hex: string | null | undefined, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex ?? "");
  if (!m) return `rgba(115, 115, 115, ${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/** 视图和筛选条件记在本机。读写都可能抛（隐私模式、禁用存储），抛了就当没存过 */
export function loadPref<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(`tcourses:${key}`);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function savePref<T>(key: string, value: T): void {
  try {
    localStorage.setItem(`tcourses:${key}`, JSON.stringify(value));
  } catch {
    // 存不了就不存
  }
}

/** 存筛选时顺手记下当时有哪些类别。之后新加的类别（比如「我的任务」）
 *  不在记录里，读出来时默认勾上：用户没机会关掉它，不能当成关掉了 */
export function loadFilter<T extends { kinds: PlannerKind[] }>(
  key: string,
  defaults: T,
  options: PlannerKind[],
): T {
  const { known, ...saved } = loadPref<Partial<T> & { known?: PlannerKind[] }>(key, {});
  const merged = { ...defaults, ...saved } as T;
  // 没有记录的是第二期以前存的，那时还没有「我的任务」
  const seen = known ?? options.filter((k) => k !== "task");
  const fresh = options.filter((k) => !seen.includes(k));
  if (fresh.length) {
    merged.kinds = options.filter((k) => merged.kinds.includes(k) || fresh.includes(k));
  }
  return merged;
}

export function saveFilter<T>(key: string, value: T, options: PlannerKind[]): void {
  savePref(key, { ...value, known: options });
}

/** "2026-10-01" → "10月1日 周四" / "Thu, Oct 1" */
export function longDay(key: string, lang: "zh" | "en"): string {
  return new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-US", {
    timeZone: "UTC",
    month: lang === "zh" ? "long" : "short",
    day: "numeric",
    weekday: "short",
  }).format(new Date(`${key}T12:00:00Z`));
}

/** ISO 时刻 → datetime-local 输入框要的墙上时间（按所选时区） */
export function toLocalInput(iso: string | null, timeZone: string): string {
  if (!iso) return "";
  return `${dayKey(iso, timeZone)}T${timeLabel(iso, timeZone)}`;
}

/** datetime-local 的值（所选时区里的墙上时间）→ ISO 时刻 */
export function fromLocalInput(value: string, timeZone: string): string | null {
  if (!value) return null;
  const guess = new Date(`${value}:00Z`);
  const wall = toLocalInput(guess.toISOString(), timeZone);
  const offset = new Date(`${wall}:00Z`).getTime() - guess.getTime();
  return new Date(guess.getTime() - offset).toISOString();
}
