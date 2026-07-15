from pathlib import Path

from h2lab_tga_app.domain.tasks import TaskCreateInput, TaskSeverity, TaskStatus
from h2lab_tga_app.infra.task_store import SQLiteTaskStore
from h2lab_tga_app.tasks_cli import main


def _seed(db: Path) -> None:
    store = SQLiteTaskStore(db)
    store.create_task(
        TaskCreateInput(
            comment_text="first task",
            severity=TaskSeverity.HIGH,
            status=TaskStatus.OPEN,
            ui_location_path="MainWindow/Process Run/QPushButton",
            view_name="Process Run",
            widget_class="QPushButton",
            widget_object_name="run_btn",
            tab_name="Process Run",
            window_title="H2Lab",
            selected_folder=str(db.parent),
            app_version="0.1.0",
        )
    )


def test_tasks_cli_list_and_stats(capsys, tmp_path: Path) -> None:
    db = tmp_path / "tasks.sqlite"
    _seed(db)

    rc_list = main(["--db", str(db), "list", "--format", "table"])
    out_list = capsys.readouterr().out
    assert rc_list == 0
    assert "first task" in out_list
    assert "HIGH" in out_list

    rc_stats = main(["--db", str(db), "stats"])
    out_stats = capsys.readouterr().out
    assert rc_stats == 0
    assert "\"total\": 1" in out_stats


def test_tasks_cli_csv_export(tmp_path: Path) -> None:
    db = tmp_path / "tasks.sqlite"
    _seed(db)
    out_csv = tmp_path / "tasks.csv"
    rc = main(["--db", str(db), "list", "--csv", str(out_csv)])
    assert rc == 0
    assert out_csv.exists()
    assert "first task" in out_csv.read_text(encoding="utf-8")


def test_tasks_cli_no_args_uses_picker(monkeypatch, capsys, tmp_path: Path) -> None:
    data_root = tmp_path / "selected"
    data_root.mkdir(parents=True)
    db = data_root / ".h2lab_tga" / "tasks" / "tasks.sqlite"
    _seed(db)

    import h2lab_tga_app.tasks_cli as tasks_cli

    monkeypatch.setattr(tasks_cli, "_pick_data_root_interactive", lambda: str(data_root))

    rc = tasks_cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "first task" in out


def test_tasks_cli_no_args_cancel_picker(monkeypatch, capsys) -> None:
    import h2lab_tga_app.tasks_cli as tasks_cli

    monkeypatch.setattr(tasks_cli, "_pick_data_root_interactive", lambda: "")
    rc = tasks_cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No folder selected." in out
