from __future__ import annotations

from importlib import metadata
from pathlib import Path

from PySide6.QtCore import QObject, QEvent, QPoint
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from h2lab_tga_app.desktop.feedback.dialog import FeedbackCommentDialog
from h2lab_tga_app.desktop.feedback.location import resolve_ui_location
from h2lab_tga_app.domain.tasks import TaskCreateInput
from h2lab_tga_app.services.task_service import TaskService


class FeedbackEventFilter(QObject):
    def __init__(self, task_service: TaskService, selected_folder: str, parent=None) -> None:
        super().__init__(parent)
        self._task_service = task_service
        self._selected_folder = selected_folder
        self._enabled = True
        self._app_version = _get_app_version()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if not self._enabled:
            return False
        if event.type() != QEvent.Type.ContextMenu:
            return False
        if not isinstance(obj, QWidget):
            return False
        if self._is_internal_feedback_widget(obj) or self._is_web_engine_widget(obj):
            return False

        pos = QPoint(-1, -1)
        if hasattr(event, "pos"):
            pos = event.pos()

        location = resolve_ui_location(obj, pos)
        dialog = FeedbackCommentDialog(parent=obj.window())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return True

        payload = dialog.result_payload()
        try:
            task = self._task_service.create_task(
                TaskCreateInput(
                    comment_text=payload.comment_text,
                    severity=payload.severity,
                    status=payload.status,
                    ui_location_path=location.ui_location_path,
                    view_name=location.view_name,
                    widget_class=location.widget_class,
                    widget_object_name=location.widget_object_name,
                    tab_name=location.tab_name,
                    window_title=location.window_title,
                    selected_folder=self._selected_folder,
                    app_version=self._app_version,
                )
            )
        except Exception as exc:
            QMessageBox.critical(obj.window(), "Task Save Failed", str(exc))
            return True

        QMessageBox.information(obj.window(), "Task Saved", f"Saved feedback task #{task.id}.")
        return True

    @staticmethod
    def _is_internal_feedback_widget(widget: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if bool(current.property("_feedback_internal")):
                return True
            current = current.parentWidget()
        return False

    @staticmethod
    def _is_web_engine_widget(widget: QWidget) -> bool:
        class_name = widget.metaObject().className()
        return class_name.startswith("QWebEngine")


def _get_app_version() -> str:
    try:
        return metadata.version("h2lab-tga-app")
    except metadata.PackageNotFoundError:
        return "unknown"
    except Exception:
        return "unknown"


def tasks_folder_from_db_path(db_path: Path) -> Path:
    return db_path.parent
