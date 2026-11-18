import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useResourceSummary, useRunSync, useSyncActive, useSyncStatus } from "@/api/hooks";
import Spinner from "@/components/Spinner";
import SyncProgressBar from "@/components/SyncProgressBar";
import { useI18n } from "@/lib/i18n";

/** 全局资源页的外壳：标题 + 两个入口，内容由子路由填。
 *
 *  **为什么是「主页」和「查找资源」两栏，而不是直接给一个大列表。**
 *  打开这个页面的人想知道的是"有什么新东西"和"我收的那几份在哪" —— 那是主页
 *  那两个框。按课程翻资料是课程页的事，只有明确要找什么的时候才需要那套
 *  带侧边栏的列表，所以它退到第二个入口。
 *
 *  「全部星标」是主页那个框的**二级页面**（`/resources/starred`），不是弹窗：
 *  进去之后要能搜索、筛选、翻页，那是一个页面该干的事；而弹窗关掉就回不去了。
 *
 *  表头和课程页是同一套：左边标题 + 一行"共多少份"，右边上次同步时间 + 同步按钮。
 *  两个页面的同一个位置必须是同一件事，否则每换一页都要重新找一遍。
 */
export default function Resources() {
  const { t, formatDate } = useI18n();
  const { pathname } = useLocation();
  const { data: summary } = useResourceSummary();
  const { data: status } = useSyncStatus();
  const anySyncing = useSyncActive();
  const runSync = useRunSync();
  // 二级页面（全部星标）也归在「主页」这一栏下：它是从主页那个框点进去的
  const onFind = pathname.startsWith("/resources/find");
  const syncingAll = status?.is_running ?? false;

  return (
    <section className="mx-auto max-w-6xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("resources.title")}</h1>
          <p className="text-sm text-neutral-500">
            {t("course.resourceCount", { n: summary?.total ?? 0 })}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* 上次同步时间放在按钮左边 —— 决定要不要点同步的第一依据，
              和课程页那一排是同一个顺序 */}
          {status?.last_full_sync_at && (
            <span className="whitespace-nowrap text-xs text-neutral-400">
              {t("files.lastSync", { when: formatDate(status.last_full_sync_at) })}
            </span>
          )}
          <button
            onClick={() => runSync.mutate()}
            disabled={!status?.canvas_configured || anySyncing}
            title={t("settings.syncNow")}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-60 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {anySyncing && <Spinner />}
            {syncingAll ? t("settings.syncing") : t("settings.syncAll")}
          </button>
        </div>
      </header>

      {/* 同步跑起来之后就地给进度，不用切到设置页去看 */}
      {syncingAll && status && <SyncProgressBar progress={status.progress} />}

      {/* 和课程页的一级/二级切换用同一套样式 —— 两个页面的"换一堆看"
          必须长得一样，否则用户得重新学一次 */}
      <nav className="flex gap-1 border-b border-neutral-200 dark:border-neutral-800">
        <Tab to="/resources" active={!onFind}>
          {t("resources.tabHome")}
        </Tab>
        <Tab to="/resources/find" active={onFind}>
          {t("resources.tabFind")}
        </Tab>
      </nav>

      <Outlet />
    </section>
  );
}

function Tab({
  to,
  active,
  children,
}: {
  to: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <NavLink
      to={to}
      end
      className={`-mb-px border-b-2 px-4 pb-2 text-sm font-medium transition ${
        active
          ? "border-neutral-900 text-neutral-900 dark:border-white dark:text-white"
          : "border-transparent text-neutral-500 hover:text-neutral-800 dark:hover:text-neutral-200"
      }`}
    >
      {children}
    </NavLink>
  );
}
