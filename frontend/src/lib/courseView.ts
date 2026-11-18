/** 记住每门课上次停在哪个视图。
 *
 *  为什么按课程分别记：视图状态是组件 state，而课程页在切换课程时并不重新挂载
 *  （同一个路由、只换参数），所以状态会被带到下一门课 —— 表现就是在 A 课看了
 *  「我的分组」，点进 B 课也停在「我的分组」，哪怕 B 课根本没有分组。
 *
 *  第一次进一门课时没有记录，用默认视图（自动整理 → 默认）。
 */

const KEY = "tcourses:course-view";

export interface CourseView {
  topTab: "auto" | "manual";
  autoTab: string;
}

export const DEFAULT_VIEW: CourseView = { topTab: "auto", autoTab: "default" };

type Stored = Record<string, CourseView>;

function readAll(): Stored {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Stored) : {};
  } catch {
    // 隐私模式、存储被禁用等：当作没有记录，用默认视图
    return {};
  }
}

export function loadCourseView(courseId: number): CourseView {
  const stored = readAll()[String(courseId)];
  if (!stored || (stored.topTab !== "auto" && stored.topTab !== "manual")) {
    return DEFAULT_VIEW;
  }
  return stored;
}

export function saveCourseView(courseId: number, view: CourseView): void {
  try {
    const all = readAll();
    all[String(courseId)] = view;
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // 存不下就算了，只是下次回到默认视图，不影响使用
  }
}
