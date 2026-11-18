/** 纯粹的格式化：只依赖"用哪个语言、哪个时区"，不认识任何界面文案。
 *
 *  带文案的那几个（事件类型、同步间隔、相对时间）在 `lib/i18n.tsx` 里，
 *  因为它们要查词典。这里留下的都是把一个值排成一串字符的活儿。
 */

export type Lang = "zh" | "en";

/** 所有时间格式化的两个输入。`timeZone` 为空 = 跟随设备 */
export interface Fmt {
  lang: Lang;
  timeZone?: string;
}

/** 英文用 en-CA（YYYY-MM-DD / MM-DD）而不是 en-US：
 *  09/05 在北美读作 9 月 5 日、在别处读作 5 月 9 日，而课程资料的日期是要拿来
 *  判断"哪份是最新的"的，读错一次就白显示了。宽度也和中文那套一致，列表不会抖。 */
const LOCALE: Record<Lang, string> = { zh: "zh-CN", en: "en-CA" };

/** 在指定时区下这个时刻属于哪一年 —— 跨年那几个小时里设备时区和所选时区
 *  可能给出不同的年份，两边必须用同一把尺子量 */
function yearIn(date: Date, f: Fmt): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: f.timeZone, year: "numeric" }).format(date);
}

/** 日期和时间分开返回，方便让日期更显眼、时间弱化。
 *  非今年的一定带上年份 —— 不然 "09-27" 到底是今年还是去年会造成误判。 */
export function dateParts(
  value: string | null | undefined,
  f: Fmt,
): { date: string; time: string } {
  if (!value) return { date: "—", time: "" };
  const d = new Date(value);
  const sameYear = yearIn(d, f) === yearIn(new Date(), f);
  const date = d.toLocaleDateString(LOCALE[f.lang], {
    timeZone: f.timeZone,
    ...(sameYear ? {} : { year: "numeric" }),
    month: "2-digit",
    day: "2-digit",
  });
  // 两种语言都用 24 小时制：12 小时制多出来的 AM/PM 会把列表右侧撑宽
  const time = d.toLocaleTimeString(LOCALE[f.lang], {
    timeZone: f.timeZone,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return { date, time };
}

export function formatDate(value: string | null | undefined, f: Fmt): string {
  const { date, time } = dateParts(value, f);
  return time ? `${date} ${time}` : date;
}

/** 单位是国际通用的，两种语言下一样 */
export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** 设备自己的时区，例如 "America/Toronto"。自动时区就是用它 */
export function deviceTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** 某个时区此刻的偏移，例如 "UTC-04:00" —— 光看 IANA 名字说不出差几个小时 */
export function utcOffsetLabel(timeZone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      timeZoneName: "longOffset",
    }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

/** 浏览器知道的全部 IANA 时区。
 *
 *  用 `Intl.supportedValuesOf` 而不是自己维护一份清单：时区规则每年都在变，
 *  自带的那份跟着浏览器更新，我们抄一份下来就等着过期。老浏览器上退回一份
 *  够用的短清单，至少不会是个空下拉框。 */
export function allTimeZones(): string[] {
  try {
    const supported = (
      Intl as unknown as { supportedValuesOf?: (k: string) => string[] }
    ).supportedValuesOf?.("timeZone");
    if (supported?.length) return supported;
  } catch {
    /* 下面走兜底 */
  }
  return [
    "UTC",
    "America/Toronto",
    "America/Vancouver",
    "America/New_York",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Paris",
    "Asia/Shanghai",
    "Asia/Hong_Kong",
    "Asia/Tokyo",
    "Australia/Sydney",
  ];
}
