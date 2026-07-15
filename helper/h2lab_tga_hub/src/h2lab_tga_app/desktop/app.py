from __future__ import annotations

import os
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication, QFileDialog

from h2lab_tga_app.desktop.context import build_context
from h2lab_tga_app.desktop.main_window import MainWindow


def _initial_folder() -> str:
    for key in ("H2LAB_SHAREPOINT_PATH", "SHAREPOINT_PATH", "H2LAB_SHAREPOINT"):
        value = os.getenv(key)
        if not value:
            continue
        candidate = Path(value).expanduser()
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
    return str(Path.cwd())


def _pick_folder(parent=None, initial_dir: str | None = None) -> str:
    return QFileDialog.getExistingDirectory(
        parent,
        "Select Data Folder",
        initial_dir or _initial_folder(),
    )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    selected_folder = _pick_folder(initial_dir=_initial_folder())
    if not selected_folder:
        return 0

    ctx = build_context(selected_data_root=selected_folder)
    window = MainWindow(ctx, selected_folder=selected_folder)
    window.resize(1400, 900)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
