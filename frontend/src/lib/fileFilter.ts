import type { Medium } from "@/types";

/** 一套筛选条件。
 *
 *  和"当前在看哪一堆"（侧边栏选中的节点、我的分组）是两回事：那个决定列表从哪儿
 *  取，这个在取到的那一堆上再切一刀。所以两者可以叠加，也各自记忆。
 *
 *  `courseIds` 现在恒为空 —— 它是给后面的全局文件管理留的位置。留在类型里而不是
 *  以后再加，是因为筛选面板要能原样搬到那个页面去用，只多显示一节。
 */
export interface FileFilter {
  /** 载体。**空 = 文件和链接都要**，不是"都不要" —— 面板上两个都亮着就是这个状态 */
  media: Medium[];
  /** 板块 id（侧边栏那套：syllabus / modules_pages / …） */
  boards: string[];
  /** 板块下的容器名（Week 3 / Lectures / …） */
  containers: string[];
  /** 资源类型 key（pdf / docs / link_video / …） */
  kinds: string[];
  /** YYYY-MM-DD，空 = 不限。按**所选时区**理解成那一天的起止 */
  dateFrom: string;
  dateTo: string;
  /** 预留给全局文件管理，课程页里恒为空 */
  courseIds: number[];
}

export const EMPTY_FILTER: FileFilter = {
  media: [],
  boards: [],
  containers: [],
  kinds: [],
  dateFrom: "",
  dateTo: "",
  courseIds: [],
};

/** 生效了几条。给按钮上那个角标用 —— 只说"有筛选"不够，用户想知道有几条 */
export function activeCount(f: FileFilter): number {
  return (
    (f.media.length === 1 ? 1 : 0) +
    f.boards.length +
    f.containers.length +
    f.kinds.length +
    (f.dateFrom ? 1 : 0) +
    (f.dateTo ? 1 : 0) +
    f.courseIds.length
  );
}

export const isActive = (f: FileFilter) => activeCount(f) > 0;

/** 两套条件是不是一样。用来判断"应用"按钮该不该亮 */
export function sameFilter(a: FileFilter, b: FileFilter): boolean {
  const key = (f: FileFilter) =>
    JSON.stringify([
      [...f.media].sort(),
      [...f.boards].sort(),
      [...f.containers].sort(),
      [...f.kinds].sort(),
      f.dateFrom,
      f.dateTo,
      [...f.courseIds].sort(),
    ]);
  return key(a) === key(b);
}

/** 多选轴上的加减 */
export function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** 某个时区在某个时刻的 UTC 偏移（分钟）。
 *
 *  没有第三方库的做法：把那个时刻在目标时区里的"墙上时间"读出来，再当成 UTC
 *  反算一次，两者之差就是偏移。 */
function offsetMinutes(timeZone: string, at: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(at);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  const asUTC = Date.UTC(
    get("year"),
    get("month") - 1,
    get("day"),
    get("hour") % 24,
    get("minute"),
    get("second"),
  );
  return (asUTC - at.getTime()) / 60_000;
}

/** "2026-03-10" + 时区 → 那一天在该时区里的起点/终点对应的 UTC 时刻。
 *
 *  必须做这一步换算：用户选的是"3 月 10 日"，而库里存的是时刻。直接把
 *  `2026-03-10T00:00:00Z` 发过去，在 UTC+8 的人看来会漏掉当天早上的八个小时。 */
function dayBoundary(day: string, timeZone: string, end: boolean): string | undefined {
  if (!day) return undefined;
  const wall = `${day}T${end ? "23:59:59.999" : "00:00:00.000"}Z`;
  const guess = new Date(wall);
  if (Number.isNaN(guess.getTime())) return undefined;
  try {
    const offset = offsetMinutes(timeZone, guess);
    return new Date(guess.getTime() - offset * 60_000).toISOString();
  } catch {
    // 时区名不认识：退回按 UTC 理解，总比整个筛选失效强
    return guess.toISOString();
  }
}

/** 接口认的那几个参数。**列表、来源树、自动分组、用户分组四个接口都收它** ——
 *  筛完之后侧边栏的分组和计数必须跟着变，否则"Lectures 28"点进去是空的，
 *  那个 28 就成了假数字 */
export interface FilterQuery {
  medium?: Medium;
  board: string[];
  container: string[];
  kind: string[];
  date_from?: string;
  date_to?: string;
  /** 课程轴。参数名是 `course` 而不是 `course_id`：后者和课程页路由里的
   *  路径参数同名，会让 FastAPI 把路径参数当成查询参数 */
  course: number[];
}

/** 条件 → 接口参数。日期在这里从"某一天"变成"某个时刻" */
export function toQuery(f: FileFilter, timeZone: string): FilterQuery {
  return {
    // 两个都选等于没筛，别往 URL 上挂一个恒真的条件
    medium: f.media.length === 1 ? f.media[0] : undefined,
    board: f.boards,
    container: f.containers,
    kind: f.kinds,
    date_from: dayBoundary(f.dateFrom, timeZone, false),
    date_to: dayBoundary(f.dateTo, timeZone, true),
    course: f.courseIds,
  };
}

/** 那一天在某个时区里的起点，ISO 时刻。全局页的「今天 / 本周」靠它 ——
 *  边界必须由前端算：它取决于用户选的时区，后端猜一个只会在跨日的那几个
 *  小时里给出和列表对不上的数字。 */
export function startOfToday(timeZone: string): string | undefined {
  return dayBoundary(today(timeZone), timeZone, false);
}

/** 本周起点（周一）。学生的一周是从周一算的，课表也是 */
export function startOfWeek(timeZone: string): string | undefined {
  const day = today(timeZone);
  const noon = new Date(`${day}T12:00:00Z`);
  // getUTCDay: 0 = 周日。周日要回退 6 天，其余回退到本周一
  const back = (noon.getUTCDay() + 6) % 7;
  noon.setUTCDate(noon.getUTCDate() - back);
  return dayBoundary(noon.toISOString().slice(0, 10), timeZone, false);
}

/** 某个时区里的"今天"是哪一天（YYYY-MM-DD） */
function today(timeZone: string): string {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}
