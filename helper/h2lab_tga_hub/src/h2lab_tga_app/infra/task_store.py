from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from h2lab_tga_app.domain.tasks import FeedbackTask, TaskCreateInput, TaskSeverity, TaskStatus


class SQLiteTaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    comment_text TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ui_location_path TEXT NOT NULL,
                    view_name TEXT NOT NULL,
                    widget_class TEXT NOT NULL,
                    widget_object_name TEXT NOT NULL,
                    tab_name TEXT NOT NULL,
                    window_title TEXT NOT NULL,
                    selected_folder TEXT NOT NULL,
                    app_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_tasks_severity ON tasks(severity);
                CREATE INDEX IF NOT EXISTS idx_tasks_view_name ON tasks(view_name);
                """
            )

    def create_task(self, payload: TaskCreateInput) -> FeedbackTask:
        now = datetime.utcnow().isoformat(timespec="seconds")
        values = asdict(payload)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    created_at, updated_at, comment_text, severity, status, ui_location_path,
                    view_name, widget_class, widget_object_name, tab_name, window_title,
                    selected_folder, app_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    values["comment_text"],
                    values["severity"].value,
                    values["status"].value,
                    values["ui_location_path"],
                    values["view_name"],
                    values["widget_class"],
                    values["widget_object_name"],
                    values["tab_name"],
                    values["window_title"],
                    values["selected_folder"],
                    values["app_version"],
                ),
            )
            task_id = int(cursor.lastrowid)
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row)

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        severity: TaskSeverity | None = None,
        view_name: str | None = None,
        since_iso: str | None = None,
        limit: int = 500,
    ) -> list[FeedbackTask]:
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("status = ?")
            params.append(status.value)
        if severity is not None:
            where.append("severity = ?")
            params.append(severity.value)
        if view_name:
            where.append("view_name = ?")
            params.append(view_name)
        if since_iso:
            where.append("created_at >= ?")
            params.append(since_iso)

        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
            by_status_rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status ORDER BY status"
            ).fetchall()
            by_severity_rows = conn.execute(
                "SELECT severity, COUNT(*) AS n FROM tasks GROUP BY severity ORDER BY severity"
            ).fetchall()
        return {
            "total": total,
            "by_status": {str(row["status"]): int(row["n"]) for row in by_status_rows},
            "by_severity": {str(row["severity"]): int(row["n"]) for row in by_severity_rows},
        }

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> FeedbackTask:
        return FeedbackTask(
            id=int(row["id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            comment_text=str(row["comment_text"]),
            severity=TaskSeverity(str(row["severity"])),
            status=TaskStatus(str(row["status"])),
            ui_location_path=str(row["ui_location_path"]),
            view_name=str(row["view_name"]),
            widget_class=str(row["widget_class"]),
            widget_object_name=str(row["widget_object_name"]),
            tab_name=str(row["tab_name"]),
            window_title=str(row["window_title"]),
            selected_folder=str(row["selected_folder"]),
            app_version=str(row["app_version"]),
        )

