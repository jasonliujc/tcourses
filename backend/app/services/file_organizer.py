"""文件自动分组。

设计要点：

- **每个维度是一次完整划分**。同一个维度内，一个文件恰好属于一个分组 ——
  所以某个维度下各分组的文件数相加，一定等于这门课的文件总数。没有任何维度会漏掉文件：
  匹配不上的一律落到兜底分组。这是这个功能能让人信任的前提：看到某组有 8 个文件，
  就知道确实是 8 个，不是"至少 8 个"。
- **维度之间才是多对多**。同一个文件可以既在"文档"里，又在用户手建的分组里，
  但在每个维度内部只有一个位置。
- **用户永远优先**：手动调整过的文件打 `organize_locked`，自动管道不再动它；
  自动产生的归属每轮重建，用户手建的（assigned_by=user）一律保留。
- **可扩展**：加一个维度 = 往 CLASSIFIERS 里加一个函数，只要它同样保证
  "每个文件恰好一条归属"。

早期还有 `folder:` / `week:` / `category:` 几个维度，已经全部移除 ——
按周次和按用途将由后续的高级筛选功能承担，那里能表达更复杂的条件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.file import AssignedBy, CourseFile, FileGroup, FileGroupMember, GroupKind, Medium
from app.services import links


@dataclass(frozen=True)
class Suggestion:
    rule_key: str  # 分组的稳定标识，跨同步幂等。格式 "维度:值"
    group_name: str  # 展示名
    confidence: float
    matched_rule: str  # 命中的规则名，用于界面解释
    sort_order: int = 0  # 同维度内的展示顺序，兜底分组永远排最后


@dataclass
class FileContext:
    """分类器的输入：文件本身 + 它在 Canvas 上所有出现位置的容器名。"""

    file: CourseFile
    containers: list[str] = field(default_factory=list)

    @property
    def haystack(self) -> str:
        parts = [*self.containers, self.file.canvas_folder_path or "", self.file.display_name]
        return " ".join(p for p in parts if p).lower()

    @classmethod
    def of(cls, file: CourseFile, sources: list | None = None) -> FileContext:
        names = [s.container_name for s in (sources or []) if s.container_name]
        seen: dict[str, None] = {}
        for n in names:
            seen.setdefault(n, None)
        return cls(file=file, containers=list(seen))


# ---------- 维度：文件类型 ----------

# 扩展名 -> (类型 key, 展示名)。顺序即展示顺序。
# 命名统一用英文，和侧边栏的 Canvas Files / Modules / Pages 保持一致。
FILE_KINDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("pdf", "PDF", (".pdf",)),
    ("docs", "Docs", (".doc", ".docx", ".pages", ".txt", ".md", ".rtf", ".tex", ".odt")),
    ("powerpoint", "PowerPoint", (".ppt", ".pptx", ".key", ".odp")),
    ("data", "Data", (".csv", ".xls", ".xlsx", ".tsv", ".numbers", ".json", ".ods")),
    ("pictures", "Pictures", (".png", ".jpg", ".jpeg", ".gif", ".svg", ".heic", ".webp", ".bmp")),
    ("code", "Code", (".py", ".java", ".c", ".cpp", ".ipynb", ".r", ".m", ".js", ".ts", ".sql")),
    # 叫 Zip 不叫 Archives：后者在开发者语境里指"压缩归档"，但学生读到的是
    # "存档 / 往年资料"，完全是另一个意思。这一类里装的就是压缩包
    ("zip", "Zip", (".zip", ".tar", ".gz", ".rar", ".7z")),
    ("media", "Media", (".mp4", ".mov", ".mp3", ".wav", ".m4a", ".avi", ".mkv")),
]

# 认不出扩展名时的兜底，保证每个文件都有类型
UNKNOWN_KIND = ("other", "Other")

_KIND_BY_EXT = {ext: (key, label) for key, label, exts in FILE_KINDS for ext in exts}


def file_kind(file: CourseFile) -> tuple[str, str]:
    """(类型 key, 展示名)。认不出就有兜底 —— 永远不会返回空。

    文件按扩展名分，外部链接按域名分（Video / Docs / Others，见 services/links.py）。
    两套 key 不共用命名空间：`type:` 维度要保持"每个文件恰好一条归属"，
    而"一份 .docx"和"一个 Google Docs 链接"是两回事，混进同一个分组只会让人
    以为分类出了错。
    """
    if file.medium == Medium.LINK:
        return links.link_kind(file.external_url)
    name = (file.filename or file.display_name or "").lower()
    return _KIND_BY_EXT.get(PurePosixPath(name).suffix, UNKNOWN_KIND)


def classify_type(ctx: FileContext) -> Suggestion:
    key, label = file_kind(ctx.file)
    order = next((i for i, (k, _, _) in enumerate(FILE_KINDS) if k == key), 999)
    if key.startswith("link_"):
        # 链接的三个分组排在文件类型之后，顺序 Video / Docs / Others
        order = 100 + next(
            (i for i, (k, _, _) in enumerate(links.LINK_KINDS) if k == key), len(links.LINK_KINDS)
        )
    return Suggestion(
        rule_key=f"type:{key}",
        group_name=label,
        confidence=0.5 if key != UNKNOWN_KIND[0] else 0.3,
        matched_rule="file_extension" if key != UNKNOWN_KIND[0] else "unknown_extension",
        sort_order=order,
    )


# 每个分类器对每个文件恰好返回一条建议 —— 这是"相加等于总数"的保证
CLASSIFIERS = [classify_type]

DIMENSIONS = ["type"]


def suggest_groups(ctx: FileContext) -> list[Suggestion]:
    """每个维度一条归属，顺序即维度顺序。"""
    return [classify(ctx) for classify in CLASSIFIERS]


def extract_features(ctx: FileContext, suggestions: list[Suggestion]) -> dict:
    """结构化特征存到 file.features，前端可直接展示，也方便以后做筛选。"""
    dimensions = {}
    for s in suggestions:
        dim, _, value = s.rule_key.partition(":")
        dimensions[dim] = value
    name = (ctx.file.filename or ctx.file.display_name or "").lower()
    return {
        "dimensions": dimensions,
        "ext": PurePosixPath(name).suffix,
        "containers": ctx.containers,
    }


# ---------- 落库 ----------


async def _get_or_create_group(
    db: AsyncSession, course_id: int, suggestion: Suggestion
) -> FileGroup:
    stmt = select(FileGroup).where(
        FileGroup.course_id == course_id, FileGroup.rule_key == suggestion.rule_key
    )
    group = (await db.execute(stmt)).scalar_one_or_none()
    if group is None:
        group = FileGroup(
            course_id=course_id,
            name=suggestion.group_name,
            kind=GroupKind.AUTO,
            rule_key=suggestion.rule_key,
            sort_order=suggestion.sort_order,
        )
        db.add(group)
        await db.flush()
    else:
        # 名字也要跟着规则走。自动分组的名字由 rule_key 决定，不是用户起的 ——
        # 只认 rule_key 不更新名字的话，改过展示名之后库里会永远留着旧叫法
        group.name = suggestion.group_name
        group.sort_order = suggestion.sort_order
    return group


async def _drop_retired_groups(db: AsyncSession, course_id: int) -> int:
    """删掉已下线维度的分组，以及空的自动分组。

    维度改版后旧维度（week: / category: / folder: …）的分组会留在库里，
    不清掉的话界面上会冒出一堆再也不会更新的组。
    """
    groups = (
        (
            await db.execute(
                select(FileGroup).where(
                    FileGroup.course_id == course_id, FileGroup.kind == GroupKind.AUTO
                )
            )
        )
        .scalars()
        .all()
    )
    removed = 0
    for group in groups:
        dimension = (group.rule_key or "").split(":", 1)[0]
        if dimension not in DIMENSIONS:
            await db.delete(group)
            removed += 1
            continue
        has_member = (
            await db.execute(
                select(FileGroupMember.id).where(FileGroupMember.group_id == group.id).limit(1)
            )
        ).scalar_one_or_none()
        if has_member is None:
            await db.delete(group)
            removed += 1
    return removed


async def organize_course_files(db: AsyncSession, course_id: int) -> dict:
    """对一门课重跑自动分组。用户锁定过的文件会被跳过。"""
    files = (
        (
            await db.execute(
                select(CourseFile)
                .where(CourseFile.course_id == course_id)
                # 分类器要读来源容器名，必须预加载
                .options(selectinload(CourseFile.sources))
            )
        )
        .scalars()
        .all()
    )

    organized = 0
    skipped = 0

    for file in files:
        if file.organize_locked:
            skipped += 1
            continue

        ctx = FileContext.of(file, list(file.sources))
        suggestions = suggest_groups(ctx)
        file.features = extract_features(ctx, suggestions)

        # 清掉这个文件上一轮的自动归属，保留用户手工加的
        existing = (
            (
                await db.execute(
                    select(FileGroupMember).where(FileGroupMember.file_id == file.id)
                )
            )
            .scalars()
            .all()
        )
        kept_group_ids = set()
        for m in existing:
            if m.assigned_by == AssignedBy.USER:
                kept_group_ids.add(m.group_id)
            else:
                await db.delete(m)
        await db.flush()

        for s in suggestions:
            group = await _get_or_create_group(db, course_id, s)
            if group.id in kept_group_ids:
                continue
            db.add(
                FileGroupMember(
                    group_id=group.id,
                    file_id=file.id,
                    assigned_by=AssignedBy.AUTO,
                    confidence=s.confidence,
                    matched_rule=s.matched_rule,
                )
            )
        organized += 1

    await db.flush()
    removed = await _drop_retired_groups(db, course_id)
    await db.commit()
    return {
        "course_id": course_id,
        "organized": organized,
        "skipped_locked": skipped,
        "removed_empty_groups": removed,
    }
