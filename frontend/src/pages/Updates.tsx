import { useEvents, useMarkAllRead, useMarkRead, useSyncStatus } from "@/api/hooks";
import SyncProgressBar from "@/components/SyncProgressBar";
import { eventLabel, eventSummary } from "@/lib/events";
import { useI18n } from "@/lib/i18n";

export default function Updates() {
  const { data: events, isLoading } = useEvents();
  const { data: sync } = useSyncStatus();
  const markRead = useMarkRead();
  const markAllRead = useMarkAllRead();
  const i18n = useI18n();
  const { t } = i18n;

  return (
    <div className="mx-auto max-w-5xl space-y-10">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">{t("updates.title")}</h1>
        <button
          onClick={() => markAllRead.mutate()}
          className="text-sm text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
        >
          {t("updates.markAllRead")}
        </button>
      </header>

      {/* 首轮同步要跑很久，这里也放一条进度，省得用户以为卡住了 */}
      {sync?.is_running && (
        <div className="rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
          <SyncProgressBar progress={sync.progress} />
        </div>
      )}

      <section className="space-y-2">
        {isLoading && <p className="text-sm text-neutral-400">{t("common.loading")}</p>}
        {events?.length === 0 && (
          <p className="rounded-lg border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-400 dark:border-neutral-700">
            {t("updates.empty")}
          </p>
        )}
        {/* 整条就是按钮：点一下即已读，不再单独放一个「标记已读」链接 */}
        {events?.map((e) => (
          <button
            key={e.id}
            onClick={() => !e.is_read && markRead.mutate(e.id)}
            disabled={e.is_read}
            className={`flex w-full items-start gap-4 rounded-lg border p-4 text-left transition ${
              e.is_read
                ? "cursor-default border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
                : "cursor-pointer border-blue-200 bg-blue-50/60 hover:border-blue-300 hover:bg-blue-50 dark:border-blue-900 dark:bg-blue-950/30 dark:hover:bg-blue-950/50"
            }`}
          >
            <span className="shrink-0 rounded-md bg-neutral-100 px-2 py-1 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
              {eventLabel(e.kind, i18n)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{e.title}</p>
              <p className="text-sm text-neutral-500">{eventSummary(e, i18n)}</p>
            </div>
            <div className="shrink-0 text-right text-xs text-neutral-400">
              <div>{i18n.formatDate(e.created_at)}</div>
              {!e.is_read && <div className="mt-1 text-blue-500">{t("updates.unread")}</div>}
            </div>
          </button>
        ))}
      </section>
    </div>
  );
}
