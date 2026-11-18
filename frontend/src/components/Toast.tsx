import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

import { useI18n } from "@/lib/i18n";

/** 右下角的轻量提示，几秒后自己消失。
 *
 *  用它而不是在面板里插一块结果区：那块区域会把它下面的内容整体推下去，
 *  用户刚点完保存，视线还在原来的位置，结果整个页面动了一下。
 *
 *  做成 Provider + `useToast()` 而不是让每个页面自己 `useState` ——
 *  下载失败、保存成功这类反馈到处都要用，各写一份的话样式和时长迟早走样。 */

export type ToastKind = "info" | "error";

interface ToastItem {
  id: number;
  message: string;
  kind: ToastKind;
}

const ToastCtx = createContext<(message: string, kind?: ToastKind) => void>(() => {});

/** 在任意组件里弹一条右下角提示。默认 info，失败用 "error"（红色、停留更久） */
export const useToast = () => useContext(ToastCtx);

/** 失败的那条要让人来得及看清是哪个文件出了问题 */
const DURATION: Record<ToastKind, number> = { info: 3000, error: 6000 };

function ToastCard({ item, onDone }: { item: ToastItem; onDone: () => void }) {
  const { t } = useI18n();
  useEffect(() => {
    const timer = setTimeout(onDone, DURATION[item.kind]);
    return () => clearTimeout(timer);
  }, [item, onDone]);

  return (
    <div
      className={`flex items-center gap-3 rounded-lg border bg-white px-4 py-3 text-sm shadow-lg dark:bg-neutral-800 ${
        item.kind === "error"
          ? "border-red-300 text-red-700 dark:border-red-800 dark:text-red-300"
          : "border-neutral-200 dark:border-neutral-700"
      }`}
    >
      <span>{item.message}</span>
      <button
        onClick={onDone}
        className="shrink-0 text-xs text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200"
      >
        {t("common.close")}
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  // 身份用自增 id 而不是消息内容：连着弹两条一样的提示也要是两条，
  // 而且第二条不能被第一条的定时器提前收走
  const show = useCallback((message: string, kind: ToastKind = "info") => {
    const id = nextId.current++;
    setItems((prev) => [...prev, { id, message, kind }]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastCtx.Provider value={show}>
      {children}
      {/* column-reverse：新的一条压在最下面，靠近视线落点 */}
      <div className="pointer-events-none fixed bottom-6 right-6 z-[60] flex flex-col-reverse gap-2">
        {items.map((t) => (
          <div key={t.id} className="pointer-events-auto">
            <ToastCard item={t} onDone={() => dismiss(t.id)} />
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
