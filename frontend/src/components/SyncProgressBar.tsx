import { useI18n } from "@/lib/i18n";
import type { SyncProgress } from "@/types";

/** 进度按课程数而不是百分比 —— 每门课耗时差异极大，百分比会骗人 */
export default function SyncProgressBar({ progress }: { progress: SyncProgress }) {
  const { t } = useI18n();
  if (progress.total === 0) {
    return <p className="text-xs text-neutral-500">{t("sync.preparing")}</p>;
  }
  const pct = (progress.done / progress.total) * 100;
  // 同步是课程级并发的，同时有好几门在跑，所以这里列的是一组
  const running = progress.current_courses ?? [];

  return (
    <div className="space-y-1.5">
      <div className="flex justify-between gap-3 text-xs text-neutral-500">
        <span className="truncate">
          {running.length > 0
            ? t("sync.runningCourses", { courses: running.join(", ") })
            : t("sync.running")}
        </span>
        <span className="shrink-0 tabular-nums">
          {t("sync.courseProgress", { done: progress.done, total: progress.total })}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800">
        <div
          className="h-full rounded-full bg-neutral-900 transition-all duration-500 dark:bg-white"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
