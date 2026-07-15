from collections import OrderedDict
from pathlib import Path

import pandas as pd

from h2lab_tga_app.domain.models import ExperimentRef
from h2lab_tga_app.pipeline.tga_adapter import TGAProcessor


def test_preview_stages_uses_cache_for_same_input(tmp_path: Path) -> None:
    calls = {"n": 0}

    class FakeTGAExperiment:
        def __init__(
            self,
            file_path,
            config,
            experiment_id,
            df_meta,
            df_comp,
            save_parquet,
            stage_callback,
        ) -> None:
            calls["n"] += 1
            frame = pd.DataFrame({"temperature_C": [20.0, 100.0], "dm_original_mg": [0.0, -1.0]})
            stage_callback("raw_loaded", frame)
            self.df = frame

    processor = object.__new__(TGAProcessor)
    processor.preview_cache_size = 32
    processor._preview_cache = OrderedDict()
    processor._TGAExperiment = FakeTGAExperiment
    processor.df_meta = pd.DataFrame()
    processor.df_comp = pd.DataFrame()
    processor._resolve_preparation_config = lambda exp, override: object()

    source = tmp_path / "RT1.txt"
    source.write_text("x", encoding="utf-8")
    exp = ExperimentRef(id="RT1", file_path=source)
    override = {"process_file": "yes"}

    first, _ = processor.preview_stages(exp, override)
    second, _ = processor.preview_stages(exp, override)

    assert calls["n"] == 1
    assert "raw_loaded" in first
    assert "raw_loaded" in second
