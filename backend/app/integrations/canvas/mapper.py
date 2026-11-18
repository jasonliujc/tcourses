"""Canvas 原始 JSON → 内部模型字段的映射，以及变更检测用的内容哈希。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from dateutil import parser as date_parser

from app.models.canvas_item import ItemKind
from app.models.file import Medium
from app.services import links


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return date_parser.isoparse(str(value))
    except (ValueError, TypeError):
        return None


def same_moment(a: datetime | None, b: datetime | None) -> bool:
    """比较两个时间点是否相同，容忍时区信息的有无。

    必须这么比：SQLite 不保存时区，从库里读出来的是 naive datetime，
    而刚从 Canvas 解析出来的是 aware。直接 `!=` 永远为真 ——
    后果是每个文件每轮都被判定成"已更新"，白白重复下载、重复发提醒。
    """
    if a is None or b is None:
        return a is None and b is None
    if a.tzinfo is None:
        a = a.replace(tzinfo=UTC)
    if b.tzinfo is None:
        b = b.replace(tzinfo=UTC)
    return a == b


def _hash(*parts: Any) -> str:
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def map_course(raw: dict) -> dict:
    term = raw.get("term") or {}
    return {
        "canvas_id": int(raw["id"]),
        "name": raw.get("name") or f"Course {raw['id']}",
        "course_code": raw.get("course_code"),
        "term_name": term.get("name"),
        "enrollment_state": (raw.get("enrollments") or [{}])[0].get("enrollment_state"),
        "start_at": parse_dt(raw.get("start_at")),
        "end_at": parse_dt(raw.get("end_at")),
        "is_favorite": bool(raw.get("is_favorite")),
    }


def map_assignment(raw: dict, course_id: int) -> dict:
    return {
        "kind": ItemKind.ASSIGNMENT,
        "canvas_id": str(raw["id"]),
        "course_id": course_id,
        "title": raw.get("name") or "",
        "body_html": raw.get("description"),
        "html_url": raw.get("html_url"),
        "due_at": parse_dt(raw.get("due_at")),
        "posted_at": parse_dt(raw.get("created_at")),
        "canvas_updated_at": parse_dt(raw.get("updated_at")),
        # 哈希只覆盖"学生真正在意的变化"：标题、截止时间、分值、正文
        "content_hash": _hash(
            raw.get("name"),
            raw.get("due_at"),
            raw.get("points_possible"),
            raw.get("description"),
        ),
        "raw": raw,
    }


def map_announcement(raw: dict, course_id: int) -> dict:
    return {
        "kind": ItemKind.ANNOUNCEMENT,
        "canvas_id": str(raw["id"]),
        "course_id": course_id,
        "title": raw.get("title") or "",
        "body_html": raw.get("message"),
        "html_url": raw.get("html_url"),
        "author_name": (raw.get("author") or {}).get("display_name"),
        "posted_at": parse_dt(raw.get("posted_at")),
        "canvas_updated_at": parse_dt(raw.get("posted_at")),
        "content_hash": _hash(raw.get("title"), raw.get("message")),
        "raw": raw,
    }


def map_page(raw: dict, course_id: int) -> dict:
    return {
        "kind": ItemKind.PAGE,
        "canvas_id": str(raw.get("page_id") or raw.get("url")),
        "course_id": course_id,
        "title": raw.get("title") or "",
        "body_html": raw.get("body"),
        "html_url": raw.get("html_url"),
        "canvas_updated_at": parse_dt(raw.get("updated_at")),
        # 页面列表接口不返回 body，用 updated_at 当哈希输入即可
        "content_hash": _hash(raw.get("title"), raw.get("updated_at")),
        "raw": raw,
    }


def map_conversation(raw: dict) -> dict:
    return {
        "kind": ItemKind.MESSAGE,
        "canvas_id": str(raw["id"]),
        "course_id": None,  # 稍后按 context_code 回填
        "title": raw.get("subject") or "(无主题)",
        "body_html": raw.get("last_message"),
        "html_url": None,
        "author_name": (raw.get("participants") or [{}])[0].get("name"),
        "posted_at": parse_dt(raw.get("last_message_at")),
        "canvas_updated_at": parse_dt(raw.get("last_message_at")),
        "content_hash": _hash(
            raw.get("subject"), raw.get("last_message_at"), raw.get("message_count")
        ),
        "raw": raw,
    }


def conversation_course_canvas_id(raw: dict) -> int | None:
    """会话的 context_code 形如 "course_12345"。"""
    code = raw.get("context_code") or ""
    if code.startswith("course_"):
        try:
            return int(code.removeprefix("course_"))
        except ValueError:
            return None
    return None


def map_file(raw: dict, course_id: int, folder_path: str | None) -> dict:
    return {
        "canvas_id": int(raw["id"]),
        # 身份是 (课程, resource_key)。canvas_id 单独不再是身份 —— 同一个 Canvas
        # 文件被两门课引用时它会撞，而外部链接压根没有 canvas_id
        "resource_key": links.file_resource_key(int(raw["id"])),
        "medium": Medium.FILE,
        "course_id": course_id,
        "display_name": raw.get("display_name") or raw.get("filename") or f"file-{raw['id']}",
        "filename": raw.get("filename"),
        "content_type": raw.get("content-type") or raw.get("content_type"),
        "size_bytes": raw.get("size"),
        "canvas_folder_path": folder_path,
        "download_url": raw.get("url"),
        "canvas_updated_at": parse_dt(raw.get("updated_at") or raw.get("modified_at")),
    }


def build_folder_path_map(folders: list[dict]) -> dict[int, str]:
    """folder_id → "Lectures/Week 3"（去掉 Canvas 自带的 "course files" 前缀）。"""
    out: dict[int, str] = {}
    for f in folders:
        full = (f.get("full_name") or "").strip()
        path = full.removeprefix("course files").strip("/")
        out[int(f["id"])] = path
    return out
