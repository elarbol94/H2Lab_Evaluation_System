from __future__ import annotations

import os
from pathlib import Path
import webbrowser

from PySide6.QtCore import QUrl
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget

from h2lab_tga_app.desktop.context import build_context
from h2lab_tga_app.desktop.feedback.event_filter import FeedbackEventFilter, tasks_folder_from_db_path
from h2lab_tga_app.desktop.views.catalog_view import CatalogView
from h2lab_tga_app.desktop.views.process_view import ProcessView
from h2lab_tga_app.desktop.views.visualize_view import VisualizeView


def _picker_default_folder() -> str:
    for key in ("H2LAB_SHAREPOINT_PATH", "SHAREPOINT_PATH", "H2LAB_SHAREPOINT"):
        value = os.getenv(key)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
    return str(Path.cwd())


class MainWindow(QMainWindow):
    def __init__(self, ctx: dict, selected_folder: str) -> None:
        super().__init__()
        self.ctx = ctx
        self._selected_folder = str(Path(selected_folder).resolve())
        self._feedback_filter: FeedbackEventFilter | None = None
        self._set_folder(self._selected_folder, ctx)
        self._build_menu()
        self._install_feedback_capture()

    def _build_tabs(self, ctx: dict) -> QTabWidget:
        tabs = QTabWidget()
        tabs.addTab(CatalogView(ctx), "Data Catalog")
        tabs.addTab(ProcessView(ctx), "Process Run")
        tabs.addTab(VisualizeView(ctx), "Visualize")
        return tabs

    def _set_folder(self, selected_folder: str, ctx: dict) -> None:
        self.ctx = ctx
        self._selected_folder = str(Path(selected_folder).resolve())
        folder_name = Path(self._selected_folder).name or self._selected_folder
        self.setWindowTitle(f"H2Lab TGA Desktop App - {folder_name}")
        self.setToolTip(self._selected_folder)
        self.setCentralWidget(self._build_tabs(ctx))

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        switch_action = file_menu.addAction("Switch Folder")
        switch_action.triggered.connect(self.switch_folder)

        tools_menu = self.menuBar().addMenu("Tools")
        open_tasks_action = tools_menu.addAction("Open Tasks Folder")
        open_tasks_action.triggered.connect(self.open_tasks_folder)
        self._toggle_feedback_action = tools_menu.addAction("Disable Right-Click Feedback")
        self._toggle_feedback_action.triggered.connect(self.toggle_feedback_capture)

    def _install_feedback_capture(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if self._feedback_filter is not None:
            app.removeEventFilter(self._feedback_filter)
        self._feedback_filter = FeedbackEventFilter(
            task_service=self.ctx["task_service"],
            selected_folder=self._selected_folder,
            parent=self,
        )
        app.installEventFilter(self._feedback_filter)

    def toggle_feedback_capture(self) -> None:
        if self._feedback_filter is None:
            return
        enabled = self._toggle_feedback_action.text().startswith("Enable")
        self._feedback_filter.set_enabled(enabled)
        self._toggle_feedback_action.setText(
            "Disable Right-Click Feedback" if enabled else "Enable Right-Click Feedback"
        )

    def open_tasks_folder(self) -> None:
        resolver = self.ctx.get("resolver")
        if resolver is None:
            return
        folder = tasks_folder_from_db_path(resolver.tasks_db_path())
        folder.mkdir(parents=True, exist_ok=True)
        webbrowser.open(QUrl.fromLocalFile(str(folder)).toString())

    def switch_folder(self) -> None:
        selected_folder = QFileDialog.getExistingDirectory(
            self,
            "Select Data Folder",
            _picker_default_folder(),
        )
        if not selected_folder:
            return

        selected_folder = str(Path(selected_folder).resolve())
        if selected_folder == self._selected_folder:
            return

        try:
            new_ctx = build_context(selected_data_root=selected_folder)
        except Exception as exc:
            QMessageBox.critical(self, "Folder Switch Failed", str(exc))
            return

        self._set_folder(selected_folder, new_ctx)
        self._install_feedback_capture()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        app = QApplication.instance()
        if app is not None and self._feedback_filter is not None:
            app.removeEventFilter(self._feedback_filter)
        super().closeEvent(event)
