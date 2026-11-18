/** 跨课程的个人偏好。
 *
 *  和 `courseView` 分开：那个是"这门课上次停在哪个视图"，按课程记；
 *  这里是"我习惯一页看多少个"，和课程无关，换一门课不该变回去。
 */

const PAGE_SIZE_KEY = "tcourses:page-size";

export const PAGE_SIZE_CHOICES = [15, 25, 50, 100] as const;
export const DEFAULT_PAGE_SIZE = 15;

export function loadPageSize(): number {
  try {
    const raw = Number(localStorage.getItem(PAGE_SIZE_KEY));
    // 只接受选项里有的值：存储可能被手改过，或者以后选项变了留下了旧值
    return (PAGE_SIZE_CHOICES as readonly number[]).includes(raw) ? raw : DEFAULT_PAGE_SIZE;
  } catch {
    // 隐私模式、存储被禁用等
    return DEFAULT_PAGE_SIZE;
  }
}

export function savePageSize(size: number): void {
  try {
    localStorage.setItem(PAGE_SIZE_KEY, String(size));
  } catch {
    // 存不下就算了，只是下次回到默认值
  }
}
