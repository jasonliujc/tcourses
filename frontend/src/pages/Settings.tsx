import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  useCheckCanvasConnection,
  useCourseChoices,
  useCourses,
  useCourseSyncStatus,
  useCreateTaskList,
  useDeleteTaskList,
  usePlannerStatus,
  useSetCourseColor,
  useRunSync,
  useSaveCanvasConnection,
  useSaveCourseSelection,
  useSettings,
  useRefreshOnSyncComplete,
  useSyncCourse,
  useSyncStatus,
  useTaskLists,
  useUpdateSettings,
  useUpdateTaskList,
} from "@/api/hooks";
import { ConcludedTag } from "@/App";
import ConfirmDialog from "@/components/ConfirmDialog";
import Panel from "@/components/Panel";
import Spinner from "@/components/Spinner";
import SyncProgressBar from "@/components/SyncProgressBar";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import { allTimeZones, deviceTimeZone, utcOffsetLabel } from "@/lib/format";
import { useI18n, type Lang } from "@/lib/i18n";
import type {
  AppSettings,
  CanvasConnectionCheck,
  Course,
  CourseChoice,
  CourseSyncStatus,
  PlannerSyncState,
  ResultNote,
  TaskList,
} from "@/types";

type ToggleKey =
  | "auto_download_files"
  | "sync_inline_images"
  | "sync_links"
  | "sync_announcement_links"
  | "sync_discussions";

/** 这几个开关的行为完全一致：改动触发一次「同步所有课程」，关闭时删掉对应的数据。
 *  所以每一个都要确认，走全局统一的 ConfirmDialog。 */
const TOGGLE_KEYS: ToggleKey[] = [
  "auto_download_files",
  "sync_inline_images",
  "sync_links",
  "sync_announcement_links",
  "sync_discussions",
];

interface PendingChange {
  key: ToggleKey;
  value: boolean;
  title: string;
}

export default function Settings() {
  const { data: status } = useSyncStatus();
  const { data: settings } = useSettings();
  // 有任何同步在跑（全量或单课）时快速刷新课程状态，跑完的那一刻再刷一次
  const anySyncing = useRefreshOnSyncComplete();
  const { data: courses } = useCourseSyncStatus(anySyncing);
  const runSync = useRunSync();
  const syncCourse = useSyncCourse();
  const updateSettings = useUpdateSettings();
  const { data: plannerStatus } = usePlannerStatus();
  const { data: allCourses } = useCourses(true);

  const [pending, setPending] = useState<PendingChange | null>(null);
  // 存 key 不存句子：换语言之后这块结果也要跟着变
  const [result, setResult] = useState<ResultNote[] | null>(null);
  const [picking, setPicking] = useState(false);
  const toast = useToast();
  const i18n = useI18n();
  const { t, formatDate, formatInterval, formatRelativeFuture } = i18n;
  const { data: lists } = useTaskLists();

  // 任何同步在跑都锁死设置：取消勾选课程会当场删数据，
  // 而同步可能正在往那些课里写。单课同步也算。
  const syncingAll = status?.is_running ?? false;
  const locked = anySyncing || updateSettings.isPending;

  const askToggle = (key: ToggleKey, value: boolean) => {
    setPending({
      key,
      value,
      title: t("toggle.ask", {
        action: t(value ? "common.on" : "common.off"),
        name: t(`toggle.${key}`),
      }),
    });
  };

  const confirmToggle = async () => {
    if (!pending) return;
    const res = await updateSettings.mutateAsync({ [pending.key]: pending.value });
    setResult(res.notes.length > 0 ? res.notes : [{ key: "saved" }]);
    setPending(null);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>

      <GeneralPanel />

      {/* ---------- 同步 ---------- */}
      <Panel title={t("settings.sectionSync")}>
        <CanvasConnection settings={settings} locked={locked} />

        <div className="space-y-3 border-t border-neutral-200 p-5 dark:border-neutral-800">
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => runSync.mutate()}
              disabled={!status?.canvas_configured || anySyncing}
              className="flex items-center gap-2 rounded-lg bg-neutral-900 px-4 py-1.5 text-sm text-white disabled:opacity-40 dark:bg-white dark:text-neutral-900"
            >
              {anySyncing && <Spinner />}
              {syncingAll
                ? t("settings.syncing")
                : anySyncing
                  ? t("settings.waitingCourse")
                  : t("settings.syncNow")}
            </button>
            {status?.last_full_sync_at && (
              <span className="whitespace-nowrap text-xs text-neutral-400">
                {t("files.lastSync", { when: formatDate(status.last_full_sync_at) })}
              </span>
            )}
          </div>
          {syncingAll && status && <SyncProgressBar progress={status.progress} />}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-neutral-200 p-5 dark:border-neutral-800">
          <p className="text-sm font-medium">{t("settings.intervalLabel")}</p>
          <div className="flex items-center gap-3">
            <span className="whitespace-nowrap text-xs text-neutral-400">
              {syncingAll ? (
                <span className="text-blue-600">{t("sync.running")}</span>
              ) : status?.interval_minutes === 0 ? (
                t("settings.intervalOff")
              ) : (
                t("sync.next", { when: formatRelativeFuture(status?.next_run_at) })
              )}
            </span>
            <select
              value={status?.interval_minutes ?? 15}
              onChange={(e) =>
                updateSettings.mutate({ sync_interval_minutes: Number(e.target.value) })
              }
              disabled={locked}
              className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-900"
            >
              {(status?.interval_choices ?? [15]).map((m) => (
                <option key={m} value={m}>
                  {formatInterval(m)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-neutral-200 p-5 dark:border-neutral-800">
          <p className="text-sm font-medium">{t("settings.pickCourses")}</p>
          <div className="flex items-center gap-3">
            <span className="whitespace-nowrap text-xs text-neutral-400">
              {t("settings.pickedCount", {
                n: settings?.selected_course_count ?? 0,
                total: settings?.total_course_count ?? 0,
              })}
            </span>
            <button
              onClick={() => setPicking(true)}
              disabled={locked}
              className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              {t("settings.pickCoursesButton")}
            </button>
          </div>
        </div>

        <SyncSourceRow
          label={t("settings.syncTasks")}
          state={plannerStatus?.items}
          syncing={syncingAll}
        />
        <SyncSourceRow
          label={t("settings.syncEvents")}
          state={plannerStatus?.events}
          syncing={syncingAll}
        />

        <div className="border-t border-neutral-200 dark:border-neutral-800">
          <p className="px-5 pb-1 pt-4 text-xs font-medium uppercase tracking-wide text-neutral-400">
            {t("settings.courseStatus")}
          </p>
          {courses?.length === 0 && (
            <p className="px-5 pb-4 text-sm text-neutral-400">{t("settings.noCourses")}</p>
          )}
          {courses?.map((c) => (
            <CourseRow
              key={c.course_id}
              course={c}
              locked={syncingAll}
              syncing={(status?.syncing_course_ids ?? []).includes(c.course_id)}
              onSync={() => syncCourse.mutate(c.course_id)}
            />
          ))}
        </div>
      </Panel>

      {/* ---------- 任务 ---------- */}
      <Panel title={t("settings.sectionTasks")}>
        <Toggle
          label={t("settings.plannerWriteback")}
          checked={settings?.planner_writeback ?? false}
          disabled={locked}
          onChange={(v) => updateSettings.mutate({ planner_writeback: v })}
          first
        />
        <Link
          to="/settings/colors"
          className="flex items-center justify-between gap-4 border-t border-neutral-200 p-5 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/40"
        >
          <span className="text-sm font-medium">{t("settings.courseColors")}</span>
          <span className="flex items-center gap-2">
            <span className="flex -space-x-1">
              {(allCourses ?? []).slice(0, 6).map((c) => (
                <span
                  key={c.id}
                  className="size-3 rounded-full ring-2 ring-white dark:ring-neutral-900"
                  style={{ backgroundColor: c.color ?? "#a3a3a3" }}
                />
              ))}
            </span>
            <span className="text-neutral-400">›</span>
          </span>
        </Link>
        <Link
          to="/settings/lists"
          className="flex items-center justify-between gap-4 border-t border-neutral-200 p-5 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/40"
        >
          <span className="text-sm font-medium">{t("settings.lists")}</span>
          <span className="flex items-center gap-2 text-sm text-neutral-500">
            {lists?.length ? lists.length : null}
            <span className="text-neutral-400">›</span>
          </span>
        </Link>
      </Panel>

      {/* ---------- 文件 ---------- */}
      <Panel title={t("settings.sectionFiles")}>
        {TOGGLE_KEYS.map((key, i) => (
          <Toggle
            key={key}
            label={t(`toggle.${key}`)}
            checked={settings?.[key] ?? false}
            disabled={locked}
            onChange={(v) => askToggle(key, v)}
            first={i === 0}
          />
        ))}
      </Panel>

      {result && (
        <div className="flex items-start justify-between gap-4 rounded-xl border border-neutral-200 bg-neutral-50 p-4 text-sm dark:border-neutral-800 dark:bg-neutral-800/40">
          <ul className="space-y-1">
            {result.map((n) => (
              <li key={n.key}>{i18n.tOr(`note.${n.key}`, n.key, n.params)}</li>
            ))}
          </ul>
          <button onClick={() => setResult(null)} className="shrink-0 text-xs text-neutral-400">
            {t("common.gotIt")}
          </button>
        </div>
      )}

      {pending && (
        <ConfirmDialog
          title={pending.title}
          pending={updateSettings.isPending}
          onCancel={() => setPending(null)}
          onConfirm={confirmToggle}
        />
      )}

      {picking && (
        <CoursePicker
          onClose={() => setPicking(false)}
          onSaved={() => {
            toast(t("settings.savedShort"));
            setPicking(false);
          }}
        />
      )}
    </div>
  );
}

/** 通用设置：语言和时区。
 *
 *  这两项只改**显示**，不改同步抓什么、怎么比对（同步内部一律用带时区的 UTC
 *  比较），所以同步在跑的时候也能随便改，不上锁。 */
function GeneralPanel() {
  const { t, lang, setLang, autoTimezone, setAutoTimezone, timezone, setTimezone, zone } =
    useI18n();

  return (
    <Panel title={t("settings.general")}>
      <div className="flex flex-wrap items-center justify-between gap-3 p-5">
        <p className="text-sm font-medium">{t("settings.language")}</p>
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value as Lang)}
          className="rounded-lg border border-neutral-300 px-3 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        >
          {/* 语言名一律用它自己的说法：正在找英文的人未必读得懂"英语" */}
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
      </div>

      <label
        className={`flex cursor-pointer items-center justify-between gap-4 border-t border-neutral-200 p-5 dark:border-neutral-800`}
      >
        <span className="min-w-0">
          <span className="block text-sm font-medium">{t("settings.autoTimezone")}</span>
          {autoTimezone && (
            <span className="mt-0.5 block text-xs text-neutral-400">
              {t("settings.autoTimezoneHint", { tz: zoneLabel(deviceTimeZone()) })}
            </span>
          )}
        </span>
        <input
          type="checkbox"
          checked={autoTimezone}
          onChange={(e) => setAutoTimezone(e.target.checked)}
          className="size-4 shrink-0"
        />
      </label>

      {/* 关掉自动才出现手动选择 —— 开着的时候摆一个改不动的下拉只会让人来点它 */}
      {!autoTimezone && (
        <TimezonePicker value={timezone} onChange={setTimezone} effective={zone} />
      )}
    </Panel>
  );
}

/** "America/Toronto (UTC-04:00)" —— 光有 IANA 名字说不出差几个小时 */
function zoneLabel(zone: string): string {
  const offset = utcOffsetLabel(zone);
  return offset ? `${zone} (${offset})` : zone;
}

function TimezonePicker({
  value,
  onChange,
  effective,
}: {
  value: string;
  onChange: (tz: string) => void;
  effective: string;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const zones = useMemo(() => allTimeZones(), []);
  const matched = useMemo(() => {
    const q = query.trim().toLowerCase();
    // 选中的那个永远留在列表里，否则一搜索它就消失，select 会跳到别的值上
    const hit = zones.filter((z) => !q || z.toLowerCase().includes(q));
    return hit.includes(value) ? hit : [value, ...hit];
  }, [zones, query, value]);

  return (
    <div className="space-y-2 border-t border-neutral-200 p-5 dark:border-neutral-800">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm font-medium">{t("settings.timezone")}</p>
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("settings.timezoneSearch")}
            className="h-8 w-40 rounded-lg border border-neutral-300 px-2 text-sm outline-none focus:border-neutral-500 dark:border-neutral-700 dark:bg-neutral-900"
          />
          <select
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="h-8 max-w-56 rounded-lg border border-neutral-300 px-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
          >
            {matched.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p className="text-xs text-neutral-400">
        {matched.length === 0 ? t("settings.timezoneNoMatch") : zoneLabel(effective)}
      </p>
    </div>
  );
}

/** 选择要同步哪些课程。
 *
 *  勾选状态只在本地攒着，点「保存」才提交 —— 中途每勾一下就删一门课的数据，
 *  用户改主意的成本就太高了。点「取消」直接丢弃。 */
function CoursePicker({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { data: choices, isLoading } = useCourseChoices(true);
  const save = useSaveCourseSelection();
  const [picked, setPicked] = useState<Set<number> | null>(null);
  const { t } = useI18n();

  // 首次拿到数据时把当前选择灌进来；之后完全由用户操作决定，不再被后台刷新覆盖
  const selected =
    picked ?? new Set((choices ?? []).filter((c) => c.sync_enabled).map((c) => c.course_id));

  const toggle = (id: number) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };

  const active = (choices ?? []).filter((c) => !c.is_concluded);
  const concluded = (choices ?? []).filter((c) => c.is_concluded);

  // 会被取消的课里有多少已经同步过内容 —— 保存前得让用户看见代价
  const losing = (choices ?? []).filter((c) => c.sync_enabled && !selected.has(c.course_id));
  const losingFiles = losing.reduce((n, c) => n + c.file_count, 0);
  const adding = (choices ?? []).filter((c) => !c.sync_enabled && selected.has(c.course_id));
  const dirty = losing.length > 0 || adding.length > 0;

  // 保存按钮的三种状态，颜色就是"这次点下去会发生什么"：
  //   有取消 → 红（会删数据，即使同时也有新增，破坏性的那一面优先说）
  //   只新增 → 绿
  //   没改动 → 中性，点了也只是关掉对话框
  const saveStyle = losing.length
    ? "bg-red-600 text-white hover:bg-red-700"
    : adding.length
      ? "bg-green-600 text-white hover:bg-green-700"
      : "bg-neutral-200 text-neutral-500 hover:bg-neutral-300 dark:bg-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-600";

  const section = (title: string, rows: CourseChoice[]) =>
    rows.length === 0 ? null : (
      <div key={title}>
        <div className="sticky top-0 bg-white px-1 py-2 text-xs font-medium uppercase tracking-wide text-neutral-400 dark:bg-neutral-900">
          {title}
        </div>
        {rows.map((c) => (
          <label
            key={c.course_id}
            className="flex cursor-pointer items-center gap-3 rounded-lg px-1 py-2 hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <input
              type="checkbox"
              checked={selected.has(c.course_id)}
              onChange={() => toggle(c.course_id)}
              className="size-4 shrink-0"
            />
            <span className="min-w-0 flex-1">
              {/* 不能 truncate：Canvas 的课程名常常只在**结尾**才分得出彼此
                  （…all lecture sections / …all tutorial sections），
                  截断之后两门课长得一模一样，用户根本没法勾对 */}
              <span className="block text-sm leading-snug [overflow-wrap:anywhere]">
                {c.course_code || c.name}
                {c.is_concluded && <ConcludedTag />}
              </span>
              <span className="text-xs text-neutral-400">
                {c.term_name || "—"}
                {c.file_count > 0 && ` · ${t("course.resourceCount", { n: c.file_count })}`}
              </span>
            </span>
          </label>
        ))}
      </div>
    );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[80vh] w-full max-w-xl flex-col rounded-xl border border-neutral-200 bg-white shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
        <div className="flex items-baseline justify-between border-b border-neutral-200 p-5 dark:border-neutral-800">
          <h3 className="text-lg font-semibold">{t("picker.title")}</h3>
          <span className="text-xs text-neutral-400">
            {t("picker.pickedCount", { n: selected.size })}
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4">
          {isLoading && <p className="p-4 text-sm text-neutral-400">{t("common.loading")}</p>}
          {!isLoading && (choices ?? []).length === 0 && (
            <p className="p-4 text-sm text-neutral-400">{t("picker.empty")}</p>
          )}
          {section(t("picker.active"), active)}
          {section(t("picker.concluded"), concluded)}
        </div>

        {losing.length > 0 && (
          <p className="border-t border-neutral-200 px-5 py-3 text-xs text-red-600 dark:border-neutral-800">
            {t("picker.losing", { n: losing.length, files: losingFiles })}
          </p>
        )}

        <div className="flex justify-end gap-2 border-t border-neutral-200 p-5 dark:border-neutral-800">
          <button
            onClick={onClose}
            disabled={save.isPending}
            className="rounded-lg border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {t("common.cancel")}
          </button>
          <button
            onClick={async () => {
              // 什么都没改就别打接口、也别提示 —— 一次"保存"没有任何后果，
              // 却弹一条"已保存"，只会让人怀疑自己是不是误改了什么
              if (!dirty) {
                onClose();
                return;
              }
              await save.mutateAsync([...selected]);
              onSaved();
            }}
            disabled={save.isPending || isLoading}
            className={`rounded-lg px-4 py-1.5 text-sm disabled:opacity-50 ${saveStyle}`}
          >
            {save.isPending ? t("common.saving") : t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- 子组件 ----------

const INPUT =
  "min-w-0 rounded-lg border border-neutral-300 bg-transparent px-3 py-1.5 text-sm outline-none focus:border-neutral-500 disabled:opacity-50 dark:border-neutral-700 dark:focus:border-neutral-500";

/** Canvas 地址和令牌。改了先问一次 Canvas 连上的是谁，确认后保存并同步所有课程。
 *  同步进行中整块锁住：那一轮正拿着旧令牌在跑 */
function CanvasConnection({ settings, locked }: { settings?: AppSettings; locked: boolean }) {
  const i18n = useI18n();
  const { t } = i18n;
  const toast = useToast();
  const check = useCheckCanvasConnection();
  const save = useSaveCanvasConnection();
  const current = settings?.canvas_base_url ?? "";
  const [url, setUrl] = useState(current);
  const [token, setToken] = useState("");
  const [confirm, setConfirm] = useState<CanvasConnectionCheck | null>(null);

  useEffect(() => setUrl(current), [current]);

  const configured = settings?.canvas_configured ?? false;
  const hint = settings?.canvas_token_hint;
  const dirty = token.trim() !== "" || url.trim().replace(/\/+$/, "") !== current;
  const demo = settings?.demo_mode ?? false;
  const busy = locked || demo || check.isPending || save.isPending;
  const body = { base_url: url, token: token.trim() || undefined };

  const submit = async () => {
    try {
      const res = await check.mutateAsync(body);
      if (res.changed) setConfirm(res);
      else {
        setToken("");
        toast(t("settings.canvasUnchanged"));
      }
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  const confirmSave = async () => {
    try {
      await save.mutateAsync(body);
      setToken("");
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
    setConfirm(null);
  };

  const name = confirm?.user_name || "Canvas";

  return (
    <div className="space-y-3 p-5">
      <div className="grid grid-cols-[7rem_1fr] items-center gap-3">
        <label htmlFor="canvas-url" className="text-sm font-medium">
          {t("settings.canvasUrl")}
        </label>
        <input
          id="canvas-url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
          placeholder="https://canvas.instructure.com"
          spellCheck={false}
          className={INPUT}
        />
        <label htmlFor="canvas-token" className="text-sm font-medium">
          {t("settings.canvasToken")}
        </label>
        <input
          id="canvas-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && dirty && !busy) submit();
          }}
          disabled={busy}
          placeholder={configured && hint ? `•••• ${hint}` : ""}
          autoComplete="off"
          spellCheck={false}
          className={INPUT}
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        {configured ? (
          <span className="truncate text-xs text-neutral-400">{settings?.canvas_user_name}</span>
        ) : (
          <span className="text-xs text-amber-600">{t("settings.notConfigured")}</span>
        )}
        <button
          onClick={submit}
          disabled={busy || !dirty}
          className="flex shrink-0 items-center gap-2 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {check.isPending && <Spinner />}
          {t("common.save")}
        </button>
      </div>
      {!configured && (
        <ol className="list-decimal space-y-1 rounded-lg bg-amber-50 p-4 pl-9 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          <li>{t("settings.canvasStep1")}</li>
          <li>{t("settings.canvasStep2")}</li>
          <li>{t("settings.canvasStep3")}</li>
        </ol>
      )}
      {confirm && (
        <ConfirmDialog
          title={t("settings.canvasConfirm")}
          body={t(
            confirm.account_changed ? "settings.canvasSwitchBody" : "settings.canvasConfirmBody",
            { name },
          )}
          confirmLabel={t(
            confirm.account_changed ? "settings.canvasSwitchAction" : "settings.canvasConfirmAction",
          )}
          pending={save.isPending}
          onCancel={() => setConfirm(null)}
          onConfirm={confirmSave}
        />
      )}
    </div>
  );
}

function Toggle({
  label,
  checked,
  disabled,
  onChange,
  first,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
  first?: boolean;
}) {
  return (
    <label
      className={`flex items-center justify-between gap-4 p-5 ${
        first ? "" : "border-t border-neutral-200 dark:border-neutral-800"
      } ${disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}
    >
      <span className="min-w-0 text-sm font-medium">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 shrink-0"
      />
    </label>
  );
}

const STATUS_STYLE: Record<string, { dot: string; text: string }> = {
  ok: { dot: "bg-green-500", text: "text-green-600" },
  failed: { dot: "bg-red-500", text: "text-red-600" },
  syncing: { dot: "bg-blue-500 animate-pulse", text: "text-blue-600" },
  never: { dot: "bg-neutral-300", text: "text-neutral-400" },
};

function CourseRow({
  course,
  locked,
  syncing,
  onSync,
}: {
  course: CourseSyncStatus;
  locked: boolean;
  syncing: boolean;
  onSync: () => void;
}) {
  const [open, setOpen] = useState(false);
  const i18n = useI18n();
  const { t, formatDate } = i18n;
  const effective = syncing ? "syncing" : course.status;
  const style = STATUS_STYLE[effective] ?? STATUS_STYLE.never;
  // 只有「还不支持」需要用户注意 —— 其余三种都是正常情况
  const needsAttention = course.boards.filter((b) => b.status === "unsupported");

  return (
    <div className="border-t border-neutral-200 first:border-t-0 dark:border-neutral-800">
      <div className="flex items-center gap-3 px-5 py-3">
        <span className={`size-2 shrink-0 rounded-full ${style.dot}`} />
        <button onClick={() => setOpen(!open)} className="min-w-0 flex-1 text-left">
          <span className="block truncate text-sm font-medium">
            {course.course_code || course.name}
            {course.is_concluded && <ConcludedTag />}
          </span>
          <span className={`text-xs ${style.text}`}>
            {i18n.tOr(`status.${effective}`, effective)}
            <span className="text-neutral-400">
              {course.last_synced_at && ` · ${formatDate(course.last_synced_at)}`}
              {` · ${t("course.resourceCount", { n: course.file_count })}`}
            </span>
          </span>
        </button>
        <button
          onClick={onSync}
          disabled={locked || syncing}
          className="flex shrink-0 items-center gap-1.5 rounded-lg border border-neutral-300 px-2.5 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {syncing && <Spinner className="size-3" />}
          {syncing ? t("settings.syncingShort") : t("settings.manualSync")}
        </button>
        <button
          onClick={() => setOpen(!open)}
          className="shrink-0 text-xs text-neutral-400 hover:text-neutral-600"
        >
          {open ? t("common.collapse") : t("common.details")}
        </button>
      </div>

      {course.error && (
        <p className="mx-5 mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
          {course.error}
        </p>
      )}

      {open && (
        <div className="space-y-1 px-5 pb-4">
          {course.boards.length === 0 && (
            <p className="text-xs text-neutral-400">{t("settings.noSyncRecord")}</p>
          )}
          {/* 一行一个板块 —— 就是 Canvas 课程左边那一列。
              这门课没有的板块不占行，所以看到几行就是这门课有几个板块 */}
          {course.boards.map((b) => (
            <div key={b.id} className="flex items-center justify-between gap-3 text-xs">
              <span className="text-neutral-600 dark:text-neutral-400">{b.label}</span>
              <span className="flex items-center gap-2">
                {b.count > 0 && (
                  <span className="text-neutral-400">
                    {t("course.resourceCount", { n: b.count })}
                  </span>
                )}
                <span
                  className={
                    b.status === "scanned"
                      ? "text-green-600"
                      : b.status === "unsupported"
                        ? "text-amber-600"
                        : "text-neutral-400"
                  }
                >
                  {i18n.tOr(`board.${b.status}`, b.status)}
                </span>
              </span>
            </div>
          ))}
          {needsAttention.map((b) => (
            <p key={`${b.id}-detail`} className="pt-1 text-xs text-amber-600">
              {t("settings.unsupportedHint", { boards: b.detail ?? "" })}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

/** 同步里"任务 / 日历事件"那两行：状态点 + 条数 + 上次同步时间 */
function SyncSourceRow({
  label,
  state,
  syncing,
}: {
  label: string;
  state: PlannerSyncState | undefined;
  syncing: boolean;
}) {
  const i18n = useI18n();
  const { t, formatDate } = i18n;
  const effective = syncing ? "syncing" : (state?.status ?? "never");
  const style = STATUS_STYLE[effective] ?? STATUS_STYLE.never;
  return (
    <div className="border-t border-neutral-200 px-5 py-3 dark:border-neutral-800">
      <div className="flex items-center gap-3">
        <span className={`size-2 shrink-0 rounded-full ${style.dot}`} />
        <span className="min-w-0 flex-1 text-sm font-medium">{label}</span>
        <span className={`text-xs ${style.text}`}>
          {i18n.tOr(`status.${effective}`, effective)}
          <span className="text-neutral-400">
            {state?.last_synced_at && ` · ${formatDate(state.last_synced_at)}`}
            {state && state.status !== "never" && ` · ${t("settings.itemCount", { n: state.count })}`}
          </span>
        </span>
      </div>
      {state?.error && !syncing && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
          {state.error}
        </p>
      )}
    </div>
  );
}

/** 一门课的颜色。原生取色器拖动时会连续触发，停下来再存 */
function ColorRow({ course }: { course: Course }) {
  const { t } = useI18n();
  const setColor = useSetCourseColor();
  const [value, setValue] = useState(course.color ?? "#a3a3a3");
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => setValue(course.color ?? "#a3a3a3"), [course.color]);

  const change = (next: string) => {
    setValue(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setColor.mutate({ id: course.id, color: next }), 400);
  };

  return (
    <div className="flex items-center gap-3 px-5 py-2">
      <input
        type="color"
        value={value}
        onChange={(e) => change(e.target.value)}
        className="size-7 shrink-0 cursor-pointer rounded border border-neutral-300 bg-transparent p-0.5 dark:border-neutral-700"
      />
      <span className="min-w-0 flex-1 truncate text-sm">{course.course_code || course.name}</span>
      {course.custom_color && (
        <button
          onClick={() => setColor.mutate({ id: course.id, color: "" })}
          className="shrink-0 text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
        >
          {t("settings.resetColor")}
        </button>
      )}
    </div>
  );
}

/** 设置 → Planner → 课程颜色。单独一页：九门课的取色器挤在设置里太乱 */
export function CourseColors() {
  const { t } = useI18n();
  const { data: courses } = useCourses(true);
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("settings.colorsTitle")}</h1>
        <Link
          to="/settings"
          className="rounded-lg border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-neutral-100 dark:border-neutral-600 dark:text-neutral-200 dark:hover:bg-neutral-800"
        >
          ← {t("common.back")}
        </Link>
      </div>
      <Panel title={t("settings.sectionTasks")}>
        <div className="py-2">
          {(courses ?? []).map((c) => (
            <ColorRow key={c.id} course={c} />
          ))}
        </div>
      </Panel>
    </div>
  );
}

/** 新建清单时按顺序给一个颜色，免得一排全是灰的 */
const LIST_PALETTE = ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626"];

function ListRow({ list }: { list: TaskList }) {
  const i18n = useI18n();
  const { t } = i18n;
  const update = useUpdateTaskList();
  const remove = useDeleteTaskList();
  const toast = useToast();
  const [name, setName] = useState(list.name);
  const [color, setColor] = useState(list.color ?? "#64748b");
  const [confirming, setConfirming] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => setName(list.name), [list.name]);
  useEffect(() => setColor(list.color ?? "#64748b"), [list.color]);

  const save = async (patch: { name?: string; color?: string }) => {
    try {
      await update.mutateAsync({ id: list.id, ...patch });
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  const commitName = () => {
    const next = name.trim();
    if (!next) return setName(list.name);
    if (next !== list.name) save({ name: next });
  };

  return (
    <div className="flex items-center gap-3 px-5 py-2">
      <input
        type="color"
        value={color}
        onChange={(e) => {
          const next = e.target.value;
          setColor(next);
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => save({ color: next }), 400);
        }}
        className="size-7 shrink-0 cursor-pointer rounded border border-neutral-300 bg-transparent p-0.5 dark:border-neutral-700"
      />
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        onBlur={commitName}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
          if (e.key === "Escape") setName(list.name);
        }}
        className="min-w-0 flex-1 rounded-md bg-transparent px-2 py-1 text-sm outline-none hover:bg-neutral-100 focus:bg-neutral-100 dark:hover:bg-neutral-800 dark:focus:bg-neutral-800"
      />
      <span className="shrink-0 text-xs tabular-nums text-neutral-500">{list.task_count}</span>
      <button
        onClick={() => setConfirming(true)}
        className="shrink-0 text-xs text-neutral-500 hover:text-red-600"
      >
        {t("task.delete")}
      </button>
      {confirming && (
        <ConfirmDialog
          title={t("settings.deleteList", { name: list.name })}
          confirmLabel={t("task.delete")}
          pending={remove.isPending}
          onCancel={() => setConfirming(false)}
          onConfirm={async () => {
            try {
              await remove.mutateAsync(list.id);
            } catch (err) {
              toast(apiMessage(err, i18n), "error");
            }
            setConfirming(false);
          }}
        />
      )}
    </div>
  );
}

/** 设置 → Planner → 清单 */
export function TaskLists() {
  const i18n = useI18n();
  const { t } = i18n;
  const { data: lists } = useTaskLists();
  const create = useCreateTaskList();
  const toast = useToast();
  const [name, setName] = useState("");

  const add = async () => {
    const value = name.trim();
    if (!value || create.isPending) return;
    try {
      await create.mutateAsync({
        name: value,
        color: LIST_PALETTE[(lists?.length ?? 0) % LIST_PALETTE.length],
      });
      setName("");
    } catch (err) {
      toast(apiMessage(err, i18n), "error");
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{t("settings.lists")}</h1>
        <Link
          to="/settings"
          className="rounded-lg border border-neutral-300 px-3 py-1 text-xs text-neutral-700 hover:bg-neutral-100 dark:border-neutral-600 dark:text-neutral-200 dark:hover:bg-neutral-800"
        >
          ← {t("common.back")}
        </Link>
      </div>
      <Panel title={t("settings.sectionTasks")}>
        <div className="py-2">
          {(lists ?? []).map((l) => (
            <ListRow key={l.id} list={l} />
          ))}
          <div className="flex items-center gap-3 px-5 py-2">
            <span className="flex size-7 shrink-0 items-center justify-center text-lg text-neutral-400">
              +
            </span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") add();
              }}
              placeholder={t("settings.newList")}
              className="min-w-0 flex-1 rounded-md bg-transparent px-2 py-1 text-sm outline-none placeholder:text-neutral-400"
            />
          </div>
        </div>
      </Panel>
    </div>
  );
}
