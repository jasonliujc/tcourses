interface Props {
  title: string;
  description?: string;
  children: React.ReactNode;
  /** 标题右侧的操作位 */
  action?: React.ReactNode;
}

/** 设置页统一的分区容器 —— 三个大框长得一样，内容各不相同 */
export default function Panel({ title, description, children, action }: Props) {
  return (
    <section className="overflow-hidden rounded-xl border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
      <header className="flex items-start justify-between gap-4 p-5">
        <div>
          <h2 className="font-medium">{title}</h2>
          {description && <p className="mt-1 text-xs text-neutral-400">{description}</p>}
        </div>
        {action}
      </header>
      <div className="border-t border-neutral-200 dark:border-neutral-800">{children}</div>
    </section>
  );
}
