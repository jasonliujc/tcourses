import { DeletedBadge, StarIcon, TagBadges, TypeBadge } from "@/components/FileList";
import { formatSize } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { CourseFile } from "@/types";

/** 和侧边栏、列表标签用同一套词 */
const SOURCE_LABELS: Record<string, string> = {
  files: "Canvas Files",
  module: "Canvas Modules",
  page: "Canvas Pages",
  announcement: "Canvas Announcements",
  assignment: "Canvas Assignments",
  syllabus: "Canvas Syllabus",
  quiz: "Canvas Quizzes",
  nav: "Canvas Nav",
};

/** 文件详情。列表里只显示主来源，这里把**所有**来源都摊开 ——
 *  一个文件同时挂在三个地方是常态，只看第一个会误以为它只在那儿。 */
export default function FileDetail({
  file,
  onClose,
}: {
  file: CourseFile;
  onClose: () => void;
}) {
  // 链接和文件的详情要问的问题不一样：文件问"多大、下了没"，
  // 链接问"指到哪儿"。同一个弹窗，按载体挑该显示的行
  const { t, formatDate } = useI18n();
  const isLink = file.medium === "link";
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b border-neutral-200 p-5 dark:border-neutral-800">
          <h3 className="flex items-start gap-2 pr-8 text-lg font-semibold leading-snug">
            <span className="mt-1 shrink-0">
              <StarIcon filled={file.is_starred} />
            </span>
            <span className="min-w-0">{file.display_name}</span>
          </h3>
          {/* 板块和类型**分两行**。一个说"它在课程的哪一栏"，一个说"它本身是什么"，
              两串徽章挤在同一行里会被读成同一组标签 —— 尤其是一个文件属于好几个
              板块的时候，最后那个类型胶囊看着就像第四个板块 */}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
            <TagBadges tags={file.tags} />
            {file.is_deleted_on_canvas && <DeletedBadge />}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-neutral-500">
            <TypeBadge medium={file.medium} label={file.kind_label} />
            {!isLink && <span>{formatSize(file.size_bytes)}</span>}
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <dl className="space-y-3 text-sm">
            {isLink ? (
              /* 链接的身份就是这个 URL，所以它排第一 —— 用户要确认的是
                 "这条链接指到哪儿"，而不是"它叫什么" */
              <Row label={t("detail.linkUrl")}>
                <a
                  href={file.external_url ?? "#"}
                  target="_blank"
                  rel="noreferrer noopener"
                  title={file.external_url ?? ""}
                  className="line-clamp-2 break-all text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
                >
                  {file.external_url}
                </a>
              </Row>
            ) : null}
            <Row label={isLink ? t("detail.linkType") : t("detail.fileType")}>
              {file.kind_label}
              {file.content_type && (
                <span className="ml-2 text-xs text-neutral-400">{file.content_type}</span>
              )}
            </Row>
            {/* 链接没有自己的 canvas_updated_at，它的时间就是承载它那个页面的
                更新时间（source_date）—— 列表行显示的也是这个值，两处要一致 */}
            <Row label={t("detail.updatedAt")}>
              {formatDate(file.canvas_updated_at ?? file.source_date)}
            </Row>
            {file.canvas_folder_path && (
              <Row label={t("detail.canvasFolder")}>{file.canvas_folder_path}</Row>
            )}
            {/* 链接没有本体可下，这一行对它没有意义 */}
            {!isLink && (
              <Row label={t("detail.localCopy")}>
                {file.local_path ? (
                  <>
                    {t("detail.downloaded")}{" "}
                    <span className="text-neutral-400">· {formatDate(file.downloaded_at)}</span>
                  </>
                ) : (
                  <span className="text-neutral-400">{t("detail.notDownloaded")}</span>
                )}
              </Row>
            )}
            {/* 用户自己的整理结果也是文件的一部分信息，和四要素并列 */}
            <Row label={t("detail.myGroups")}>
              {file.user_groups.length > 0 ? (
                <span className="flex flex-wrap gap-1">
                  {file.user_groups.map((g) => (
                    <span
                      key={g.id}
                      className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs dark:bg-neutral-800"
                    >
                      {g.name}
                    </span>
                  ))}
                </span>
              ) : (
                <span className="text-neutral-400">{t("detail.noGroups")}</span>
              )}
            </Row>
          </dl>

          {file.is_deleted_on_canvas && (
            <p className="mt-4 rounded-lg bg-red-50 p-3 text-xs leading-relaxed text-red-700 dark:bg-red-950/30 dark:text-red-300">
              {t("detail.deletedNote")}
            </p>
          )}

          <h4 className="mb-2 mt-6 text-sm font-medium">
            {t("detail.locations")}
            <span className="ml-2 text-xs font-normal text-neutral-400">
              {t("detail.locationCount", { n: file.sources.length })}
            </span>
          </h4>
          <ul className="space-y-2">
            {file.sources.map((s) => (
              <li
                key={s.id}
                className="rounded-lg border border-neutral-200 p-3 text-sm dark:border-neutral-800"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs text-neutral-400">
                      {SOURCE_LABELS[s.source_kind] ?? s.source_kind}
                    </p>
                    <p className="mt-0.5 break-words">{s.label || s.container_name || "—"}</p>
                    {s.item_title && s.item_title !== s.label && (
                      <p className="mt-0.5 text-xs text-neutral-500">{s.item_title}</p>
                    )}
                    {s.source_date && (
                      <p className="mt-0.5 text-xs text-neutral-400">{formatDate(s.source_date)}</p>
                    )}
                  </div>
                  {(s.html_url || file.canvas_url) && (
                    <a
                      href={s.html_url || file.canvas_url || undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="shrink-0 text-xs text-blue-600 hover:underline dark:text-blue-400"
                    >
                      {t("detail.openInCanvas")}
                    </a>
                  )}
                </div>
              </li>
            ))}
            {file.sources.length === 0 && (
              <li className="text-sm text-neutral-400">{t("detail.noSources")}</li>
            )}
          </ul>
        </div>

        <footer className="flex justify-end gap-2 border-t border-neutral-200 p-4 dark:border-neutral-800">
          <button
            onClick={onClose}
            className="rounded-lg border border-neutral-300 px-4 py-1.5 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-800"
          >
            {t("common.close")}
          </button>
          {/* 和列表行一样：文件是下载，链接是打开。链接走 /download 只会拿到 400 */}
          {!file.is_deleted_on_canvas && (
            <a
              href={isLink ? (file.external_url ?? "#") : `/api/v1/files/${file.id}/download`}
              target="_blank"
              rel="noreferrer noopener"
              className={`rounded-lg px-4 py-1.5 text-sm font-medium text-white ${
                isLink
                  ? "bg-sky-600 hover:bg-sky-500 dark:bg-sky-500 dark:hover:bg-sky-400"
                  : "bg-neutral-900 hover:bg-neutral-700 dark:bg-white dark:text-neutral-900 dark:hover:bg-neutral-200"
              }`}
            >
              {isLink ? t("files.open") : t("files.download")}
            </a>
          )}
        </footer>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <dt className="w-24 shrink-0 text-neutral-500">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}
