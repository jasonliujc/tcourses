import { EMPTY_FILTER, type FileFilter } from "@/lib/fileFilter";

/** 每个视图各记各的筛选条件。
 *
 *  为什么按视图分开记：「默认」按 Canvas 来源看，「按资源类型」按类型看，
 *  「我的分组」看的是自己整理过的东西 —— 三者要回答的问题不一样，把在默认视图里
 *  设的"只看 PDF、三月之后"原样带到我的分组去，用户只会看到一个莫名其妙的空列表。
 *  但切回来的时候还得在，不然每次来回都要重设一遍。
 *
 *  为什么在内存里而不是 localStorage：筛选是"我现在正在找东西"的状态，不是长期偏好。
 *  存下来的话，下次打开应用看到的是一个已经被筛过的列表，而那一刻用户根本不记得
 *  自己上次筛了什么。放模块级的 Map 里，换课、换视图、甚至整棵树重挂（换语言/时区）
 *  都还在，刷新页面就干净了。
 */
const store = new Map<string, FileFilter>();

const at = (courseId: number, view: string) => `${courseId}:${view}`;

export function loadFilter(courseId: number, view: string): FileFilter {
  return store.get(at(courseId, view)) ?? EMPTY_FILTER;
}

export function saveFilter(courseId: number, view: string, filter: FileFilter): void {
  store.set(at(courseId, view), filter);
}
