from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from matplotlib import pyplot as plt
import pandas as pd

from h2lab_tga_app.domain.models import ExperimentRef, RunArtifact, RunConfig, RunStatus
from h2lab_tga_app.infra.filesystem import dump_json, ensure_dir
from h2lab_tga_app.infra.logging import configure_logger


class PipelineOrchestrator:
    def __init__(self, outputs_root: Path, processor) -> None:
        self.outputs_root = outputs_root
        self.processor = processor

    def _new_run_id(self) -> str:
        return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"

    @staticmethod
    def _processed_parquet_path(exp: ExperimentRef) -> Path:
        source = exp.file_path
        if source.suffix.lower() == ".parquet":
            return source.with_name(f"{source.stem}.processed.parquet")
        return source.with_suffix(".parquet")

    def run_pipeline(self, experiments: list[ExperimentRef], cfg: RunConfig) -> RunArtifact:
        run_id = self._new_run_id()
        run_root = ensure_dir(self.outputs_root / "runs" / run_id)
        plots_dir = ensure_dir(run_root / "plots")
        logs_dir = ensure_dir(run_root / "logs")

        logger = configure_logger(logs_dir / "run.log")
        artifact = RunArtifact(
            run_id=run_id,
            created_at=datetime.now(),
            manifest_path=run_root / "manifest.json",
            outputs={"processed": {}, "errors": {}},
            status=RunStatus.RUNNING,
        )

        logger.info("run_started run_id=%s experiments=%d", run_id, len(experiments))
        available_columns: set[str] = set()

        for exp in experiments:
            try:
                df = self.processor.process(exp, cfg)
                available_columns.update(map(str, df.columns))
                out_path = self._processed_parquet_path(exp)
                df.to_parquet(out_path)
                artifact.outputs["processed"][exp.id] = str(out_path)
                if cfg.save_plots:
                    plot_path = self._save_quicklook_plot(df, plots_dir, exp.id)
                    if plot_path is not None:
                        artifact.outputs.setdefault("plots", {})[exp.id] = str(plot_path)
                logger.info("processed experiment_id=%s path=%s", exp.id, out_path)
            except Exception as exc:  # pragma: no cover
                artifact.outputs["errors"][exp.id] = str(exc)
                logger.exception("failed experiment_id=%s", exp.id)

        artifact.status = RunStatus.SUCCEEDED if not artifact.outputs["errors"] else RunStatus.FAILED
        manifest_columns = sorted(available_columns)
        if not manifest_columns:
            manifest_columns = self._collect_available_columns(artifact.outputs.get("processed", {}))

        manifest = {
            "schema_version": 2,
            "run_id": artifact.run_id,
            "created_at": artifact.created_at.isoformat(),
            "status": artifact.status.value,
            "config": {
                "reference_temp_c": cfg.reference_temp_c,
                "compute_theory": cfg.compute_theory,
                "save_plots": cfg.save_plots,
            },
            "outputs": artifact.outputs,
            "visualization": {
                "available_columns": manifest_columns,
                "subplot_layout": self._default_subplot_layout(),
                "preset_name": "legacy_default",
            },
        }
        dump_json(artifact.manifest_path, manifest)
        return artifact

    def _save_quicklook_plot(self, df: pd.DataFrame, plots_dir: Path, experiment_id: str) -> Path | None:
        if "temperature_C" not in df.columns or "dm_filtered_pct" not in df.columns:
            return None
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(df["temperature_C"], df["dm_filtered_pct"], linewidth=1.2)
        ax.set_xlabel("Temperature [°C]")
        ax.set_ylabel("Relative Mass [%]")
        ax.set_title(experiment_id)
        out = plots_dir / f"{experiment_id}.png"
        fig.savefig(out, dpi=150)
        plt.close(fig)
        return out

    def load_previous_run(self, run_id: str) -> dict:
        manifest = self.outputs_root / "runs" / run_id / "manifest.json"
        if not manifest.exists():
            raise FileNotFoundError(f"Run manifest not found: {manifest}")
        with manifest.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _collect_available_columns(self, processed_outputs: dict[str, str]) -> list[str]:
        columns: set[str] = set()
        for path in processed_outputs.values():
            try:
                df = pd.read_parquet(path)
                columns.update(map(str, df.columns))
            except Exception:  # pragma: no cover
                continue
        return sorted(columns)

    @staticmethod
    def _default_subplot_layout() -> list[dict[str, str]]:
        return [
            {
                "id": "legacy_1",
                "x_col": "temperature_C",
                "y_col": "dm_filtered_pct",
                "title": "Relative Mass",
                "x_label": "Temperature [°C]",
                "y_label": "Relative Mass [%]",
            },
            {
                "id": "legacy_2",
                "x_col": "temperature_C",
                "y_col": "dmdt_filtered_pctmin",
                "title": "Reaction Kinetics vs Temperature",
                "x_label": "Temperature [°C]",
                "y_label": "Reaction Kinetics [%/min]",
            },
            {
                "id": "legacy_3",
                "x_col": "dm_filtered_pct",
                "y_col": "dmdt_filtered_pctmin",
                "title": "Reaction Kinetics vs Relative Mass",
                "x_label": "Relative Mass [%]",
                "y_label": "Reaction Kinetics [%/min]",
            },
            {
                "id": "legacy_4",
                "x_col": "time_min",
                "y_col": "CO",
                "title": "CO Flowrate",
                "x_label": "Time [min]",
                "y_label": "CO Flowrate [ml/min]",
            },
        ]
