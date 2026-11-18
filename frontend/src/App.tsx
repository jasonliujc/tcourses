import { NavLink, Navigate, Route, Routes } from "react-router-dom";

import { useCourses, useSettings, useUnreadCount } from "@/api/hooks";
import Calendar from "@/pages/Calendar";
import CourseFiles from "@/pages/CourseFiles";
import CourseManager from "@/pages/CourseManager";
import ResourceBrowser from "@/pages/ResourceBrowser";
import Resources from "@/pages/Resources";
import ResourcesHome from "@/pages/ResourcesHome";
import Settings, { CourseColors, TaskLists } from "@/pages/Settings";
import Tasks from "@/pages/Tasks";
import Updates from "@/pages/Updates";
import { useI18n } from "@/lib/i18n";
import type { Course } from "@/types";

function navClass({ isActive }: { isActive: boolean }) {
  return [
    "block rounded-lg px-3 py-2 text-sm transition",
    isActive
      ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
      : "text-neutral-600 hover:bg-neutral-200/60 dark:text-neutral-400 dark:hover:bg-neutral-800",
  ].join(" ");
}

function Sidebar() {
  const { data: courses } = useCourses();
  const { data: unread } = useUnreadCount();
  const { data: settings } = useSettings();
  const { t } = useI18n();

  // 侧边栏自己滚：课程多起来之后，跟着主内容一起滚会把导航滚出视野
  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col gap-6 overflow-y-auto border-r border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-center gap-2 px-3">
        <span className="text-lg font-semibold tracking-tight">TCourses</span>
        {settings?.demo_mode && (
          <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
            {t("app.demo")}
          </span>
        )}
      </div>

      <nav className="space-y-1">
        <NavLink to="/" end className={navClass}>
          {t("nav.updates")}
          {!!unread?.unread && (
            <span className="ml-2 rounded-full bg-red-500 px-1.5 py-0.5 text-xs text-white">
              {unread.unread}
            </span>
          )}
        </NavLink>
        <NavLink to="/resources" className={navClass}>
          {t("nav.resources")}
        </NavLink>
        <NavLink to="/calendar" className={navClass}>
          {t("nav.calendar")}
        </NavLink>
        <NavLink to="/tasks" className={navClass}>
          {t("nav.tasks")}
        </NavLink>
        <NavLink to="/settings" className={navClass}>
          {t("nav.settings")}
        </NavLink>
      </nav>

      {/* 在读和已结课不再分成两组：同步哪些课已经由用户自己勾了，
          结课与否只是一个标签，不该再决定它出现在哪儿 */}
      <CourseSection courses={courses ?? []} />
    </aside>
  );
}

function CourseSection({ courses }: { courses: Course[] }) {
  const { t } = useI18n();
  return (
    <div>
      {/* 标题本身就是入口：格式和原来的小标题一致，hover 时才亮起来并冒出齿轮，
          静止状态下不给边栏添任何新的视觉噪音 */}
      <NavLink
        to="/courses/manage"
        title={t("nav.manageHint")}
        className={({ isActive }) =>
          `group flex items-center justify-between rounded-lg px-3 pb-2 pt-1 text-xs font-medium uppercase tracking-wide transition ${
            isActive
              ? "text-neutral-900 dark:text-white"
              : "text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200"
          }`
        }
      >
        {t("nav.courses")}
        <GearIcon />
      </NavLink>
      <nav className="space-y-1">
        {courses.map((c) => (
          <NavLink key={c.id} to={`/courses/${c.id}`} className={navClass}>
            <span className="block truncate">{c.course_code || c.name}</span>
            <span className="text-xs text-neutral-400">
              {t("course.resourceCount", { n: c.file_count })}
              {c.is_concluded && <ConcludedTag />}
            </span>
          </NavLink>
        ))}
        {courses.length === 0 && (
          <p className="px-3 text-xs text-neutral-400">{t("nav.noCourses")}</p>
        )}
      </nav>
    </div>
  );
}

function GearIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      className="size-3.5 opacity-0 transition group-hover:opacity-100"
      aria-hidden
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.14.5.53.9 1.03 1.03H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export function ConcludedTag() {
  const { t } = useI18n();
  return (
    <span className="ml-1.5 rounded bg-neutral-200 px-1 py-px text-[10px] text-neutral-600 dark:bg-neutral-700 dark:text-neutral-300">
      {t("course.concluded")}
    </span>
  );
}

export default function App() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto p-8 [scrollbar-gutter:stable]">
        <Routes>
          <Route path="/" element={<Updates />} />
          {/* 放在 :courseId 前面，免得以后有人把路由改成按顺序匹配的实现 */}
          <Route path="/courses/manage" element={<CourseManager />} />
          <Route path="/courses/:courseId" element={<CourseFiles />} />
          {/* 全局资源：外壳带标题和两个入口，内容由子路由填。
              「全部星标」是主页那个框的二级页面，不是弹窗 —— 进去要能搜索、
              筛选、翻页，那是一个页面该干的事 */}
          <Route path="/resources" element={<Resources />}>
            <Route index element={<ResourcesHome />} />
            <Route path="find" element={<ResourceBrowser />} />
            <Route path="starred" element={<ResourceBrowser starred />} />
          </Route>
          <Route path="/calendar" element={<Calendar />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/assignments" element={<Navigate to="/tasks" replace />} />
          <Route path="/projects" element={<Navigate to="/tasks" replace />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/colors" element={<CourseColors />} />
          <Route path="/settings/lists" element={<TaskLists />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
