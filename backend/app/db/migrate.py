"""轻量迁移。

第一阶段用 `create_all` 建表 —— 它能建新表，但不会给已有表加列、也不会改索引。
模型还在快速变动，为每次加字段生成一份 alembic 迁移太重，所以这里做三件很小的事：
补列、修索引、必要时重建一张表。每一步都幂等，启动时无脑跑一遍。

模型稳定后换成 alembic，把这个文件删掉。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# (表名, 列名, SQLite 列定义)
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("courses", "is_concluded", "BOOLEAN NOT NULL DEFAULT 0"),
    ("courses", "last_sync_failed", "BOOLEAN NOT NULL DEFAULT 0"),
    ("courses", "last_sync_error", "VARCHAR(500)"),
    # 老库里已有的课都是用户当时选择在同步的，默认 1 正好保持原状；
    # 之后新发现的课由 sync_courses 按"在读才默认开"来定
    ("courses", "sync_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    # 边栏里的手动顺序。0 表示没排过 —— 老库里所有课都是这个值，正好维持字母序
    ("courses", "sidebar_sort_order", "INTEGER NOT NULL DEFAULT 0"),
    ("canvas_items", "content_fetched_at", "DATETIME"),
    ("course_files", "metadata_fetched_at", "DATETIME"),
    ("course_files", "display_date", "DATETIME"),
    ("course_files", "is_inline_image", "BOOLEAN NOT NULL DEFAULT 0"),
    # 外部链接支持：身份从 canvas_id 换成 resource_key，行分文件和链接两种
    ("course_files", "resource_key", "VARCHAR(300) NOT NULL DEFAULT ''"),
    ("course_files", "medium", "VARCHAR(10) NOT NULL DEFAULT 'file'"),
    ("course_files", "external_url", "VARCHAR(2000)"),
    # 板块覆盖报告：{板块: 状态/条数}，塞不进 error_message 那种自由文本
    ("sync_states", "payload", "JSON"),
    # 用户专门点开过某一份资料的时刻。老库里全是 NULL —— 那正是"没专门看过"，
    # 按课程的水位线（course_view_states，新表由 create_all 建）照常生效
    ("user_files", "seen_at", "DATETIME"),
    # 课程颜色：Canvas 里的默认 + 用户自己改的
    ("courses", "canvas_color", "VARCHAR(20)"),
    ("courses", "color", "VARCHAR(20)"),
    # 日历与待办：用户能改时间 / 地点、标重点；改过的条目 Canvas 删了也留着
    ("planner_items", "deleted_on_canvas", "BOOLEAN NOT NULL DEFAULT 0"),
    ("planner_overlays", "start_at", "DATETIME"),
    ("planner_overlays", "end_at", "DATETIME"),
    ("planner_overlays", "location", "VARCHAR(500)"),
    ("planner_overlays", "important", "BOOLEAN NOT NULL DEFAULT 0"),
]

# 老库里 resource_key 是空的，按老的身份回填：那时候一行就是一个 Canvas 文件。
# 必须在建唯一索引之前跑，否则所有老行的 '' 会互相撞。
_BACKFILLS: list[tuple[str, str]] = [
    (
        "course_files",
        "UPDATE course_files SET resource_key = 'file:' || canvas_id "
        "WHERE resource_key IS NULL OR resource_key = ''",
    ),
]

# (索引名, 建表语句)。先按名字删掉同名的旧索引再建 —— canvas_id 上原来那个是
# **唯一**索引，而同一个 Canvas 文件被两门课引用是完全合法的（原来会让文件"跳课"），
# 链接更是压根没有 canvas_id。
_INDEX_FIXES: list[tuple[str, str, bool]] = [
    (
        "ix_course_files_canvas_id",
        "CREATE INDEX ix_course_files_canvas_id ON course_files (canvas_id)",
        True,  # 需要先 DROP：老库里它是 UNIQUE 的
    ),
    (
        "uq_course_file_resource",
        "CREATE UNIQUE INDEX uq_course_file_resource ON course_files (course_id, resource_key)",
        False,
    ),
    (
        "ix_course_files_resource_key",
        "CREATE INDEX ix_course_files_resource_key ON course_files (resource_key)",
        False,
    ),
    (
        "ix_course_files_medium",
        "CREATE INDEX ix_course_files_medium ON course_files (medium)",
        False,
    ),
]


async def _columns(conn: AsyncConnection, table: str) -> set[str]:
    rows = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {r[1] for r in rows}


async def _index_sql(conn: AsyncConnection, name: str) -> str | None:
    row = (
        await conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"), {"n": name}
        )
    ).first()
    return None if row is None else (row[0] or "")


async def _rebuild_user_files(conn: AsyncConnection) -> None:
    """user_files 的锚点从 canvas_file_id（整数）换成 resource_key（字符串）。

    外部链接没有 Canvas 文件 id，而老表上的 `UNIQUE(course_id, canvas_file_id)`
    是**表级约束**——SQLite 改不了，只能重建。这张表很小（用户星标/分组的条数），
    而且只有 user_group_members 一个外键指过来，重建时保留 id 就不会断。

    course_files 那边不用重建：它的唯一性原来是一个独立索引，删掉就行。
    """
    columns = await _columns(conn, "user_files")
    if not columns or "resource_key" in columns:
        return  # 表还没建（create_all 会按最新模型建），或者已经迁过了

    logger.info("重建 user_files：锚点 canvas_file_id → resource_key")

    # **先把子表整个抄到内存里。**
    #
    # `DROP TABLE user_files` 会顺着 ON DELETE CASCADE 把 user_group_members
    # 清空 —— 那是用户手建分组里的成员，是这个产品里最不能丢的数据。
    # 按文档 `PRAGMA foreign_keys=OFF` 能挡住，但它在事务里是 no-op，而这里到底
    # 在不在事务里取决于调用方；实测在真实库上它**没有**生效，4 条成员全没了。
    # 所以不赌 PRAGMA 的行为：存下来，重建完原样塞回去。
    members = (
        await conn.execute(text("SELECT id, group_id, user_file_id FROM user_group_members"))
    ).all()
    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    await conn.execute(
        text(
            """
            CREATE TABLE user_files_new (
                id INTEGER NOT NULL PRIMARY KEY,
                course_id INTEGER NOT NULL,
                resource_key VARCHAR(300) NOT NULL,
                file_id INTEGER,
                display_name VARCHAR(500) NOT NULL,
                is_starred BOOLEAN NOT NULL,
                linked_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                CONSTRAINT uq_user_file UNIQUE (course_id, resource_key),
                FOREIGN KEY(course_id) REFERENCES courses (id) ON DELETE CASCADE,
                FOREIGN KEY(file_id) REFERENCES course_files (id) ON DELETE SET NULL
            )
            """
        )
    )
    # id 必须原样带过去：user_group_members.user_file_id 指着它
    await conn.execute(
        text(
            """
            INSERT INTO user_files_new
                (id, course_id, resource_key, file_id, display_name,
                 is_starred, linked_at, created_at, updated_at)
            SELECT id, course_id, 'file:' || canvas_file_id, file_id, display_name,
                   is_starred, linked_at, created_at, updated_at
            FROM user_files
            """
        )
    )
    await conn.execute(text("DROP TABLE user_files"))
    await conn.execute(text("ALTER TABLE user_files_new RENAME TO user_files"))
    for ddl in (
        "CREATE INDEX ix_user_files_course_id ON user_files (course_id)",
        "CREATE INDEX ix_user_files_resource_key ON user_files (resource_key)",
        "CREATE INDEX ix_user_files_file_id ON user_files (file_id)",
        "CREATE INDEX ix_user_files_is_starred ON user_files (is_starred)",
    ):
        await conn.execute(text(ddl))

    # 把成员塞回去。id 原样保留，重复的忽略（PRAGMA 真生效时它们本来就还在）
    for member_id, group_id, user_file_id in members:
        await conn.execute(
            text(
                "INSERT OR IGNORE INTO user_group_members (id, group_id, user_file_id) "
                "VALUES (:i, :g, :u)"
            ),
            {"i": member_id, "g": group_id, "u": user_file_id},
        )
    restored = (
        await conn.execute(text("SELECT count(*) FROM user_group_members"))
    ).scalar()
    logger.info("user_files 重建完成，分组成员 %s/%s 条", restored, len(members))
    await conn.execute(text("PRAGMA foreign_keys=ON"))


async def ensure_columns(conn: AsyncConnection) -> None:
    for table, column, ddl in _ADDED_COLUMNS:
        existing = await _columns(conn, table)
        if not existing:
            continue  # 表还不存在，create_all 会按最新模型建
        if column in existing:
            continue
        logger.info("补齐列 %s.%s", table, column)
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))

    for table, sql in _BACKFILLS:
        if await _columns(conn, table):
            await conn.execute(text(sql))

    if await _columns(conn, "course_files"):
        for name, ddl, drop_first in _INDEX_FIXES:
            current = await _index_sql(conn, name)
            if current is not None and (not drop_first or "UNIQUE" not in (current or "").upper()):
                continue  # 已经是想要的样子
            if current is not None:
                logger.info("重建索引 %s", name)
                await conn.execute(text(f"DROP INDEX {name}"))
            await conn.execute(text(ddl))

    await _rebuild_user_files(conn)
