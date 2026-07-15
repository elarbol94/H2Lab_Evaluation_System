from pathlib import Path

import json
import pandas as pd

from h2lab_tga_app.domain.models import ExperimentRef, RunConfig
from h2lab_tga_app.pipeline.orchestrator import PipelineOrchestrator


class FakeProcessor:
    def process(self, exp: ExperimentRef, cfg: RunConfig) -> pd.DataFrame:
        return pd.DataFrame({"temperature_C": [20, cfg.reference_temp_c], "dm_filtered_pct": [100.0, 90.0]})


def test_orchestrator_smoke(tmp_path: Path) -> None:
    orchestrator = PipelineOrchestrator(outputs_root=tmp_path / ".h2lab_tga" / "outputs", processor=FakeProcessor())
    source = tmp_path / "dummy.txt"
    source.write_text("x", encoding="utf-8")
    experiments = [ExperimentRef(id="RT1", file_path=source)]
    artifact = orchestrator.run_pipeline(experiments, RunConfig())

    assert artifact.manifest_path.exists()
    assert artifact.run_id
    processed = artifact.outputs["processed"]
    assert "RT1" in processed
    assert Path(processed["RT1"]).exists()
    assert Path(processed["RT1"]) == tmp_path / "dummy.parquet"

    with artifact.manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["schema_version"] == 2
    assert "visualization" in manifest
    assert "subplot_layout" in manifest["visualization"]


def test_orchestrator_parquet_source_uses_processed_suffix(tmp_path: Path) -> None:
    orchestrator = PipelineOrchestrator(outputs_root=tmp_path / ".h2lab_tga" / "outputs", processor=FakeProcessor())
    source = tmp_path / "already.parquet"
    source.write_text("x", encoding="utf-8")
    experiments = [ExperimentRef(id="RT2", file_path=source)]
    artifact = orchestrator.run_pipeline(experiments, RunConfig())

    path = Path(artifact.outputs["processed"]["RT2"])
    assert path.name == "already.processed.parquet"
    assert path.exists()
