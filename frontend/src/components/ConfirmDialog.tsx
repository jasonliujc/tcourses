/** 全局统一的确认弹框。**所有需要确认的操作都走这里。**
 *
 *  样式是写死的、不给调：标题一律 `动作「对象」？`，确认按钮一律红色。
 *  之前留了个 `danger` 开关，结果同一个组件在设置页是深色按钮、在分组那边是
 *  红色，看着像两个不同的东西。而会弹这个框的操作本来就都有后果 ——
 *  没后果的事根本不该拦一道 —— 所以不需要"温和版"。
 *
 *  `body` 可选：多数情况标题本身已经说清楚了，硬凑一段解释反而是噪声。
 *  `confirmLabel` 可以换成具体动词（删除分组 / 移出），那是在说**做什么**，
 *  和"长什么样"是两回事。 */
import { useI18n } from "@/lib/i18n";

export default function ConfirmDialog({
  title,
  body,
  confirmLabel,
  pending = false,
  onCancel,
  onConfirm,
}: {
  title: string;
  body?: string;
  confirmLabel?: string;
  pending?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-md space-y-5 rounded-xl border border-neutral-200 bg-white p-6 shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-lg font-semibold">{title}</h3>
        {body && (
          <p className="text-sm leading-relaxed text-neutral-600 dark:text-neutral-300">{body}</p>
        )}
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={pending}
            className="rounded-lg border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={onConfirm}
            disabled={pending}
            className="rounded-lg bg-red-600 px-4 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50"
          >
            {pending ? t("common.processing") : (confirmLabel ?? t("common.confirm"))}
          </button>
        </div>
      </div>
    </div>
  );
}
