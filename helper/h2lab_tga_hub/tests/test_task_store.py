from pathlib import Path

from h2lab_tga_app.domain.tasks import TaskCreateInput, TaskSeverity, TaskStatus
from h2lab_tga_app.infra.task_store import SQLiteTaskStore


def _payload(comment: str = "test task", *, severity: TaskSeverity = TaskSeverity.MEDIUM) -> TaskCreateInput:
    return TaskCreateInput(
        comment_text=comment,
        severity=severity,
        status=TaskStatus.OPEN,
        ui_location_path="MainWindow/Data Catalog/QTableWidget[row=0,col=0]",
        view_name="Data Catalog",
        widget_class="QTableWidget",
        widget_object_name="",
        tab_name="Data Catalog",
        window_title="H2Lab TGA Desktop App - PUB_25_9",
        selected_folder=r"C:\data\PUB_25_9",
        app_version="0.1.0",
    )


def test_task_store_roundtrip_and_filters(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.sqlite")
    created = store.create_task(_payload())
    assert created.id > 0
    assert created.comment_text == "test task"

    store.create_task(_payload("critical issue", severity=TaskSeverity.CRITICAL))

    all_tasks = store.list_tasks()
    assert len(all_tasks) == 2

    filtered = store.list_tasks(severity=TaskSeverity.CRITICAL)
    assert len(filtered) == 1
    assert filtered[0].comment_text == "critical issue"


def test_task_store_stats(tmp_path: Path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.sqlite")
    store.create_task(_payload("one", severity=TaskSeverity.LOW))
    store.create_task(_payload("two", severity=TaskSeverity.HIGH))
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["by_severity"]["LOW"] == 1
    assert stats["by_severity"]["HIGH"] == 1

