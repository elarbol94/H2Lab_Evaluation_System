from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from h2lab_tga_app.domain.models import ExperimentRef, RunConfig
from h2lab_tga_app.infra.repo_root import find_repo_root


class TGAProcessor:
    """Adapter around existing helper.TGA.TGAExperiment without modifying legacy scripts."""

    def __init__(
        self,
        project_root: Path,
        config_path: Path | None = None,
        df_meta: pd.DataFrame | None = None,
        df_comp: pd.DataFrame | None = None,
        prep_config_service: Any | None = None,
        preview_cache_size: int = 128,
    ) -> None:
        self.project_root = project_root
        self.config_path = config_path or (project_root / "TGA" / "config.json")
        self.df_meta = df_meta
        self.df_comp = df_comp
        self.prep_config_service = prep_config_service
        self.preview_cache_size = max(8, int(preview_cache_size))

        repo_root = find_repo_root(Path(__file__).resolve())
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))

        from helper.TGA import PreparationConfig, TGAExperiment  # pylint: disable=import-outside-toplevel

        self._PreparationConfig = PreparationConfig
        self._TGAExperiment = TGAExperiment
        self._prep_cfg = self._PreparationConfig.load_from_file(str(self.config_path))
        self._prep_cfg_cache: dict[str, Any] = {}
        self._preview_cache: OrderedDict[str, dict[str, pd.DataFrame]] = OrderedDict()

    @staticmethod
    def _get_temperature_index(df: pd.DataFrame, target_temp: float) -> int:
        temperature = df["temperature_C"].to_numpy()
        indices = np.where(temperature >= target_temp)[0]
        if indices.size == 0:
            return int(df.index[-1])
        return int(df.index[indices[0]])

    def process(
        self,
        exp: ExperimentRef,
        cfg: RunConfig,
        prep_config_override: dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        prep_cfg = self._resolve_preparation_config(exp, prep_config_override)
        tga_exp = self._TGAExperiment(
            file_path=str(exp.file_path),
            config=prep_cfg,
            experiment_id=exp.id,
            df_meta=self.df_meta,
            df_comp=self.df_comp,
            save_parquet=False,
        )
        df = tga_exp.df.copy()

        if cfg.compute_theory:
            try:
                theory = tga_exp.get_theoretical_mass_loss(fe_stage="Fe", pb_mode="evaporate")
                df.attrs["theoretical_mass_loss_pct"] = float(theory.get("mass_loss_pct", np.nan))
            except Exception:
                df.attrs["theoretical_mass_loss_pct"] = np.nan

        if "dm_filtered_pct" in df.columns and "temperature_C" in df.columns:
            idx = self._get_temperature_index(df, cfg.reference_temp_c)
            rel_mass = float(df.loc[idx, "dm_filtered_pct"])
            df.attrs["equalization_anchor_rel_mass"] = rel_mass

        return df

    def preview_stages(
        self,
        exp: ExperimentRef,
        prep_config_override: dict[str, Any] | None = None,
    ) -> tuple[dict[str, pd.DataFrame], list[str]]:
        warnings: list[str] = []
        effective_config = (
            prep_config_override
            if prep_config_override is not None
            else self.prep_config_service.get_effective_config(exp.file_path)
            if self.prep_config_service is not None
            else {}
        )
        cache_key = self._preview_cache_key(exp.file_path, effective_config)
        cached = self._preview_cache.get(cache_key)
        if cached is not None:
            self._preview_cache.move_to_end(cache_key)
            return self._copy_stage_frames(cached), warnings

        stage_frames: dict[str, pd.DataFrame] = {}
        prep_cfg = self._resolve_preparation_config(exp, prep_config_override)

        def _on_stage(stage_name: str, frame: pd.DataFrame) -> None:
            stage_frames[stage_name] = frame.copy()

        tga_exp = self._TGAExperiment(
            file_path=str(exp.file_path),
            config=prep_cfg,
            experiment_id=exp.id,
            df_meta=self.df_meta,
            df_comp=self.df_comp,
            save_parquet=False,
            stage_callback=_on_stage,
        )
        if "final_cut" not in stage_frames:
            stage_frames["final_cut"] = tga_exp.df.copy()
        if not stage_frames:
            warnings.append("No preview stages were captured.")
        self._preview_cache[cache_key] = self._copy_stage_frames(stage_frames)
        while len(self._preview_cache) > self.preview_cache_size:
            self._preview_cache.popitem(last=False)
        return stage_frames, warnings

    def _resolve_preparation_config(
        self,
        exp: ExperimentRef,
        prep_config_override: dict[str, Any] | None,
    ):
        if prep_config_override is not None:
            return self._build_prep_config_from_dict(prep_config_override)
        if self.prep_config_service is None:
            return self._prep_cfg
        effective = self.prep_config_service.get_effective_config(exp.file_path)
        return self._build_prep_config_from_dict(effective)

    def _build_prep_config_from_dict(self, payload: dict[str, Any]):
        key = json.dumps(payload, sort_keys=True)
        cached = self._prep_cfg_cache.get(key)
        if cached is not None:
            return cached
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            tmp_path = f.name
        try:
            prep_cfg = self._PreparationConfig.load_from_file(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        self._prep_cfg_cache[key] = prep_cfg
        return prep_cfg

    @staticmethod
    def _preview_cache_key(file_path: Path, payload: dict[str, Any]) -> str:
        return f"{file_path.resolve()}::{json.dumps(payload, sort_keys=True)}"

    @staticmethod
    def _copy_stage_frames(stage_frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        return {name: frame.copy(deep=True) for name, frame in stage_frames.items()}
