import { ApiError } from "@/lib/api";
import type { I18n, Params } from "@/lib/i18n";

/** 把后端的报错翻成界面语言。
 *
 *  后端返回的是 `{code, params}` 而不是一句话 —— 一句话没法跟着界面语言变。
 *  认不出的 code 就把原文显示出来：后端以后加了新的错误码，用户至少看得到
 *  出了什么事，而不是一句"操作失败"。 */
export function apiMessage(err: unknown, i: I18n): string {
  if (err instanceof ApiError && err.code) {
    return i.tOr(`err.${err.code}`, err.message, err.params as Params);
  }
  if (err instanceof Error && err.message) return err.message;
  return i.t("err.generic");
}
