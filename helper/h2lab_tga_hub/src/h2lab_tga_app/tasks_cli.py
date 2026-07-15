from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
from typing import Sequence

from h2lab_tga_app.config.paths import PathResolver
from h2lab_tga_app.config.settings import Settings, load_settings
from h2lab_tga_app.domain.tasks import FeedbackTask, TaskSeverity, TaskStatus
from h2lab_tga_app.infra.task_store import SQLiteTaskStore
from h2lab_tga_app.services.task_service import TaskService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect tester feedback tasks.")
    parser.add_argument("--db", type=str, default="", help="Explicit path to tasks sqlite file.")
    parser.add_argument(
        "--data-root",
        type=str,
        default="",
        help="Optional selected data root used to resolve default tasks DB path.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default="",
        help="Legacy project folder under H2LAB_SHAREPOINT_PATH for DB path resolution.",
    )
    sub = parser.add_subparsers(dest="cmd")

    list_parser = sub.add_parser("list", help="List tasks")
    list_parser.add_argument("--status", type=str, default="")
    list_parser.add_argument("--severity", type=str, default="")
    list_parser.add_argument("--view", type=str, default="")
    list_parser.add_argument("--since", type=str, default="")
    list_parser.add_argument("--limit", type=int, default=200)
    list_parser.add_argument("--format", choices=("table", "json"), default="table")
    list_parser.add_argument("--csv", type=str, default="", help="Optional CSV export path.")

    sub.add_parser("stats", help="Show task counts grouped by status and severity")
    return parser


def _initial_folder() -> str:
    for key in ("H2LAB_SHAREPOINT_PATH", "SHAREPOINT_PATH", "H2LAB_SHAREPOINT"):
        value = os.getenv(key)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
    return str(Path.cwd())


def _pick_data_root_interactive() -> str:
    from PySide6.QtWidgets import QApplication, QFileDialog

    QApplication.instance() or QApplication(sys.argv)
    return QFileDialog.getExistingDirectory(
        None,
        "Select Project/Data Folder",
        _initial_folder(),
    )


def _resolve_db_path(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db).expanduser().resolve()
    if args.data_root:
        root = Path(args.data_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"Selected data root does not exist: {root}")
        settings = Settings(sharepoint_root=root, project_rel_path=root.name, selected_data_root=root)
    else:
        settings = load_settings(project_rel_path=args.project or None)
    resolver = PathResolver(settings)
    resolver.ensure_layout()
    return resolver.tasks_db_path()


def _parse_status(raw: str) -> TaskStatus | None:
    text = raw.strip().upper()
    return TaskStatus(text) if text else None


def _parse_severity(raw: str) -> TaskSeverity | None:
    text = raw.strip().upper()
    return TaskSeverity(text) if text else None


def _task_to_dict(task: FeedbackTask) -> dict:
    return {
        "id": task.id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "status": task.status.value,
        "severity": task.severity.value,
        "view_name": task.view_name,
        "tab_name": task.tab_name,
        "widget_class": task.widget_class,
        "widget_object_name": task.widget_object_name,
        "ui_location_path": task.ui_location_path,
        "window_title": task.window_title,
        "selected_folder": task.selected_folder,
        "app_version": task.app_version,
        "comment_text": task.comment_text,
    }


def _print_table(tasks: list[FeedbackTask]) -> None:
    headers = ["ID", "Created", "Status", "Severity", "View", "Location", "Comment"]
    print(" | ".join(headers))
    print("-" * 120)
    for task in tasks:
        comment = task.comment_text.replace("\n", " ").strip()
        if len(comment) > 64:
            comment = comment[:61] + "..."
        location = task.ui_location_path
        if len(location) > 48:
            location = "..." + location[-45:]
        print(
            " | ".join(
                [
                    str(task.id),
                    task.created_at.strftime("%Y-%m-%d %H:%M"),
                    task.status.value,
                    task.severity.value,
                    task.view_name,
                    location,
                    comment,
                ]
            )
        )


def _write_csv(path: Path, tasks: list[FeedbackTask]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_task_to_dict(task) for task in tasks]
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.cmd:
        args.cmd = "list"
        if not args.db and not args.data_root and not args.project:
            selected = _pick_data_root_interactive()
            if not selected:
                print("No folder selected.")
                return 0
            args.data_root = selected
        _ensure_list_defaults(args)

    try:
        db_path = _resolve_db_path(args)
    except RuntimeError as exc:
        print(str(exc))
        return 1

    service = TaskService(SQLiteTaskStore(db_path))

    if args.cmd == "stats":
        print(json.dumps(service.stats(), indent=2))
        return 0

    try:
        status = _parse_status(args.status)
        severity = _parse_severity(args.severity)
    except ValueError as exc:
        print(f"Invalid filter value: {exc}")
        return 1

    tasks = service.list_tasks(
        status=status,
        severity=severity,
        view_name=args.view.strip() or None,
        since_iso=args.since.strip() or None,
        limit=args.limit,
    )

    if args.format == "json":
        print(json.dumps([_task_to_dict(task) for task in tasks], indent=2))
    else:
        _print_table(tasks)

    if args.csv:
        out = Path(args.csv).expanduser().resolve()
        _write_csv(out, tasks)
        print(f"\nCSV written: {out}")
    return 0


def _ensure_list_defaults(args: argparse.Namespace) -> None:
    defaults = {
        "status": "",
        "severity": "",
        "view": "",
        "since": "",
        "limit": 200,
        "format": "table",
        "csv": "",
    }
    for key, value in defaults.items():
        if not hasattr(args, key):
            setattr(args, key, value)


if __name__ == "__main__":
    raise SystemExit(main())
