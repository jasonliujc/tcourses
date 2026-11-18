import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useDownloadFile, useMarkSeen } from "@/api/hooks";
import Spinner from "@/components/Spinner";
import { useToast } from "@/components/Toast";
import { apiMessage } from "@/lib/apiError";
import { useI18n } from "@/lib/i18n";
import type { CourseFile, MatchHit, Medium, TagRef, UserGroup } from "@/types";

/** 批量操作栏里所有按钮共用的尺寸。
 *
 *  星标 / 取消星标 / 加入分组 / 下载并排挤在一个小条里，高度和内边距差一点点
 *  都看得出来 —— 所以尺寸只在这里定义一次，各按钮只管自己的配色。 */
export const BAR_BTN =
  "inline-flex h-7 shrink-0 items-center justify-center gap-1 rounded-lg px-2.5 text-xs font-medium";
export const BAR_BTN_OUTLINE =
  `${BAR_BTN} border border-neutral-300 bg-white hover:bg-neutral-50` +
  " dark:border-neutral-600 dark:bg-neutral-900 dark:hover:bg-neutral-700";

/** 列表行右侧的按钮尺寸。行里比操作栏松一点，因为不挤 */
const ROW_BTN = "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg text-xs font-medium";

interface Props {
  files: CourseFile[] | undefined;
  isLoading?: boolean;
  onOpenDetail: (file: CourseFile) => void;
  onStar: (fileIds: number[], starred: boolean) => void;
  /** 勾选。**不传就没有勾选框** —— 全局资源页没有批量操作（跨课程打包下载
   *  是另一件事），留着一排点了没用的勾选框只会让人找不到那个批量操作栏 */
  selected?: Set<number>;
  onToggle?: (id: number) => void;
  /** 用户自建的分组（不含星标），「加入」下拉菜单用。
   *
   *  **不传就整块不显示**，全局资源页就是这样：跨课程的分组没有意义
   *  （"Week 3"在六门课里各指一件事），那里只有星标。 */
  groups?: UserGroup[];
  onAddToGroup?: (groupId: number, fileIds: number[]) => void;
  /** 在菜单里点已加入的分组 = 移出。要弹框确认，所以带上文件本身好写提示语 */
  onRemoveFromGroup?: (groupId: number, file: CourseFile) => void;
  /** 从「分组」菜单里点新建时，要记住是给哪些文件建的 —— 建完直接加进去 */
  onCreateGroup?: (pendingFileIds: number[]) => void;
  /** 空列表时说什么。"这里还没有资源"和"没有资源符合筛选条件"是两回事 ——
   *  后者用户能自己解决，前者不能 */
  emptyText?: string;
}

/** 板块的配色。键是**侧边栏那套节点 id** —— 行上的标签、侧边栏、筛选面板
 *  必须说同一套词，否则按板块筛出来一堆标着另一个词的资料。
 *  认不出的用中性色，但一定有值：界面上不能出现空白标签 */
const TAG_STYLE: Record<string, string> = {
  modules_pages: "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
  files: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  announcements: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  assignments: "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300",
  syllabus: "bg-teal-50 text-teal-700 dark:bg-teal-950/40 dark:text-teal-300",
  quizzes: "bg-orange-50 text-orange-700 dark:bg-orange-950/40 dark:text-orange-300",
  discussions: "bg-violet-50 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300",
  others: "bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300",
};
const TAG_FALLBACK = "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300";

/** **来源**：这东西在 Canvas 的哪个位置。实心浅色块，按来源分色。
 *
 *  和右边的「类型」是两类完全不同的信息，所以形状、配色、位置三项同时拉开：
 *  来源是实心色块、贴在行首；类型是描边胶囊、贴在行尾。之前类型只是一段和
 *  时间一样的灰字，紧挨着时间，看上去就像时间戳的一部分。 */
export function TagBadge({ tag }: { tag: TagRef }) {
  return (
    <span
      className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
        TAG_STYLE[tag.id] ?? TAG_FALLBACK
      }`}
    >
      {tag.label}
    </span>
  );
}

/** 哪门课的。只在全局资源页出现。
 *
 *  做成描边而不是实心：这一行上已经有一个实心的来源标签了，再来一个
 *  同样分量的色块，"在 Canvas 的哪儿"和"哪门课的"就分不出主次。 */
export function CourseBadge({ code, name }: { code: string; name?: string | null }) {
  return (
    <span
      title={name ?? code}
      // 课程代码可能很长（`Arts_Faculty_Undergraduate_Advising_Hub`），
      // 不限宽的话它会把这一行真正要说的东西挤出视野。全名在悬停提示里
      className="max-w-[9rem] shrink-0 truncate rounded border border-neutral-300 px-1.5 py-px text-[11px] font-medium text-neutral-600 dark:border-neutral-600 dark:text-neutral-300"
    >
      {code}
    </span>
  );
}

/** 一个文件所属的全部板块。实测 1233 份资料里 1222 份只有一个、最多的三个，
 *  所以全画出来不会把行挤爆 */
export function TagBadges({ tags }: { tags: TagRef[] }) {
  return (
    <>
      {tags.map((tag) => (
        <TagBadge key={tag.id} tag={tag} />
      ))}
    </>
  );
}

function FileGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="size-3" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function LinkGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    >
      <path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" />
      <path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" />
    </svg>
  );
}

/** 命中说明前面的放大镜。一眼看出这行灰字说的是"搜索为什么选中了它" */
function SearchGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-3 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4.5-4.5" />
    </svg>
  );
}

/** **为什么这一行会出现在搜索结果里** —— 命中落在文件名以外时才有。
 *
 *  搜 "week 3" 出来一个叫 `l3.pdf` 的文件，用户第一反应是"搜错了"。
 *  多这一句「命中 模块：Week 3」就说得通了 —— 而这正是搜索要管到文件夹和
 *  页面标题的原因：教授把一整周的东西挂在一个页面下、文件本身只叫 l3.pdf。 */
function MatchHints({ hits }: { hits: MatchHit[] }) {
  const { t, tOr } = useI18n();
  return (
    <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-neutral-500">
      <SearchGlyph />
      <span className="shrink-0 text-neutral-400">{t("files.matchedIn")}</span>
      {hits.map((h) => (
        <span key={`${h.field}:${h.text}`} className="flex min-w-0 items-center gap-1">
          {/* 字段名弱化、命中的那段文本正常色 —— 要读的是后者 */}
          <span className="shrink-0 text-neutral-400">{tOr(`match.${h.field}`, h.field)}</span>
          <span className="truncate text-neutral-600 dark:text-neutral-300">{h.text}</span>
        </span>
      ))}
    </div>
  );
}

/** 「打开」按钮上的外链箭头。文字之外再给一个"会跳出去"的信号 */
function ExternalGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-3"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14 4h6v6" />
      <path d="M20 4l-8 8" />
      <path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </svg>
  );
}

/** **类型**：这东西本身是什么 —— 能下载的文件（PDF / Docs / …），
 *  还是要跳出去的外部链接。
 *
 *  描边胶囊 + 前置图标，和左边实心的来源标签一眼就分得开。链接再用蓝色单独
 *  拉开一层：用户扫一眼就该知道"点下去是下载还是跳走"，不用去读标签上的字。 */
export function TypeBadge({ medium, label }: { medium: Medium; label: string }) {
  const { t } = useI18n();
  const isLink = medium === "link";
  return (
    <span
      title={isLink ? t("files.linkTip") : t("files.fileTip")}
      // 两种载体都上色，而且是冷暖一对。之前文件是灰的、链接是蓝的，
      // 灰色读起来像"次要的、没识别出来的"，凭空给了链接更高的分量 ——
      // 它们其实是平级的两类东西
      className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-px text-[11px] font-medium ${
        isLink
          ? "border-sky-300 bg-sky-50/60 text-sky-700 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-400"
          : "border-amber-300 bg-amber-50/60 text-amber-700 dark:border-amber-800/70 dark:bg-amber-950/30 dark:text-amber-500"
      }`}
    >
      {isLink ? <LinkGlyph /> : <FileGlyph />}
      {label}
    </span>
  );
}

/** 来源路径末尾常常就是文件名本身（Modules 里的条目标题多半和文件同名）——
 *  同一行里把文件名写两遍纯属噪声，去掉尾巴只留真正说明位置的部分。
 *  条目标题和文件名不同的时候它是有信息的，那就留着。 */
export function trimSourceLabel(label: string, fileName: string): string {
  const parts = label.split(" · ");
  if (parts.length > 1 && parts[parts.length - 1] === fileName) {
    // Canvas 的模块名常带尾随空格（"Week 12 "），去掉尾巴之后就露出来了
    return parts.slice(0, -1).join(" · ").trim();
  }
  return label;
}

/** 五角星。**任何视图下都画**，标过的是实心亮的，没标的是空心灰的 ——
 *  用户要一眼看出"这个我标过"，而不是切到某个视图才知道。 */
export function StarIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={`size-4 ${filled ? "fill-amber-400 text-amber-400" : "fill-none text-neutral-300 dark:text-neutral-600"}`}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinejoin="round"
    >
      <path d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 17.4 6.2 20.5l1.1-6.5L2.6 9.4l6.5-.9z" />
    </svg>
  );
}

/** 文件在 Canvas 上已经没了，但用户整理过它，所以记录留着 */
export function DeletedBadge() {
  const { t } = useI18n();
  return (
    <span
      title={t("files.deletedTip")}
      className="shrink-0 rounded bg-red-50 px-1.5 py-0.5 text-xs font-medium text-red-600 dark:bg-red-950/40 dark:text-red-300"
    >
      {t("files.deletedBadge")}
    </span>
  );
}

/** 挂在 body 上的浮层菜单。
 *
 *  必须用 portal + fixed：菜单的触发点在文件列表里，而那个 `<ul>` 为了圆角带了
 *  `overflow-hidden`，普通的绝对定位会被直接裁掉 —— 表现就是列表底部那几行点
 *  「加入」只看得见半截菜单。顺便处理了贴近视口底部时向上翻。 */
function PopoverMenu({
  anchor,
  onClose,
  children,
}: {
  anchor: DOMRect;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const WIDTH = 208;
  const MAX_HEIGHT = 260;
  const flipUp = anchor.bottom + MAX_HEIGHT > window.innerHeight;

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) onClose();
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={box}
      style={{
        position: "fixed",
        // 右对齐触发按钮，但不许顶出左边界
        left: Math.max(8, anchor.right - WIDTH),
        top: flipUp ? undefined : anchor.bottom + 4,
        bottom: flipUp ? window.innerHeight - anchor.top + 4 : undefined,
        width: WIDTH,
        maxHeight: MAX_HEIGHT,
      }}
      className="z-[60] overflow-y-auto rounded-lg border border-neutral-200 bg-white py-1 shadow-lg dark:border-neutral-700 dark:bg-neutral-800"
    >
      {children}
    </div>,
    document.body,
  );
}

/** 「分组」按钮 + 下拉。列表行和批量操作栏共用。
 *
 *  行内用的时候是**开关**：已加入的组前面有勾，再点一次就移出。批量操作栏不给
 *  这个语义 —— 选中的一堆文件各自在不在这个组里并不一样，"再点一次"到底是加还是
 *  移根本说不清，所以那边只做加入。 */
export function AddToGroupMenu({
  groups,
  onPick,
  onRemove,
  onCreateGroup,
  label,
  buttonClass,
  disabled,
  memberOf,
}: {
  groups: UserGroup[];
  onPick: (groupId: number) => void;
  /** 传了才有"再点一次移出"的行为 */
  onRemove?: (groupId: number) => void;
  onCreateGroup: () => void;
  label?: string;
  /** 尺寸和配色由调用方给：行里和批量操作栏里要和旁边的按钮对齐 */
  buttonClass?: string;
  disabled?: boolean;
  /** 这个文件已经在哪些分组里 —— 菜单上打勾，省得用户重复点 */
  memberOf?: Set<number>;
}) {
  const { t } = useI18n();
  const [anchor, setAnchor] = useState<DOMRect | null>(null);

  return (
    <>
      <button
        onClick={(e) =>
          setAnchor(anchor ? null : (e.currentTarget as HTMLElement).getBoundingClientRect())
        }
        disabled={disabled}
        className={`disabled:opacity-50 ${
          buttonClass ??
          `${ROW_BTN} px-2.5 py-1.5 bg-neutral-100 text-neutral-700 hover:bg-neutral-200` +
            " dark:bg-neutral-800 dark:text-neutral-200 dark:hover:bg-neutral-700"
        }`}
      >
        {label ?? t("files.group")}
      </button>
      {anchor && (
        <PopoverMenu anchor={anchor} onClose={() => setAnchor(null)}>
          {groups.map((g) => {
            const joined = memberOf?.has(g.id) ?? false;
            const canToggleOff = joined && !!onRemove;
            return (
              <button
                key={g.id}
                onClick={() => {
                  if (canToggleOff) onRemove(g.id);
                  else if (!joined) onPick(g.id);
                  setAnchor(null);
                }}
                title={canToggleOff ? t("files.removeHint") : undefined}
                className="group/item flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700"
              >
                <span className="w-3 shrink-0 text-green-600">{joined ? "✓" : ""}</span>
                <span className="min-w-0 flex-1 truncate">{g.name}</span>
                {/* 这里不显示文件数。之前已加入的组用"移出"顶掉了计数，没加入的
                    显示计数 —— 同一列有的是数字有的是文字，看着就像出了错。
                    分组有多少文件是「我的分组」侧边栏要回答的问题，不是这里 */}
                {canToggleOff && (
                  <span className="hidden shrink-0 text-red-500 group-hover/item:inline">
                    {t("files.removeFromGroup")}
                  </span>
                )}
              </button>
            );
          })}
          {groups.length === 0 && (
            <p className="px-3 py-2 text-xs text-neutral-400">{t("files.noGroupsYet")}</p>
          )}
          <button
            onClick={() => {
              onCreateGroup();
              setAnchor(null);
            }}
            className="mt-1 w-full border-t border-neutral-200 px-3 py-1.5 text-left text-xs text-neutral-500 hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-700"
          >
            {t("files.newGroup")}
          </button>
        </PopoverMenu>
      )}
    </>
  );
}

/** 侧边栏分组行上的「操作」菜单：改名 / 删除合成一个入口。 */
export function GroupActionsMenu({
  onRename,
  onDelete,
}: {
  onRename: () => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  return (
    <>
      <button
        onClick={(e) => {
          e.stopPropagation();
          setAnchor(anchor ? null : (e.currentTarget as HTMLElement).getBoundingClientRect());
        }}
        className="rounded px-1.5 py-0.5 text-xs text-neutral-400 hover:bg-neutral-200 hover:text-neutral-700 dark:hover:bg-neutral-700 dark:hover:text-neutral-200"
      >
        {t("files.edit")}
      </button>
      {anchor && (
        <PopoverMenu anchor={anchor} onClose={() => setAnchor(null)}>
          <button
            onClick={() => {
              onRename();
              setAnchor(null);
            }}
            className="w-full px-3 py-1.5 text-left text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700"
          >
            {t("files.rename")}
          </button>
          <button
            onClick={() => {
              onDelete();
              setAnchor(null);
            }}
            className="w-full px-3 py-1.5 text-left text-xs text-red-600 hover:bg-neutral-100 dark:hover:bg-neutral-700"
          >
            {t("files.deleteGroup")}
          </button>
        </PopoverMenu>
      )}
    </>
  );
}

/** 行尾的主操作。文件是「下载」，外部链接是「打开」。
 *
 *  两者长得一样大、位置一样 —— 它们在这一行里是同一个角色（"我要这个东西"）。
 *  区别由左边的类型胶囊承担：链接那个是蓝色描边加链条图标，扫一眼就知道
 *  点下去是下载还是跳出去。 */
function PrimaryAction({ file }: { file: CourseFile }) {
  const { t } = useI18n();
  const markSeen = useMarkSeen();
  if (file.medium === "link") {
    return (
      <a
        href={file.external_url ?? "#"}
        target="_blank"
        rel="noreferrer noopener"
        // 点开过的链接就是"专门看过了"，不该再堵在全局「更新」里。
        // 和「下载」那一路是同一条规则（见 useDownloadFile）
        onClick={() => markSeen.mutate([file.id])}
        // 和「下载」一样大、一样的位置，但换成链接那套蓝 —— 一个是把东西存到
        // 本地，一个是把你送出这个应用，长得一模一样只会让人点错
        className={`${ROW_BTN} w-[5.5rem] px-3.5 py-1.5 bg-sky-600 text-white hover:bg-sky-500 dark:bg-sky-500 dark:hover:bg-sky-400`}
      >
        {t("files.open")}
        <ExternalGlyph />
      </a>
    );
  }
  return <DownloadButton file={file} />;
}

/** 行里的下载按钮。自己管自己的进行中状态 —— 一次可能同时下好几个文件，
 *  状态放在列表上就得维护一个 id 集合，没必要。 */
function DownloadButton({ file }: { file: CourseFile }) {
  const download = useDownloadFile();
  const toast = useToast();
  const i18n = useI18n();
  const busy = download.isPending;

  return (
    <button
      onClick={() => {
        if (busy) return;
        download.mutate(file, { onError: (err) => toast(apiMessage(err, i18n), "error") });
      }}
      disabled={busy}
      // 宽度定死：不然"下载"变"下载中"时按钮会变宽，整行跟着抖一下
      className={`${ROW_BTN} w-[5.5rem] px-3.5 py-1.5 bg-neutral-900 text-white hover:bg-neutral-700 disabled:opacity-60 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200`}
    >
      {busy && <Spinner className="size-3" />}
      {busy ? i18n.t("files.downloading") : i18n.t("files.download")}
    </button>
  );
}

export default function FileList({
  files,
  isLoading,
  selected,
  onToggle,
  onOpenDetail,
  groups,
  onStar,
  onAddToGroup,
  onRemoveFromGroup,
  onCreateGroup,
  emptyText,
}: Props) {
  const { t, dateParts } = useI18n();
  if (isLoading) {
    return <p className="py-8 text-center text-sm text-neutral-400">{t("common.loading")}</p>;
  }

  return (
    <ul className="divide-y divide-neutral-200 overflow-hidden rounded-lg border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900">
      {files?.map((f) => {
        const when = dateParts(f.source_date ?? f.canvas_updated_at);
        return (
          <li
            key={f.id}
            className="flex items-center gap-3 p-3 hover:bg-neutral-50 dark:hover:bg-neutral-800/50"
          >
            {onToggle && (
              <input
                type="checkbox"
                checked={selected?.has(f.id) ?? false}
                onChange={() => onToggle(f.id)}
                className="size-4 shrink-0"
              />
            )}

            {/* 星标就在文件名旁边，任何视图都在 —— 标记和取消都是一下的事 */}
            <button
              onClick={() => onStar([f.id], !f.is_starred)}
              title={f.is_starred ? t("files.unstar") : t("files.starToggleOn")}
              className="shrink-0"
            >
              <StarIcon filled={f.is_starred} />
            </button>

            <div className="min-w-0 flex-1">
              {/* 「详情」挪到文件名旁边，做成一个不抢眼的小链接。原来它和「分组」
                  「下载」并排挤在右边，三个同等大小的按钮让右侧非常臃肿，
                  而这三件事的分量其实差很多 */}
              <p className="flex min-w-0 items-center gap-2 text-sm font-medium">
                <span className="truncate">{f.display_name}</span>
                <button
                  onClick={() => onOpenDetail(f)}
                  className="shrink-0 text-xs font-normal text-neutral-400 underline-offset-2 hover:text-neutral-700 hover:underline dark:hover:text-neutral-200"
                >
                  {t("common.details")}
                </button>
                {f.is_deleted_on_canvas && <DeletedBadge />}
              </p>
              {/* 四要素恒定显示，但分成**两组**：
                    左 = 来源（在 Canvas 的哪儿）：实心色块 + 路径
                    右 = 是什么 + 什么时候：日期时间 + 描边的类型胶囊
                  中间用一条竖线断开。之前四项等距排在一行、类型还是和时间一样的
                  灰字，读起来是一串分不清层次的碎信息 */}
              <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-neutral-500">
                {/* 全局列表里一行必须自己说清"这是哪门课的" —— 只有全局那几个
                    接口会带 course_code，课程页里它是既定前提，不占位置 */}
                {f.course_code && <CourseBadge code={f.course_code} name={f.course_name} />}
                <TagBadges tags={f.tags} />
                <span className="min-w-0 flex-1 truncate">
                  {trimSourceLabel(f.source_label, f.display_name)}
                </span>
                {f.sources.length > 1 && (
                  <span className="shrink-0 text-neutral-400">
                    {t("files.morePlaces", { n: f.sources.length - 1 })}
                  </span>
                )}
                <span
                  aria-hidden
                  className="h-3 w-px shrink-0 bg-neutral-200 dark:bg-neutral-700"
                />
                {/* 日期正常色、时间弱化 —— 扫一眼先看到哪天的 */}
                {/* 日期 = 承载它的那个东西最后一次变动的时间。取不到就留空 ——
                    课程大纲和模块条目在 Canvas 上根本没有时间字段 */}
                <span className="shrink-0 tabular-nums">
                  <span className="text-neutral-600 dark:text-neutral-300">{when.date}</span>
                  {when.time && <span className="ml-1 text-neutral-400">{when.time}</span>}
                </span>
                <TypeBadge medium={f.medium} label={f.kind_label} />
              </div>
              {f.match_hits.length > 0 && <MatchHints hits={f.match_hits} />}
              {/* 所属的用户分组也跟着文件走，在哪个视图都看得到 */}
              {f.user_groups.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {f.user_groups.map((g) => (
                    <span
                      key={g.id}
                      className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300"
                    >
                      {g.name}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {/* 右边只留两个按钮，分量拉开：分组是浅色的，下载是实心的主操作。
                全局资源页不传 groups，那一格整个不出现 */}
            {groups && onAddToGroup && onRemoveFromGroup && onCreateGroup && (
              <AddToGroupMenu
                groups={groups}
                memberOf={new Set(f.user_groups.map((g) => g.id))}
                onPick={(gid) => onAddToGroup(gid, [f.id])}
                onRemove={(gid) => onRemoveFromGroup(gid, f)}
                onCreateGroup={() => onCreateGroup([f.id])}
              />
            )}
            {/* Canvas 上没了的东西点不动，按钮也就没有意义 */}
            {!f.is_deleted_on_canvas && <PrimaryAction file={f} />}
          </li>
        );
      })}
      {files?.length === 0 && (
        <li className="p-8 text-center text-sm text-neutral-400">
          {emptyText ?? t("files.emptyList")}
        </li>
      )}
    </ul>
  );
}
