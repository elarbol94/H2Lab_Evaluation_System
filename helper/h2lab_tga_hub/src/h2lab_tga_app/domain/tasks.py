from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TaskSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TaskStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


@dataclass(frozen=True)
class TaskCreateInput:
    comment_text: str
    severity: TaskSeverity
    status: TaskStatus
    ui_location_path: str
    view_name: str
    widget_class: str
    widget_object_name: str
    tab_name: str
    window_title: str
    selected_folder: str
    app_version: str


@dataclass(frozen=True)
class FeedbackTask:
    id: int
    created_at: datetime
    updated_at: datetime
    comment_text: str
    severity: TaskSeverity
    status: TaskStatus
    ui_location_path: str
    view_name: str
    widget_class: str
    widget_object_name: str
    tab_name: str
    window_title: str
    selected_folder: str
    app_version: str

