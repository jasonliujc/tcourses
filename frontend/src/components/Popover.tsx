import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

const GAP = 8;
const MARGIN = 12;

/** 贴着某个元素弹出的浮层。
 *
 *  默认放在锚点右边、顶端对齐；右边放不下就放左边，两边都放不下就压在锚点上。
 *  上下超出视口时往回推。这样用户一眼就知道它是从哪儿出来的 —— 居中的弹窗
 *  会把这层关系切断。
 *
 *  点外面、按 Esc、页面滚动会关掉（滚动之后锚点已经不在原处了）；
 *  窗口尺寸变了就重新摆一次。
 */
export default function Popover({
  anchor,
  width = 360,
  onClose,
  children,
}: {
  anchor: DOMRect;
  width?: number;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const place = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const h = el.offsetHeight;

    let left: number;
    if (anchor.right + GAP + width <= vw - MARGIN) left = anchor.right + GAP;
    else if (anchor.left - GAP - width >= MARGIN) left = anchor.left - GAP - width;
    else left = Math.min(Math.max(MARGIN, anchor.left), vw - width - MARGIN);

    let top = anchor.top;
    if (top + h > vh - MARGIN) top = vh - MARGIN - h;
    top = Math.max(MARGIN, top);
    setPos({ top, left });
  }, [anchor, width]);

  useLayoutEffect(place, [place]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onScroll = (e: Event) => {
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      onClose();
    };
    // 下一拍再挂：打开它的那次点击本身不能把它关掉
    const timer = window.setTimeout(() => {
      document.addEventListener("mousedown", onDown);
      document.addEventListener("keydown", onKey);
      window.addEventListener("scroll", onScroll, true);
    });
    window.addEventListener("resize", place);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [onClose, place]);

  return createPortal(
    <div
      ref={ref}
      style={{ top: pos?.top ?? -9999, left: pos?.left ?? -9999, width }}
      className="fixed z-50 max-h-[calc(100vh-24px)] overflow-y-auto rounded-xl border border-neutral-200 bg-white shadow-2xl ring-1 ring-black/5 dark:border-neutral-600 dark:bg-neutral-800 dark:ring-white/5"
    >
      {children}
    </div>,
    document.body,
  );
}
