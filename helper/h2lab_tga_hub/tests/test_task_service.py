from pathlib import Path

import pytest

from h2lab_tga_app.domain.tasks import TaskCreateInput, TaskSeverity, TaskStatus
from h2lab_tga_app.infra.task_store import SQLiteTaskStore
from h2lab_tga_app.services.task_service import TaskService


def _payload(comment: str = "needs better labels") -> TaskCreateInput:
    return TaskCreateInput(
        comment_text=comment,
        severity=TaskSeverity.MEDIUM,
        status=TaskStatus.OPEN,
        ui_location_path="MainWindow/Visualize/QLineEdit",
        view_name="Visualize",
        widget_class="QLineEdit",
        widget_object_name="",
        tab_name="Visualize",
        window_title="H2Lab TGA Desktop App - TEST",
        selected_folder=r"C:\data\TEST",
        app_version="0.1.0",
    )


def test_task_service_rejects_empty_comment(tmp_path: Path) -> None:
    service = TaskService(SQLiteTaskStore(tmp_path / "tasks.sqlite"))
    with pytest.raises(ValueError):
        service.create_task(_payload("   "))


def test_task_service_trims_comment(tmp_path: Path) -> None:
    service = TaskService(SQLiteTaskStore(tmp_path / "tasks.sqlite"))
    task = service.create_task(_payload("  add tooltip here  "))
    assert task.comment_text == "add tooltip here"

