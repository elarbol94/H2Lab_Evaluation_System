from __future__ import annotations

from h2lab_tga_app.domain.tasks import FeedbackTask, TaskCreateInput, TaskSeverity, TaskStatus
from h2lab_tga_app.infra.task_store import SQLiteTaskStore


class TaskService:
    def __init__(self, store: SQLiteTaskStore) -> None:
        self._store = store

    def create_task(self, payload: TaskCreateInput) -> FeedbackTask:
        comment = payload.comment_text.strip()
        if not comment:
            raise ValueError("Comment must not be empty.")
        normalized = TaskCreateInput(
            comment_text=comment,
            severity=TaskSeverity(payload.severity),
            status=TaskStatus(payload.status),
            ui_location_path=payload.ui_location_path.strip() or "unknown",
            view_name=payload.view_name.strip() or "Unknown",
            widget_class=payload.widget_class.strip() or "QWidget",
            widget_object_name=payload.widget_object_name.strip(),
            tab_name=payload.tab_name.strip() or "Unknown",
            window_title=payload.window_title.strip(),
            selected_folder=payload.selected_folder.strip(),
            app_version=payload.app_version.strip() or "unknown",
        )
        return self._store.create_task(normalized)

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        severity: TaskSeverity | None = None,
        view_name: str | None = None,
        since_iso: str | None = None,
        limit: int = 500,
    ) -> list[FeedbackTask]:
        return self._store.list_tasks(
            status=status,
            severity=severity,
            view_name=view_name,
            since_iso=since_iso,
            limit=limit,
        )

    def stats(self) -> dict:
        return self._store.stats()

