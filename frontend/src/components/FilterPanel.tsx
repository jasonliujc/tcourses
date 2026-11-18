import { useState } from "react";

import { useI18n } from "@/lib/i18n";
import {
  activeCount,
  sameFilter,
  toggle,
  EMPTY_FILTER,
  type FileFilter,
} from "@/lib/fileFilter";
import type { Medium } from "@/types";

/** 面板上能选的东西。由调用方从当前页面已有的数据里现算 ——
 *  这样课程页给的是这门课的板块和容器，将来全局页给的是全部课程的，组件本身不用改。
 *
 *  **不带数量。** 数量只能是"没筛之前有几个"，而用户一旦勾上「文件」，
 *  旁边还写着 Syllabus 5、筛出来却只有 1，那个 5 就成了假数字。要让它准，
 *  得每勾一下就把每个选项各算一遍，代价和收益完全不成比例 —— 侧边栏的计数
 *  是实时的，那里才是看数量的地方。 */
export interface FilterOptions {
  boards: { id: string; name: string }[];
  containers: { key: string; name: string }[];
  kinds: { key: string; name: string; medium: Medium }[];
  /** 预留：只有全局文件管理会传。课程页里一门课是既定前提，没什么可筛的 */
  courses?: { id: number; name: string }[];
}

/** 筛选面板。
 *
 *  和「选择同步课程」同一套壳：改动只在本地攒着，点「应用」才生效，点「取消」
 *  原样丢弃。没有改动时「应用」是灰的，点了只是关掉 —— 一次没有后果的操作
 *  不该给出"已应用"的反馈。
 *
 *  组件本身不认识课程，只认识一组条件和一组可选项，所以全局文件管理可以原样拿去用，
 *  多传一个 `courses` 就多出一节。
 */
export default function FilterPanel({
  value,
  options,
  onCancel,
  onApply,
}: {
  value: FileFilter;
  options: FilterOptions;
  onCancel: () => void;
  onApply: (next: FileFilter) => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState<FileFilter>(value);
  const dirty = !sameFilter(draft, value);
  const patch = (p: Partial<FileFilter>) => setDraft({ ...draft, ...p });

  const mediaOn = (m: Medium) => draft.media.length === 0 || draft.media.includes(m);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-xl border border-neutral-200 bg-white shadow-xl dark:border-neutral-700 dark:bg-neutral-900">
        <div className="flex items-baseline justify-between border-b border-neutral-200 p-5 dark:border-neutral-800">
          <h3 className="text-lg font-semibold">{t("filter.title")}</h3>
          <span className="text-xs text-neutral-400">
            {t("filter.activeCount", { n: activeCount(draft) })}
          </span>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
          {/* 文件 / 链接放最上面，而且比下面的筛选项大一号：这是最常用的一刀，
              也是唯一一个"两个都要"的默认状态需要说清楚的地方 */}
          <section>
            <SectionTitle>{t("filter.show")}</SectionTitle>
            <div className="flex gap-2">
              {(["file", "link"] as Medium[]).map((m) => (
                <button
                  key={m}
                  onClick={() =>
                    // 关掉唯一亮着的那个 = 回到"两个都要"，不会出现两个都灭的空状态
                    patch({ media: draft.media.length === 1 && draft.media[0] === m ? [] : [m] })
                  }
                  className={`flex-1 rounded-lg border px-4 py-2.5 text-sm font-medium transition ${
                    mediaOn(m)
                      ? m === "link"
                        ? "border-sky-400 bg-sky-50 text-sky-700 dark:border-sky-700 dark:bg-sky-950/40 dark:text-sky-300"
                        : "border-amber-400 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-500"
                      : "border-neutral-200 text-neutral-400 dark:border-neutral-700"
                  }`}
                >
                  {t(m === "link" ? "filter.media.link" : "filter.media.file")}
                </button>
              ))}
            </div>
          </section>

          <section>
            <SectionTitle>{t("filter.dateRange")}</SectionTitle>
            <div className="flex items-center gap-2">
              <input
                type="date"
                value={draft.dateFrom}
                max={draft.dateTo || undefined}
                onChange={(e) => patch({ dateFrom: e.target.value })}
                className="h-9 flex-1 rounded-lg border border-neutral-300 px-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
              />
              <span className="text-neutral-400">–</span>
              <input
                type="date"
                value={draft.dateTo}
                min={draft.dateFrom || undefined}
                onChange={(e) => patch({ dateTo: e.target.value })}
                className="h-9 flex-1 rounded-lg border border-neutral-300 px-2 text-sm dark:border-neutral-700 dark:bg-neutral-900"
              />
            </div>
          </section>

          {options.courses && options.courses.length > 0 && (
            <Chips
              title={t("filter.courses")}
              items={options.courses.map((c) => ({ key: String(c.id), name: c.name }))}
              selected={draft.courseIds.map(String)}
              onToggle={(key) => patch({ courseIds: toggle(draft.courseIds, Number(key)) })}
            />
          )}

          <Chips
            title={t("filter.boards")}
            items={options.boards.map((b) => ({ key: b.id, name: b.name }))}
            selected={draft.boards}
            onToggle={(key) => patch({ boards: toggle(draft.boards, key) })}
          />

          {/* 类型分成文件和链接两行。两边都有一个叫 Docs 的类型（一份 .docx
              和一个 Google Docs 链接），平铺的话就是两个一模一样的按钮，
              用户只会以为出了 bug —— 侧边栏也正是为这个做成两级的 */}
          {options.kinds.length > 0 && (
          <section>
            <SectionTitle>{t("filter.kinds")}</SectionTitle>
            <div className="space-y-2">
              {(["file", "link"] as Medium[]).map((m) => {
                const items = options.kinds.filter((k) => k.medium === m);
                if (items.length === 0) return null;
                return (
                  <div key={m} className="flex items-baseline gap-2">
                    <span className="w-8 shrink-0 text-xs text-neutral-400">
                      {t(m === "link" ? "filter.media.link" : "filter.media.file")}
                    </span>
                    <div className="flex flex-1 flex-wrap gap-1.5">
                      {items.map((k) => (
                        <Chip
                          key={k.key}
                          name={k.name}
                          on={draft.kinds.includes(k.key)}
                          onClick={() => patch({ kinds: toggle(draft.kinds, k.key) })}
                        />
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
          )}

          {/* 容器可能有几十个，单独给一块可滚区域，不让它把上面的条件顶出视野 */}
          <Chips
            title={t("filter.containers")}
            items={options.containers.map((c) => ({ key: c.key, name: c.name }))}
            selected={draft.containers}
            onToggle={(key) => patch({ containers: toggle(draft.containers, key) })}
            scroll
          />
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-neutral-200 p-5 dark:border-neutral-800">
          <button
            onClick={() => setDraft(EMPTY_FILTER)}
            disabled={activeCount(draft) === 0}
            className="text-sm text-neutral-500 hover:text-neutral-900 disabled:opacity-40 dark:hover:text-neutral-100"
          >
            {t("filter.clear")}
          </button>
          <div className="flex gap-2">
            <button
              onClick={onCancel}
              className="rounded-lg border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              {t("common.cancel")}
            </button>
            {/* 有改动才变实心 —— 颜色就是"这次点下去有没有后果" */}
            <button
              onClick={() => (dirty ? onApply(draft) : onCancel())}
              className={`rounded-lg px-4 py-1.5 text-sm ${
                dirty
                  ? "bg-neutral-900 text-white hover:bg-neutral-700 dark:bg-white dark:text-neutral-900"
                  : "bg-neutral-200 text-neutral-500 dark:bg-neutral-700 dark:text-neutral-300"
              }`}
            >
              {t("filter.apply")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-400">{children}</p>
  );
}

function Chips({
  title,
  items,
  selected,
  onToggle,
  scroll,
}: {
  title: string;
  items: { key: string; name: string }[];
  selected: string[];
  onToggle: (key: string) => void;
  scroll?: boolean;
}) {
  // 一个选项都没有就整块不显示 —— 一个写着"没有可选项"的空分区，
  // 除了让人怀疑是不是坏了之外没有任何用处
  if (items.length === 0) return null;
  return (
    <section>
      <SectionTitle>{title}</SectionTitle>
      <div className={`flex flex-wrap gap-1.5 ${scroll ? "max-h-40 overflow-y-auto" : ""}`}>
        {items.map((item) => (
          <Chip
            key={item.key}
            name={item.name}
            on={selected.includes(item.key)}
            onClick={() => onToggle(item.key)}
          />
        ))}
      </div>
    </section>
  );
}

function Chip({ name, on, onClick }: { name: string; on: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-2.5 py-1 text-xs transition ${
        on
          ? "border-neutral-900 bg-neutral-900 text-white dark:border-white dark:bg-white dark:text-neutral-900"
          : "border-neutral-300 text-neutral-600 hover:bg-neutral-100 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
      }`}
    >
      {name}
    </button>
  );
}
