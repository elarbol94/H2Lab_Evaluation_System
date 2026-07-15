from __future__ import annotations

import json

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from h2lab_tga_app.desktop.widgets.plot_widget import PlotWidget
from h2lab_tga_app.domain.models import ExperimentRef, RunConfig
from h2lab_tga_app.ui_common.plot_data import build_stage_preview_figure


class _PreviewSignals(QObject):
    finished = Signal(int, object, object, str)


class _PreviewTask(QRunnable):
    def __init__(self, token: int, processor, experiment: ExperimentRef, config_payload: dict) -> None:
        super().__init__()
        self.token = token
        self.processor = processor
        self.experiment = experiment
        self.config_payload = config_payload
        self.signals = _PreviewSignals()

    def run(self) -> None:
        try:
            stage_frames, warnings = self.processor.preview_stages(
                self.experiment,
                prep_config_override=self.config_payload,
            )
            self.signals.finished.emit(self.token, stage_frames, warnings, "")
        except Exception as exc:  # pragma: no cover
            self.signals.finished.emit(self.token, {}, [], str(exc))


class ProcessView(QWidget):
    def __init__(self, ctx: dict, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._by_id: dict[str, ExperimentRef] = {}
        self._loading_editor = False
        self._preview_token = 0
        self._preview_in_flight = False
        self._pending_preview = False
        self._last_render_signature = ""
        self._thread_pool = QThreadPool.globalInstance()

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(350)
        self._preview_timer.timeout.connect(self._run_preview)

        root = QVBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter, 1)

        left_panel = QWidget()
        left = QVBoxLayout(left_panel)
        left.addWidget(QLabel("Experiments"))

        self.exp_list = QListWidget()
        self.exp_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.exp_list.itemSelectionChanged.connect(self._on_selection_changed)
        left.addWidget(self.exp_list)

        controls = QHBoxLayout()
        self.reference_spin = QDoubleSpinBox()
        self.reference_spin.setRange(0.0, 2000.0)
        self.reference_spin.setValue(950.0)
        self.compute_theory = QCheckBox("Compute theoretical mass loss")
        self.compute_theory.setChecked(True)
        self.save_plots = QCheckBox("Save quicklook plots")
        self.save_plots.setChecked(True)
        controls.addWidget(QLabel("Reference Temperature [°C]"))
        controls.addWidget(self.reference_spin)
        controls.addWidget(self.compute_theory)
        controls.addWidget(self.save_plots)
        controls.addStretch()
        left.addLayout(controls)

        run_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh List")
        self.run_btn = QPushButton("Run Pipeline")
        self.refresh_btn.clicked.connect(self.refresh)
        self.run_btn.clicked.connect(self.run_pipeline)
        run_row.addWidget(self.refresh_btn)
        run_row.addWidget(self.run_btn)
        run_row.addStretch()
        left.addLayout(run_row)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        left.addWidget(self.output)

        right_panel = QWidget()
        right = QVBoxLayout(right_panel)
        right.addWidget(QLabel("Preparation Config (effective JSON for selected file)"))

        self.config_editor = QPlainTextEdit()
        self.config_editor.textChanged.connect(self._on_config_text_changed)
        right.addWidget(self.config_editor, 1)

        config_actions = QHBoxLayout()
        self.save_override_btn = QPushButton("Save Override")
        self.reset_override_btn = QPushButton("Reset to Default")
        self.preview_now_btn = QPushButton("Refresh Preview")
        self.save_override_btn.clicked.connect(self.save_override)
        self.reset_override_btn.clicked.connect(self.reset_override)
        self.preview_now_btn.clicked.connect(self._run_preview)
        config_actions.addWidget(self.save_override_btn)
        config_actions.addWidget(self.reset_override_btn)
        config_actions.addWidget(self.preview_now_btn)
        config_actions.addStretch()
        right.addLayout(config_actions)

        right.addWidget(QLabel("Live Stage Preview"))
        self.preview_plot = PlotWidget()
        right.addWidget(self.preview_plot, 2)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([620, 980])

        self.refresh()

    def refresh(self) -> None:
        self.exp_list.clear()
        experiments = self.ctx["catalog_service"].list_experiments()
        self._by_id = {e.id: e for e in experiments}
        for exp in sorted(experiments, key=lambda e: e.id):
            item = QListWidgetItem(f"{exp.id} | {exp.file_path.name}")
            item.setData(Qt.ItemDataRole.UserRole, exp.id)
            self.exp_list.addItem(item)
        if self.exp_list.count() > 0:
            self.exp_list.setCurrentRow(0)

    def run_pipeline(self) -> None:
        selected_ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.exp_list.selectedItems()]
        if not selected_ids:
            self._log("Select at least one experiment.")
            return

        experiments = [self._by_id[eid] for eid in selected_ids if eid in self._by_id]
        cfg = RunConfig(
            reference_temp_c=float(self.reference_spin.value()),
            compute_theory=self.compute_theory.isChecked(),
            save_plots=self.save_plots.isChecked(),
        )

        try:
            run_service = self.ctx["run_service_factory"].get()
        except Exception as exc:
            self._log(f"Run service initialization failed: {exc}")
            if isinstance(exc, FileNotFoundError):
                self._log(self._missing_config_hint(exc))
            elif isinstance(exc, (ImportError, ModuleNotFoundError)):
                self._log("Install missing dependencies with: pip install -e .[dev]")
            return

        artifact = run_service.run(experiments, cfg)
        self._log(f"Run finished: {artifact.status.value}")
        self._log(f"Run ID: {artifact.run_id}")
        self._log(f"Manifest: {artifact.manifest_path}")
        self._log(f"Processed files: {len(artifact.outputs.get('processed', {}))}")
        if artifact.outputs.get("errors"):
            self._log(f"Errors: {artifact.outputs['errors']}")

    def _on_selection_changed(self) -> None:
        exp = self._selected_experiment()
        if exp is None:
            return
        self._load_config_editor(exp)
        self._schedule_preview(interval_ms=500)

    def _load_config_editor(self, exp: ExperimentRef) -> None:
        prep_service = self.ctx.get("prep_config_service")
        if prep_service is None:
            return
        effective = prep_service.get_effective_config(exp.file_path)
        self._loading_editor = True
        self.config_editor.setPlainText(json.dumps(effective, indent=2))
        self._loading_editor = False

    def _on_config_text_changed(self) -> None:
        if self._loading_editor:
            return
        self._schedule_preview(interval_ms=250)

    def _schedule_preview(self, interval_ms: int = 350) -> None:
        self._preview_timer.setInterval(interval_ms)
        self._preview_timer.start()

    def _run_preview(self) -> None:
        if self._preview_in_flight:
            self._pending_preview = True
            return
        exp = self._selected_experiment()
        if exp is None:
            return
        try:
            payload = self._editor_payload()
        except ValueError as exc:
            self._log(f"Preview skipped: {exc}")
            return

        try:
            run_service = self.ctx["run_service_factory"].get()
            processor = run_service.orchestrator.processor
        except Exception as exc:
            self._log(f"Preview processor unavailable: {exc}")
            return

        self._preview_token += 1
        token = self._preview_token
        self._preview_in_flight = True
        task = _PreviewTask(token, processor, exp, payload)
        task.signals.finished.connect(self._on_preview_ready)
        self._thread_pool.start(task)

    def _on_preview_ready(self, token: int, stage_frames: object, warnings: object, error_message: str) -> None:
        if token != self._preview_token:
            return
        self._preview_in_flight = False
        if error_message:
            self._log(f"Preview failed: {error_message}")
            if self._pending_preview:
                self._pending_preview = False
                self._run_preview()
            return
        safe_frames = stage_frames if isinstance(stage_frames, dict) else {}
        render_signature = self._preview_signature(safe_frames)
        fig, plot_warnings = build_stage_preview_figure(safe_frames)
        if render_signature != self._last_render_signature:
            self.preview_plot.set_html(fig.to_html(include_plotlyjs="cdn"))
            self._last_render_signature = render_signature
        for warning in (warnings if isinstance(warnings, list) else []):
            self._log(f"Preview: {warning}")
        for warning in plot_warnings:
            self._log(f"Preview: {warning}")
        if self._pending_preview:
            self._pending_preview = False
            self._run_preview()

    def save_override(self) -> None:
        exp = self._selected_experiment()
        if exp is None:
            return
        prep_service = self.ctx.get("prep_config_service")
        if prep_service is None:
            self._log("Preparation config service is not available.")
            return
        try:
            payload = self._editor_payload()
            prep_service.save_effective_config(exp.file_path, payload)
        except Exception as exc:
            self._log(f"Could not save override: {exc}")
            return
        self._log(f"Saved override for {exp.id}.")

    def reset_override(self) -> None:
        exp = self._selected_experiment()
        if exp is None:
            return
        prep_service = self.ctx.get("prep_config_service")
        if prep_service is None:
            return
        prep_service.reset_override(exp.file_path)
        self._load_config_editor(exp)
        self._schedule_preview(interval_ms=500)
        self._log(f"Reset override for {exp.id}.")

    def _selected_experiment(self) -> ExperimentRef | None:
        items = self.exp_list.selectedItems()
        if not items:
            item = self.exp_list.currentItem()
            if item is None:
                return None
            items = [item]
        exp_id = items[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(exp_id, str):
            return None
        return self._by_id.get(exp_id)

    def _editor_payload(self) -> dict:
        raw = self.config_editor.toPlainText().strip()
        if not raw:
            raise ValueError("config editor is empty")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("config editor must contain a JSON object")
        return payload

    def _log(self, message: str) -> None:
        self.output.appendPlainText(message)

    @staticmethod
    def _preview_signature(stage_frames: dict[str, object]) -> str:
        parts: list[str] = []
        for name in sorted(stage_frames.keys()):
            frame = stage_frames[name]
            if hasattr(frame, "shape") and hasattr(frame, "columns"):
                columns = getattr(frame, "columns")
                parts.append(f"{name}:{getattr(frame, 'shape', '')}:{','.join(map(str, columns))}")
            else:
                parts.append(name)
        return "|".join(parts)

    def _missing_config_hint(self, exc: FileNotFoundError) -> str:
        resolver = self.ctx.get("resolver")
        expected = ""
        lookup_roots = ""
        selected_root = ""
        project_key = ""
        if resolver is not None:
            expected = str(resolver.project_root() / "TGA" / "config.json")
            selected_root = str(resolver.project_root())
            project_key = resolver.inferred_project_key()
            roots = resolver.config_lookup_roots()
            if roots:
                lookup_roots = ", ".join(str(root) for root in roots)
        filename = getattr(exc, "filename", None)
        target = filename or expected or "TGA/config.json"
        root_hint = f" Lookup roots tried: {lookup_roots}." if lookup_roots else ""
        selected_hint = f" Selected data root: {selected_root}." if selected_root else ""
        key_hint = f" Canonical project key: {project_key}." if project_key else ""
        return (
            f"TGA config not found: {target}. "
            f"Use OneDrive project folders for data and the local Git repo for TGA config. "
            f"Set H2LAB_SHAREPOINT_PATH for data roots and optionally H2LAB_LOCAL_REPO_ROOT for local config lookup."
            f"{selected_hint}{key_hint}{root_hint}"
        )
