from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from h2lab_tga_app.desktop.widgets.plot_widget import PlotWidget
from h2lab_tga_app.desktop.widgets.subplot_editor import SubplotEditorWidget
from h2lab_tga_app.ui_common.layout import normalize_layout
from h2lab_tga_app.ui_common.plot_data import build_subplot_figure


class _FrameLoadSignals(QObject):
    finished = Signal(int, object, object)


class _FrameLoadTask(QRunnable):
    def __init__(self, token: int, processed_paths: dict[str, str]) -> None:
        super().__init__()
        self.token = token
        self.processed_paths = processed_paths
        self.signals = _FrameLoadSignals()

    def run(self) -> None:
        frames: dict[str, pd.DataFrame] = {}
        warnings: list[str] = []
        for exp_id, raw_path in self.processed_paths.items():
            file = Path(raw_path)
            if not file.exists():
                warnings.append(f"Processed file missing for {exp_id}: {file}")
                continue
            try:
                frames[exp_id] = pd.read_parquet(file)
            except Exception as exc:  # pragma: no cover
                warnings.append(f"Could not load {file}: {exc}")
        self.signals.finished.emit(self.token, frames, warnings)


class VisualizeView(QWidget):
    def __init__(self, ctx: dict, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._manifest_data: dict = {}
        self._frames: dict[str, pd.DataFrame] = {}
        self._load_token = 0
        self._thread_pool = QThreadPool.globalInstance()

        root = QVBoxLayout(self)

        run_row = QHBoxLayout()
        self.run_combo = QComboBox()
        self.refresh_runs_btn = QPushButton("Refresh Runs")
        self.load_run_btn = QPushButton("Load Run")
        run_row.addWidget(QLabel("Run"))
        run_row.addWidget(self.run_combo, 1)
        run_row.addWidget(self.refresh_runs_btn)
        run_row.addWidget(self.load_run_btn)
        root.addLayout(run_row)

        preset_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.load_preset_btn = QPushButton("Load Preset")
        self.delete_preset_btn = QPushButton("Delete Preset")
        self.preset_name = QLineEdit()
        self.save_preset_btn = QPushButton("Save Current as Preset")
        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.preset_combo)
        preset_row.addWidget(self.load_preset_btn)
        preset_row.addWidget(self.delete_preset_btn)
        preset_row.addWidget(self.preset_name, 1)
        preset_row.addWidget(self.save_preset_btn)
        root.addLayout(preset_row)

        action_row = QHBoxLayout()
        self.render_btn = QPushButton("Render")
        self.save_manifest_btn = QPushButton("Save Layout to Manifest")
        action_row.addWidget(self.render_btn)
        action_row.addWidget(self.save_manifest_btn)
        action_row.addStretch()
        root.addLayout(action_row)

        splitter = QSplitter()
        self.editor = SubplotEditorWidget()
        self.plot_widget = PlotWidget()
        splitter.addWidget(self.editor)
        splitter.addWidget(self.plot_widget)
        splitter.setSizes([420, 980])
        root.addWidget(splitter, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log)

        self.refresh_runs_btn.clicked.connect(self.refresh_runs)
        self.load_run_btn.clicked.connect(self.load_selected_run)
        self.render_btn.clicked.connect(self.render_plot)
        self.save_manifest_btn.clicked.connect(self.save_layout_to_manifest)
        self.save_preset_btn.clicked.connect(self.save_preset)
        self.load_preset_btn.clicked.connect(self.load_preset)
        self.delete_preset_btn.clicked.connect(self.delete_preset)

        self.refresh_runs()
        self.refresh_presets()

    def refresh_runs(self) -> None:
        self.run_combo.clear()
        runs_root = self.ctx["resolver"].outputs_root() / "runs"
        if not runs_root.exists():
            return
        run_dirs = sorted([p for p in runs_root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True)
        for run_dir in run_dirs:
            self.run_combo.addItem(run_dir.name)

    def refresh_presets(self) -> None:
        self.preset_combo.clear()
        presets = self.ctx["state_store"].load_layout_presets()
        self.preset_combo.addItem("(none)")
        for name in sorted(presets.keys()):
            self.preset_combo.addItem(name)

    def load_selected_run(self) -> None:
        run_id = self.run_combo.currentText().strip()
        if not run_id:
            self._log("No run selected.")
            return

        run_root = self.ctx["resolver"].outputs_root() / "runs" / run_id
        manifest_path = run_root / "manifest.json"
        if not manifest_path.exists():
            self._log(f"Manifest missing: {manifest_path}")
            return

        with manifest_path.open("r", encoding="utf-8") as f:
            self._manifest_data = json.load(f)

        processed_outputs = self._processed_outputs_from_manifest(run_root)
        if not processed_outputs:
            self._frames = {}
            self.editor.set_available_columns([])
            self.editor.set_layout_data([])
            self._log("No processed outputs listed in run manifest.")
            return

        self._load_token += 1
        token = self._load_token
        task = _FrameLoadTask(token, processed_outputs)
        task.signals.finished.connect(self._on_frames_loaded)
        self._thread_pool.start(task)
        self._log(f"Loading run {run_id} frames in background...")

    def render_plot(self) -> None:
        layout = self.editor.get_layout_data()
        fig, warnings = build_subplot_figure(layout, self._frames)
        self.plot_widget.set_html(fig.to_html(include_plotlyjs="cdn"))
        for message in warnings:
            self._log(message)

    def save_layout_to_manifest(self) -> None:
        run_id = self.run_combo.currentText().strip()
        if not run_id:
            return
        run_root = self.ctx["resolver"].outputs_root() / "runs" / run_id
        manifest_path = run_root / "manifest.json"
        if not manifest_path.exists():
            return

        self._manifest_data.setdefault("visualization", {})
        self._manifest_data["visualization"]["subplot_layout"] = self.editor.get_layout_data()
        self._manifest_data["visualization"]["available_columns"] = sorted(
            {col for frame in self._frames.values() for col in frame.columns}
        )

        with manifest_path.open("w", encoding="utf-8") as f:
            json.dump(self._manifest_data, f, indent=2)
        self._log(f"Saved layout to {manifest_path}")

    def save_preset(self) -> None:
        name = self.preset_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Preset", "Preset name is required.")
            return
        payload = {
            "subplot_layout": self.editor.get_layout_data(),
            "available_columns": sorted({col for frame in self._frames.values() for col in frame.columns}),
        }
        self.ctx["state_store"].save_layout_preset(name, payload)
        self.refresh_presets()
        self._log(f"Saved preset '{name}'.")

    def load_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name == "(none)":
            return
        presets = self.ctx["state_store"].load_layout_presets()
        payload = presets.get(name, {})
        layout = payload.get("subplot_layout", [])
        cols = sorted({col for frame in self._frames.values() for col in frame.columns})
        self.editor.set_available_columns(cols)
        self.editor.set_layout_data(normalize_layout(layout, cols))
        self._log(f"Loaded preset '{name}'.")

    def delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name == "(none)":
            return
        self.ctx["state_store"].delete_layout_preset(name)
        self.refresh_presets()
        self._log(f"Deleted preset '{name}'.")

    def _processed_outputs_from_manifest(self, run_root: Path) -> dict[str, str]:
        outputs = self._manifest_data.get("outputs", {})
        processed = outputs.get("processed", {})
        if isinstance(processed, dict) and processed:
            return {str(k): str(v) for k, v in processed.items()}

        # Backward fallback for older manifests.
        processed_dir = run_root / "processed"
        if not processed_dir.exists():
            return {}
        return {file.stem: str(file) for file in sorted(processed_dir.glob("*.parquet"))}

    def _on_frames_loaded(self, token: int, frames: object, warnings: object) -> None:
        if token != self._load_token:
            return
        self._frames = frames if isinstance(frames, dict) else {}
        cols = sorted({col for frame in self._frames.values() for col in frame.columns})
        self.editor.set_available_columns(cols)
        layout = self._manifest_data.get("visualization", {}).get("subplot_layout", [])
        self.editor.set_layout_data(normalize_layout(layout, cols))
        for warning in (warnings if isinstance(warnings, list) else []):
            self._log(warning)
        run_id = self._manifest_data.get("run_id", "<unknown>")
        self._log(f"Loaded run {run_id} with {len(self._frames)} processed frames.")

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)
