import type { I18n } from "@/lib/i18n";
import type { NotificationEvent } from "@/types";

/** 提醒的类型标签（列表左边那个小块）。认不出的类型直接显示原始 kind ——
 *  后端以后加了新类型，界面不会变成一个空标签 */
export function eventLabel(kind: string, i: I18n): string {
  return i.tOr(`event.${kind}`, kind);
}

/** 每种事件的固定说法。`updated_*` 那几种不在这里 —— 它们要说清"变的是哪几项"，
 *  走下面 changed_fields 那条路 */
const PHRASE: Record<string, string> = {
  new_announcement: "event.summary.new_announcement",
  updated_page: "event.summary.new_page",
  new_file: "event.summary.new_file",
  updated_file: "event.summary.updated_file",
  new_link: "event.summary.new_link",
  updated_link: "event.summary.updated_link",
};

/** 提醒的摘要。
 *
 *  **现拼，不用后端存的那句话。** 后端写事件时把摘要拼成中文存进了库，
 *  切成英文界面之后那些历史提醒会永远停在写入时的语言 —— 而"提醒"恰恰是
 *  一个越旧越要往回翻的列表。所以后端只存 kind 和 payload，话在这里说。
 *
 *  实在认不出的（以后新增的类型、或者老库里没有 payload 的行）回落到
 *  后端那句话，总比空白强。
 */
export function eventSummary(e: NotificationEvent, i: I18n): string {
  const course = e.course_name || "Canvas";
  const payload = (e.payload ?? {}) as {
    changed_fields?: string[];
    due_at?: string;
    author?: string;
  };

  // 改动型事件：不管是作业、公告还是页面，要回答的都是"哪几项变了"
  if (payload.changed_fields?.length) {
    const fields = payload.changed_fields
      // 老数据里存的是中文字面量（"标题"），新数据存的是字段名 —— 认得出就翻，
      // 认不出就原样显示
      .map((f) => i.tOr(`event.field.${f}`, f))
      .join(i.lang === "zh" ? "、" : ", ");
    return `${course} · ${i.t("event.summary.updated", { fields })}`;
  }

  if (e.kind === "new_message") {
    return i.t("event.summary.new_message", { author: payload.author || i.t("event.someone") });
  }

  if (e.kind === "new_assignment") {
    const due = payload.due_at ? i.formatDate(payload.due_at) : i.t("event.noDue");
    return `${course} · ${i.t("event.summary.new_assignment", { due })}`;
  }

  const phrase = PHRASE[e.kind];
  if (phrase) return `${course} · ${i.tOr(phrase, e.summary ?? "")}`;

  return e.summary ?? "";
}
