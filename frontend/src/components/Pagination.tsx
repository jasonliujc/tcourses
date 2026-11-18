import { useI18n } from "@/lib/i18n";
import { PAGE_SIZE_CHOICES } from "@/lib/prefs";

interface Props {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

/** 页码按钮：始终显示首尾页和当前页附近，中间用省略号收起 */
function pageNumbers(page: number, totalPages: number): (number | "…")[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const out: (number | "…")[] = [1];
  const from = Math.max(2, page - 1);
  const to = Math.min(totalPages - 1, page + 1);
  if (from > 2) out.push("…");
  for (let i = from; i <= to; i += 1) out.push(i);
  if (to < totalPages - 1) out.push("…");
  out.push(totalPages);
  return out;
}

export default function Pagination({
  page,
  totalPages,
  total,
  pageSize,
  onChange,
  onPageSizeChange,
}: Props) {
  const { t } = useI18n();
  if (total === 0) return null;

  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
      <div className="flex items-center gap-3 text-xs text-neutral-400">
        <span>{t("page.range", { from: first, to: last, total })}</span>
        {/* 每页几个跟着"共几个"放：都是在说这个列表的规模 */}
        <label className="flex items-center gap-1">
          {t("page.sizePrefix")}
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="rounded-md border border-neutral-300 bg-transparent px-1.5 py-0.5 text-xs dark:border-neutral-700"
          >
            {PAGE_SIZE_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          {t("page.sizeSuffix")}
        </label>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center gap-1">
          <button
            onClick={() => onChange(page - 1)}
            disabled={page === 1}
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs disabled:opacity-40 dark:border-neutral-700"
          >
            {t("page.prev")}
          </button>
          {pageNumbers(page, totalPages).map((n, i) =>
            n === "…" ? (
              <span key={`gap-${i}`} className="px-1 text-xs text-neutral-400">
                …
              </span>
            ) : (
              <button
                key={n}
                onClick={() => onChange(n)}
                className={`min-w-7 rounded-md px-2 py-1 text-xs ${
                  n === page
                    ? "bg-neutral-900 text-white dark:bg-white dark:text-neutral-900"
                    : "border border-neutral-300 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
                }`}
              >
                {n}
              </button>
            ),
          )}
          <button
            onClick={() => onChange(page + 1)}
            disabled={page === totalPages}
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs disabled:opacity-40 dark:border-neutral-700"
          >
            {t("page.next")}
          </button>
        </div>
      )}
    </div>
  );
}
