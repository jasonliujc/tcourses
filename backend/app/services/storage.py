"""本地文件存储。抽成一层是为了将来换 S3/R2 时只改这个模块。"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import settings

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, max_len: int = 120) -> str:
    cleaned = _UNSAFE.sub("_", name).strip(". ")
    return (cleaned or "untitled")[:max_len]


def course_dir(course_canvas_id: int, course_name: str) -> Path:
    return settings.storage_path / f"{course_canvas_id}_{safe_name(course_name, 60)}"


def file_path(course_canvas_id: int, course_name: str, file_canvas_id: int, filename: str) -> Path:
    """文件名前缀 canvas id，避免同名文件互相覆盖。"""
    return course_dir(course_canvas_id, course_name) / f"{file_canvas_id}_{safe_name(filename)}"


def relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(settings.storage_path))
    except ValueError:
        return str(path)


def remove(relative_path: str) -> None:
    """删掉一个本地副本。删记录时顺手清盘，否则 storage/ 只增不减。"""
    target = settings.storage_path / relative_path
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
