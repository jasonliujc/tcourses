"""集中导入所有模型，保证 Base.metadata 完整（create_all / alembic autogenerate 依赖它）。"""

from app.models.canvas_item import CanvasItem, ItemKind
from app.models.course import Course
from app.models.file import (
    AssignedBy,
    CourseFile,
    FileGroup,
    FileGroupMember,
    FileSource,
    GroupKind,
    SourceKind,
)
from app.models.planner import PlannerItem, PlannerKind, PlannerOverlay
from app.models.setting import AppSetting
from app.models.system import EventKind, NotificationEvent, SyncState
from app.models.tasks import TaskList, TaskStep, UserTask
from app.models.userdata import CourseViewState, UserFile, UserGroup, UserGroupMember

__all__ = [
    "AppSetting",
    "AssignedBy",
    "CanvasItem",
    "Course",
    "CourseFile",
    "CourseViewState",
    "EventKind",
    "FileGroup",
    "FileGroupMember",
    "FileSource",
    "GroupKind",
    "ItemKind",
    "SourceKind",
    "NotificationEvent",
    "PlannerItem",
    "PlannerKind",
    "PlannerOverlay",
    "SyncState",
    "TaskList",
    "TaskStep",
    "UserFile",
    "UserGroup",
    "UserGroupMember",
    "UserTask",
]
