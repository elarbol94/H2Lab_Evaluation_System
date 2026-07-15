from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[0]
H2LAB_ROOT = SCRIPT_DIR.parents[1]
if str(H2LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(H2LAB_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helper.TGA import (
    GoogleSheetLoader,
    PreparationConfig,
    TGAExperiment,
    theoretical_mass_loss_from_composition,
)
from setting import (
    apply_pub259_plot_style,
    configure_pub259_legend,
    get_path_for_folder,
    get_pub259_plot_font,
    get_paper_figsize,
    resolve_pub259_figure_stem,
    save_pub259_figure,
)

PROJECT_NAME = "PUB_25_9 Lime in EAFD Recycling"
SHEET_NAME = "H2Lab_PUB_25_9 Lime in EAFD Recycling"
EXPERIMENT_DIRECTORY = "TGA/data"
REFERENCE_TEMPERATURE = 950.0
FIGURE_TARGET = "paper"  # options: "paper", "presentation"
FIGURE_DOMAIN = "TGA"  # options: "EMI", "TGA", "SEM", "Composition", "analysis"
DEFAULT_SAVE_PATH = resolve_pub259_figure_stem(FIGURE_TARGET, FIGURE_DOMAIN, "dmdt_4dust").with_suffix(".png")
PROCESSED_CACHE_DIR = Path("TGA") / "data" / "processed"
PROCESSED_CACHE_SUFFIX = "_prepared.pkl"
DEFAULT_CALIBRATION_FILE = H2LAB_ROOT / "helper" / "TemperatureCalibration.json"
DEFAULT_LIME_COLUMN = "Lime m-%"
DEFAULT_DMDT_TEMP_WINDOW_START_C = 600.0
DEFAULT_ENLARGED_SCALE = 1.3
PAPER_MODE = "paper_full"  # options: "paper_full", "paper_half"

# Same palette used in composition bar plots.
BAR_COLORS = ["#0072B2", "#D55E00", "#009E73", "#E69F00"]
LINE_STYLES = ["-", "--", ":", "-."]

DEFAULT_EXPERIMENT_GROUPS = [
    ["RT54", "RT53", "RT55", "RT56"],  # EAFD1
    ["RT57", "RT58", "RT65", "RT59"],  # EAFD9
    ["RT71", "RT69", "RT72", "RT70"],  # EAFD15
    ["RT73", "RT74", "RT75", "RT76"],  # EAFD18
]

MASS_BASIS_CHOICES = ("mixture", "eafd", "both")
THEORY_OVERLAY_CHOICES = ("off", "mass-loss", "zn-path", "both")


def _normalize_float(value) -> float:
    if pd.isna(value):
        return np.nan
    if isinstance(value, str):
        value = value.replace(",", ".")
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _flatten_experiment_ids(experiment_groups: list[list[str]]) -> list[str]:
    return [exp_id for group in experiment_groups for exp_id in group if exp_id and exp_id != "-"]


def _build_metadata_index(meta_df: pd.DataFrame) -> dict[str, list[int]]:
    if "id" not in meta_df.columns:
        return {}
    id_series = meta_df["id"].fillna("").astype(str)
    base_ids = id_series.str.replace(r"_\d+$", "", regex=True)
    index: dict[str, list[int]] = {}
    for base_id, row_idx in zip(base_ids, meta_df.index):
        if not base_id:
            continue
        index.setdefault(base_id, []).append(row_idx)
    return index


def _get_temperature_index(df: pd.DataFrame, target_temp: float) -> int:
    temperature = df["temperature_C"].to_numpy()
    indices = np.where(temperature >= target_temp)[0]
    if indices.size == 0:
        return int(df.index[-1])
    return int(df.index[indices[0]])


def _trim_to_first_tmax(df: pd.DataFrame) -> pd.DataFrame:
    max_temp = df["temperature_C"].max()
    idx_end = np.where(df["temperature_C"] == max_temp)[0][0]
    return df.iloc[:idx_end].copy()


def _basis_dm_column(basis: str) -> str:
    return "dm_filtered_pct" if basis == "mixture" else "dm_filtered_pct_eafd"


def _basis_dmdt_column(basis: str) -> str:
    return "dmdt_filtered_pctmin" if basis == "mixture" else "dmdt_filtered_pctmin_eafd"


def _basis_mass_label(basis: str) -> str:
    return "Relative Mass\n[% of mixture]" if basis == "mixture" else "Relative Mass\n[% of EAFD]"


def _basis_rate_label(basis: str) -> str:
    if basis == "mixture":
        return f"Reaction Kinetics\n[%/min of mixture]"
    return f"Reaction Kinetics\n[%/min of EAFD]"


def _parse_lime_fraction(meta_rows: pd.DataFrame, lime_column: str, experiment_id: str) -> float:
    if meta_rows.empty or lime_column not in meta_rows.columns:
        return np.nan
    lime_frac = _normalize_float(meta_rows[lime_column].iloc[0])
    if np.isnan(lime_frac):
        return np.nan
    if lime_frac > 1.0 and lime_frac <= 100.0:
        print(
            f"[Info] {experiment_id}: interpreted lime value {lime_frac} in '{lime_column}' as percent and scaled to fraction."
        )
        lime_frac = lime_frac / 100.0
    return float(lime_frac)


def _add_eafd_basis_columns(df: pd.DataFrame, lime_frac: float, experiment_id: str) -> pd.DataFrame:
    out = df.copy()
    if np.isnan(lime_frac) or lime_frac < 0.0 or lime_frac >= 1.0:
        print(
            f"[Warning] {experiment_id}: invalid lime fraction ({lime_frac}); "
            "using mixture basis values as fallback for EAFD columns."
        )
        if "dm_filtered_pct" in out.columns:
            out["dm_filtered_pct_eafd"] = out["dm_filtered_pct"]
        if "dm_original_pct" in out.columns:
            out["dm_original_pct_eafd"] = out["dm_original_pct"]
        if "dmdt_filtered_pctmin" in out.columns:
            out["dmdt_filtered_pctmin_eafd"] = out["dmdt_filtered_pctmin"]
        if "dmdt_original_pctmin" in out.columns:
            out["dmdt_original_pctmin_eafd"] = out["dmdt_original_pctmin"]
        return out

    eafd_frac = 1.0 - lime_frac
    if "dm_filtered_pct" in out.columns:
        r_mix = out["dm_filtered_pct"] / 100.0
        out["dm_filtered_pct_eafd"] = ((r_mix - lime_frac) / eafd_frac) * 100.0
    if "dm_original_pct" in out.columns:
        r_mix = out["dm_original_pct"] / 100.0
        out["dm_original_pct_eafd"] = ((r_mix - lime_frac) / eafd_frac) * 100.0
    if "dmdt_filtered_pctmin" in out.columns:
        out["dmdt_filtered_pctmin_eafd"] = out["dmdt_filtered_pctmin"] / eafd_frac
    if "dmdt_original_pctmin" in out.columns:
        out["dmdt_original_pctmin_eafd"] = out["dmdt_original_pctmin"] / eafd_frac
    return out


def _load_composition_df(path: str | None) -> pd.DataFrame | None:
    if not path:
        return None
    comp_path = Path(path)
    if not comp_path.is_absolute():
        comp_path = SCRIPT_DIR / comp_path
    if not comp_path.exists():
        print(f"[Warning] Dust composition file not found: {comp_path}. Theory overlays disabled.")
        return None
    try:
        df_comp = pd.read_excel(comp_path)
    except Exception as err:
        print(f"[Warning] Could not read dust composition file '{comp_path}': {err}. Theory overlays disabled.")
        return None
    print(f"[Info] Loaded dust composition: {comp_path}")
    return df_comp


def _extract_composition_row(df_comp: pd.DataFrame, material: str) -> dict[str, float] | None:
    if df_comp is None or df_comp.empty or "Dust" not in df_comp.columns:
        return None
    mask = df_comp["Dust"].fillna("").astype(str).str.strip().str.upper() == str(material).strip().upper()
    if not mask.any():
        return None
    row = df_comp.loc[mask].iloc[0]
    comp: dict[str, float] = {}
    for col, val in row.items():
        if col == "Dust":
            continue
        f = _normalize_float(val)
        if not np.isnan(f):
            comp[str(col)] = float(f)
    return comp if comp else None


def _compute_theory_metrics_for_experiment(
        *,
        material: str,
        lime_frac: float,
        initial_weight_mg: float,
        df_comp: pd.DataFrame | None,
        pb_mode: str,
        zn_fraction: float,
) -> dict[str, float]:
    if df_comp is None or np.isnan(initial_weight_mg) or initial_weight_mg <= 0:
        return {}
    comp = _extract_composition_row(df_comp, material)
    if comp is None:
        return {}

    lime = 0.0 if np.isnan(lime_frac) else float(np.clip(lime_frac, 0.0, 0.999999))
    eafd_mass_g = float(initial_weight_mg) * (1.0 - lime) / 1000.0
    if eafd_mass_g <= 0:
        return {}

    try:
        theory = theoretical_mass_loss_from_composition(
            comp,
            eafd_mass_g,
            fe_stage="Fe",
            pb_mode=pb_mode,
            zn_fraction=float(np.clip(zn_fraction, 0.0, 1.0)),
        )
    except Exception as err:
        print(f"[Warning] Theory computation failed for material '{material}': {err}")
        return {}

    mass_loss_eafd_pct = float(theory.get("mass_loss_pct", np.nan))
    mass_remaining_eafd_pct = 100.0 - mass_loss_eafd_pct if pd.notna(mass_loss_eafd_pct) else np.nan

    breakdown = theory.get("breakdown_g", {}) or {}
    zn_path_g = breakdown.get("Zn_path", np.nan)
    zn_path_eafd_pct = np.nan
    zn_remaining_eafd_proxy_pct = np.nan
    if pd.notna(zn_path_g):
        zn_path_eafd_pct = 100.0 * float(zn_path_g) / eafd_mass_g
        zn_remaining_eafd_proxy_pct = 100.0 - zn_path_eafd_pct

    mass_loss_mix_pct = np.nan
    mass_remaining_mix_pct = np.nan
    zn_path_mix_pct = np.nan
    zn_remaining_mix_proxy_pct = np.nan
    if pd.notna(mass_loss_eafd_pct):
        mass_loss_mix_pct = (1.0 - lime) * mass_loss_eafd_pct
        mass_remaining_mix_pct = 100.0 - mass_loss_mix_pct
    if pd.notna(zn_path_eafd_pct):
        zn_path_mix_pct = (1.0 - lime) * zn_path_eafd_pct
        zn_remaining_mix_proxy_pct = 100.0 - zn_path_mix_pct

    return {
        "mass_loss_eafd_pct": mass_loss_eafd_pct,
        "mass_remaining_eafd_pct": mass_remaining_eafd_pct,
        "zn_path_eafd_pct": zn_path_eafd_pct,
        "zn_remaining_eafd_proxy_pct": zn_remaining_eafd_proxy_pct,
        "mass_loss_mixture_pct": mass_loss_mix_pct,
        "mass_remaining_mixture_pct": mass_remaining_mix_pct,
        "zn_path_mixture_pct": zn_path_mix_pct,
        "zn_remaining_mixture_proxy_pct": zn_remaining_mix_proxy_pct,
    }


def _remaining_key(basis: str, kind: str) -> str:
    if kind == "mass-loss":
        return "mass_remaining_mixture_pct" if basis == "mixture" else "mass_remaining_eafd_pct"
    return "zn_remaining_mixture_proxy_pct" if basis == "mixture" else "zn_remaining_eafd_proxy_pct"


class TGAPlotContext:
    def __init__(
            self,
            project_path: Path,
            prep_cfg: PreparationConfig,
            df_meta: pd.DataFrame,
            *,
            use_cache: bool = True,
            force_reprocess: bool = False,
    ):
        self.project_path = project_path
        self.prep_cfg = prep_cfg
        self.df_meta = df_meta
        self.use_cache = use_cache
        self.force_reprocess = force_reprocess
        self.meta_index = _build_metadata_index(df_meta)
        self.meta_row_cache: dict[str, pd.DataFrame] = {}
        self.file_cache: dict[str, str] = {}

        txt_files = [f for f in os.listdir(EXPERIMENT_DIRECTORY) if f.endswith(".txt")]
        self.txt_files = txt_files
        self.processed_cache_dir = self.project_path / PROCESSED_CACHE_DIR
        self.processed_cache_dir.mkdir(parents=True, exist_ok=True)

    def get_metadata_rows(self, experiment_id: str) -> pd.DataFrame:
        if "id" not in self.df_meta.columns:
            raise KeyError("'id' column missing in metadata sheet")
        exp_id = str(experiment_id)
        cached = self.meta_row_cache.get(exp_id)
        if cached is not None:
            return cached.copy()

        pattern = re.compile(rf"^{re.escape(exp_id)}(_\d+)?$")
        base_id = re.sub(r"_\d+$", "", exp_id)
        idxs = self.meta_index.get(base_id)
        if idxs:
            subset = self.df_meta.loc[idxs]
            mask = subset["id"].fillna("").astype(str).str.match(pattern)
            meta = subset.loc[mask].copy()
        else:
            mask = self.df_meta["id"].fillna("").astype(str).str.match(pattern)
            meta = self.df_meta.loc[mask].copy()

        if meta.empty:
            print(f"[Warning] No metadata entry found for '{experiment_id}'")
        self.meta_row_cache[exp_id] = meta
        return meta

    def find_experiment_file(self, experiment_id: str) -> str:
        cached = self.file_cache.get(experiment_id)
        if cached is not None:
            return cached
        matches = [f for f in self.txt_files if experiment_id in f]
        if not matches:
            raise FileNotFoundError(f"No matching .txt file found for experiment ID '{experiment_id}'")
        self.file_cache[experiment_id] = matches[0]
        return matches[0]

    def _cache_path_for_experiment(self, experiment_id: str) -> Path:
        return self.processed_cache_dir / f"{experiment_id}{PROCESSED_CACHE_SUFFIX}"

    def _save_processed_dataframe(
            self,
            experiment_id: str,
            prepared_df: pd.DataFrame,
            initial_weight: float | None,
    ) -> Path:
        cache_path = self._cache_path_for_experiment(experiment_id)
        payload = {
            "df": prepared_df.copy(),
            "initial_weight": float(initial_weight) if initial_weight is not None else np.nan,
        }
        pd.to_pickle(payload, cache_path)
        return cache_path

    def _load_processed_dataframe(self, experiment_id: str) -> tuple[pd.DataFrame, float]:
        cache_path = self._cache_path_for_experiment(experiment_id)
        payload = pd.read_pickle(cache_path)
        if isinstance(payload, dict) and "df" in payload:
            df = payload["df"].copy()
            initial_weight = float(payload.get("initial_weight", np.nan))
            if "temperature_raw_C" not in df.columns:
                print(
                    f"[Info] Cache for {experiment_id} has no 'temperature_raw_C'. "
                    "It was likely created before Curie correction was enforced. "
                    "Use --force-reprocess to refresh."
                )
            return df, initial_weight
        if isinstance(payload, pd.DataFrame):
            # Backward-compatible shape if only dataframe was cached.
            if "temperature_raw_C" not in payload.columns:
                print(
                    f"[Info] Cache for {experiment_id} has no 'temperature_raw_C'. "
                    "Use --force-reprocess to refresh with Curie-corrected temperature."
                )
            return payload.copy(), np.nan
        raise ValueError(f"Invalid cache payload in {cache_path}")

    def _process_and_cache_experiment(self, experiment_id: str) -> tuple[pd.DataFrame, float]:
        path = self.find_experiment_file(experiment_id)
        exp = TGAExperiment(
            file_path=f"{EXPERIMENT_DIRECTORY}/{path}",
            config=self.prep_cfg,
            df_meta=self.df_meta,
            experiment_id=experiment_id,
        )
        prepared_df = _trim_to_first_tmax(exp.df)
        initial_weight = float(getattr(exp, "initial_weight", np.nan))
        cache_path = self._save_processed_dataframe(
            experiment_id=experiment_id,
            prepared_df=prepared_df,
            initial_weight=initial_weight,
        )
        print(f"[Info] Saved processed dataframe: {cache_path}")
        return prepared_df, initial_weight

    def load_raw_experiment(self, experiment_id: str) -> tuple[pd.DataFrame, pd.DataFrame, float]:
        cache_path = self._cache_path_for_experiment(experiment_id)
        if self.use_cache and (not self.force_reprocess) and cache_path.exists():
            prepared_df, initial_weight = self._load_processed_dataframe(experiment_id)
            df_meta_single = self.get_metadata_rows(experiment_id)
            return prepared_df, df_meta_single, initial_weight

        prepared_df, initial_weight = self._process_and_cache_experiment(experiment_id)
        df_meta_single = self.get_metadata_rows(experiment_id)
        return prepared_df, df_meta_single, initial_weight

    def process_all_files(self, *, force_reprocess: bool = False) -> None:
        """
        Process all available txt files and store processed dataframe cache.
        When force_reprocess=True, ignore existing cache and overwrite.
        """
        for txt_file in self.txt_files:
            stem = Path(txt_file).stem
            parts = stem.split("_")
            exp_id = parts[1] if len(parts) > 1 else None
            if not exp_id:
                print(f"[Warning] Could not extract experiment id from filename: {txt_file}")
                continue
            cache_path = self._cache_path_for_experiment(exp_id)
            if cache_path.exists() and (not force_reprocess):
                continue
            try:
                _ = self._process_and_cache_experiment(exp_id)
            except Exception as err:
                print(f"[Warning] Failed processing {exp_id} ({txt_file}): {err}")


def compute_equalization_targets(
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        *,
        basis: str,
        lime_column: str,
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> dict[str, float]:
    baseline_candidates: dict[str, dict[str, float | str]] = {}
    for exp_id in _flatten_experiment_ids(experiment_groups):
        meta_rows = context.get_metadata_rows(exp_id)
        if meta_rows.empty:
            continue
        material = str(meta_rows["material"].iloc[0])
        lime_pct = _parse_lime_fraction(meta_rows, lime_column, exp_id)
        prev = baseline_candidates.get(material)
        if prev is None:
            baseline_candidates[material] = {"lime_pct": lime_pct, "exp_id": exp_id}
            continue
        prev_lime = float(prev["lime_pct"])
        if np.isnan(prev_lime) and not np.isnan(lime_pct):
            baseline_candidates[material] = {"lime_pct": lime_pct, "exp_id": exp_id}
        elif not np.isnan(lime_pct) and lime_pct < prev_lime:
            baseline_candidates[material] = {"lime_pct": lime_pct, "exp_id": exp_id}

    relative_mass_dict: dict[str, float] = {}
    col = _basis_dm_column(basis)
    for material, info in baseline_candidates.items():
        exp_id = str(info["exp_id"])
        df, meta_rows, _ = context.load_raw_experiment(exp_id)
        lime_frac = _parse_lime_fraction(meta_rows, lime_column, exp_id)
        df_with_basis = _add_eafd_basis_columns(df, lime_frac, exp_id)
        temp_idx = _get_temperature_index(df_with_basis, reference_temp)
        relative_mass_dict[material] = float(df_with_basis.loc[temp_idx, col])
    return relative_mass_dict


def _equalize_basis_inplace(
        *,
        df: pd.DataFrame,
        temp_idx: int,
        target_mass_pct: float,
        basis: str,
        initial_weight: float,
) -> None:
    dm_col = _basis_dm_column(basis)
    if dm_col not in df.columns:
        return
    current = float(df.loc[temp_idx, dm_col])
    delta_pct = float(target_mass_pct) - current
    if np.isclose(delta_pct, 0.0):
        return

    if basis == "mixture":
        for col in ("dm_filtered_pct", "dm_original_pct"):
            if col in df.columns:
                df[col] = df[col] + delta_pct
        if pd.notna(initial_weight):
            delta_mg = delta_pct * float(initial_weight) / 100.0
            for col in ("dm_filtered_mg", "dm_original_mg", "m_filtered_mg"):
                if col in df.columns:
                    df[col] = df[col] + delta_mg
    else:
        for col in ("dm_filtered_pct_eafd", "dm_original_pct_eafd"):
            if col in df.columns:
                df[col] = df[col] + delta_pct


def _select_theory_overlay_mode(requested: str, has_comp: bool) -> str:
    if not has_comp:
        if requested != "off":
            print("[Info] Theory overlay disabled because no composition data is available.")
        return "off"
    return requested


def load_experiment(
        context: TGAPlotContext,
        experiment_id: str,
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        lime_column: str,
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> tuple[pd.DataFrame, pd.DataFrame, float, float]:
    df, df_meta_single, initial_weight = context.load_raw_experiment(experiment_id)
    lime_frac = _parse_lime_fraction(df_meta_single, lime_column, experiment_id)
    df = _add_eafd_basis_columns(df, lime_frac, experiment_id)

    material = df_meta_single["material"].iloc[0] if not df_meta_single.empty else None
    if material is not None:
        temp_idx = _get_temperature_index(df, reference_temp)
        if temp_idx in df.index:
            for basis, rel_mass_dict in rel_mass_targets_by_basis.items():
                if material in rel_mass_dict:
                    _equalize_basis_inplace(
                        df=df,
                        temp_idx=temp_idx,
                        target_mass_pct=float(rel_mass_dict[material]),
                        basis=basis,
                        initial_weight=initial_weight,
                    )

    return df.copy(), df_meta_single, float(initial_weight), float(lime_frac)


def _format_material_label(raw: str) -> str:
    return str(raw).strip().upper()


def _material_label_for_group(context: TGAPlotContext, group: list[str], fallback_index: int) -> str:
    material_label = f"Group {fallback_index + 1}"
    for candidate in group:
        if candidate == "-":
            continue
        meta_rows = context.get_metadata_rows(candidate)
        if not meta_rows.empty and "material" in meta_rows.columns:
            return _format_material_label(str(meta_rows["material"].iloc[0]))
    return material_label


def _sanitize_material_stem(material_label: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9]+", "_", str(material_label).strip().lower())
    sanitized = sanitized.strip("_")
    return sanitized or "dust"


def _plot_metric_group(
        *,
        ax: plt.Axes,
        context: TGAPlotContext,
        group: list[str],
        group_idx: int,
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        basis: str,
        x_mode: str,
        y_mode: str,
        legend_loc: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        draw_zero_line: bool,
        invert_x: bool,
        legend_bbox_to_anchor: tuple[float, float] | None = None,
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> str:
    dust_color = BAR_COLORS[group_idx % len(BAR_COLORS)]
    legend_title = _material_label_for_group(context, group, group_idx)
    for line_idx, exp_id in enumerate(group):
        if exp_id == "-":
            continue
        print(f"[Info] plotting experiment: {exp_id}")
        df, df_meta_single, _, _ = load_experiment(
            context=context,
            experiment_id=exp_id,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            lime_column=lime_column,
            reference_temp=reference_temp,
        )
        dmdt_col = _basis_dmdt_column(basis)
        dm_col = _basis_dm_column(basis)
        if dmdt_col not in df.columns:
            raise ValueError(f"Expected dmdt column '{dmdt_col}' not found for {exp_id}.")
        if dm_col not in df.columns:
            raise ValueError(f"Expected dm column '{dm_col}' not found for {exp_id}.")

        linestyle = LINE_STYLES[line_idx % len(LINE_STYLES)]
        lime_pct = np.nan
        if not df_meta_single.empty and lime_column in df_meta_single.columns:
            lime_pct = _parse_lime_fraction(df_meta_single, lime_column, exp_id)
        lime_addition = int(round(lime_pct * 100.0)) if pd.notna(lime_pct) and lime_pct > 0 else 0
        labelname = f"{lime_addition} % CaO"

        if x_mode == "temperature_C":
            x = df["temperature_C"]
        elif x_mode == "dm_filtered_pct":
            x = df[dm_col]
        else:
            raise ValueError(f"Unsupported x_mode: {x_mode}")

        if y_mode == "dmdt_filtered_pctmin":
            y = df[dmdt_col]
        elif y_mode == "dm_filtered_pct":
            y = df[dm_col]
        else:
            raise ValueError(f"Unsupported y_mode: {y_mode}")

        ax.plot(
            x,
            y,
            color=dust_color,
            linestyle=linestyle,
            linewidth=1.2,
            label=labelname,
        )

        theory_metrics = theory_metrics_by_exp.get(exp_id, {})
        if theory_overlay in ("mass-loss", "both"):
            key = _remaining_key(basis, "mass-loss")
            value = theory_metrics.get(key, np.nan)
            if pd.notna(value):
                if y_mode == "dm_filtered_pct":
                    ax.axhline(y=float(value), color=dust_color, linestyle="--", linewidth=0.8, alpha=0.85)
                if x_mode == "dm_filtered_pct":
                    ax.axvline(x=float(value), color=dust_color, linestyle="--", linewidth=0.8, alpha=0.85)
        if theory_overlay in ("zn-path", "both"):
            key = _remaining_key(basis, "zn-path")
            value = theory_metrics.get(key, np.nan)
            if pd.notna(value):
                if y_mode == "dm_filtered_pct":
                    ax.axhline(y=float(value), color=dust_color, linestyle=":", linewidth=0.8, alpha=0.85)
                if x_mode == "dm_filtered_pct":
                    ax.axvline(x=float(value), color=dust_color, linestyle=":", linewidth=0.8, alpha=0.85)

    if draw_zero_line:
        ax.axhline(y=0, color="grey", linestyle="--", linewidth=0.7)
    if invert_x:
        ax.invert_xaxis()
    ax.spines["top"].set_visible(True)
    ax.spines["right"].set_visible(True)
    legend_kwargs: dict[str, object] = {
        "title": legend_title,
        "loc": legend_loc,
    }
    if legend_bbox_to_anchor is not None:
        legend_kwargs["bbox_to_anchor"] = legend_bbox_to_anchor
    configure_pub259_legend(ax, **legend_kwargs)
    return legend_title


def _plot_metric_4dust(
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        basis: str,
        x_mode: str,
        y_mode: str,
        x_label: str,
        y_label: str,
        draw_zero_line: bool,
        invert_x: bool,
        legend_loc: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        legend_bbox_to_anchor: tuple[float, float] | None = None,
        reference_temp: float = REFERENCE_TEMPERATURE,
        figsize: tuple[float, float] | None = None,
) -> plt.Figure:
    if len(experiment_groups) != 4:
        raise ValueError(f"Expected exactly 4 experiment groups, got {len(experiment_groups)}")

    active_size = get_paper_figsize(PAPER_MODE) if figsize is None else figsize
    apply_pub259_plot_style(figsize=active_size)
    print(
        f"[Info] Plot style: font={get_pub259_plot_font()}, "
        f"font_size={plt.rcParams.get('font.size')}, "
        f"figsize={active_size}"
    )

    figure_size = active_size
    fig, axes = plt.subplots(2, 2, figsize=figure_size, sharex=True, sharey=True)
    fig.subplots_adjust()
    axes_flat = axes.flatten()

    for group_idx, (group, ax) in enumerate(zip(experiment_groups, axes_flat)):
        _plot_metric_group(
            ax=ax,
            context=context,
            group=group,
            group_idx=group_idx,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            basis=basis,
            x_mode=x_mode,
            y_mode=y_mode,
            legend_loc=legend_loc,
            lime_column=lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            draw_zero_line=draw_zero_line,
            invert_x=invert_x,
            legend_bbox_to_anchor=legend_bbox_to_anchor,
            reference_temp=reference_temp,
        )

    axes[1, 0].set_xlabel(x_label)
    axes[1, 1].set_xlabel(x_label)
    axes[0, 0].set_ylabel(y_label)
    axes[1, 0].set_ylabel(y_label)
    return fig


def plot_dmdt_temperature_4dust(
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        basis: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> plt.Figure:
    return _plot_metric_4dust(
        context=context,
        experiment_groups=experiment_groups,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        basis=basis,
        x_mode="temperature_C",
        y_mode="dmdt_filtered_pctmin",
        x_label="Temperature [°C]",
        y_label=_basis_rate_label(basis),
        draw_zero_line=True,
        invert_x=False,
        legend_loc="lower left",
        lime_column=lime_column,
        theory_overlay=theory_overlay,
        theory_metrics_by_exp=theory_metrics_by_exp,
        reference_temp=reference_temp,
    )


def plot_dmdt_temperature_4dust_enlarged(
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        basis: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        temperature_min_c: float = DEFAULT_DMDT_TEMP_WINDOW_START_C,
        enlarged_scale: float = DEFAULT_ENLARGED_SCALE,
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> plt.Figure:
    base_w, base_h = get_paper_figsize(PAPER_MODE)
    scale = enlarged_scale if enlarged_scale > 0 else 1.0
    fig = _plot_metric_4dust(
        context=context,
        experiment_groups=experiment_groups,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        basis=basis,
        x_mode="temperature_C",
        y_mode="dmdt_filtered_pctmin",
        x_label="Temperature [°C]",
        y_label=_basis_rate_label(basis),
        draw_zero_line=True,
        invert_x=False,
        legend_loc="lower left",
        lime_column=lime_column,
        theory_overlay=theory_overlay,
        theory_metrics_by_exp=theory_metrics_by_exp,
        reference_temp=reference_temp,
        figsize=(base_w * scale, base_h * scale),
    )

    # Zoom into high-temperature kinetics window.
    for ax in fig.axes:
        x0, x1 = ax.get_xlim()
        xmax = max(x0, x1)
        if xmax > temperature_min_c:
            ax.set_xlim(float(temperature_min_c), float(xmax))
    return fig


def plot_dm_temperature_4dust(
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        basis: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> plt.Figure:
    return _plot_metric_4dust(
        context=context,
        experiment_groups=experiment_groups,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        basis=basis,
        x_mode="temperature_C",
        y_mode="dm_filtered_pct",
        x_label="Temperature [°C]",
        y_label=_basis_mass_label(basis),
        draw_zero_line=False,
        invert_x=False,
        legend_loc="lower left",
        lime_column=lime_column,
        theory_overlay=theory_overlay,
        theory_metrics_by_exp=theory_metrics_by_exp,
        reference_temp=reference_temp,
    )


def plot_dm_temperature_single_dust(
        context: TGAPlotContext,
        group: list[str],
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        group_idx: int,
        basis: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        reference_temp: float = REFERENCE_TEMPERATURE,
        figsize: tuple[float, float] | None = None,
) -> tuple[plt.Figure, str]:
    active_size = get_paper_figsize(PAPER_MODE) if figsize is None else figsize
    apply_pub259_plot_style(figsize=active_size)
    print(
        f"[Info] Plot style: font={get_pub259_plot_font()}, "
        f"font_size={plt.rcParams.get('font.size')}, "
        f"figsize={active_size}"
    )
    fig, ax = plt.subplots(figsize=active_size)
    legend_title = _plot_metric_group(
        ax=ax,
        context=context,
        group=group,
        group_idx=group_idx,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        basis=basis,
        x_mode="temperature_C",
        y_mode="dm_filtered_pct",
        legend_loc="lower left",
        lime_column=lime_column,
        theory_overlay=theory_overlay,
        theory_metrics_by_exp=theory_metrics_by_exp,
        draw_zero_line=False,
        invert_x=False,
        reference_temp=reference_temp,
    )
    ax.set_xlabel("Temperature [°C]")
    ax.set_ylabel(_basis_mass_label(basis))
    return fig, legend_title


def plot_dmdt_dm_4dust(
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        *,
        basis: str,
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> plt.Figure:
    fig = _plot_metric_4dust(
        context=context,
        experiment_groups=experiment_groups,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        basis=basis,
        x_mode="dm_filtered_pct",
        y_mode="dmdt_filtered_pctmin",
        x_label=_basis_mass_label(basis),
        y_label=_basis_rate_label(basis),
        draw_zero_line=True,
        invert_x=True,
        legend_loc="best",
        lime_column=lime_column,
        theory_overlay=theory_overlay,
        theory_metrics_by_exp=theory_metrics_by_exp,
        reference_temp=reference_temp,
    )
    # Enforce 100% anchor on the left side, while still showing values >100%.
    for ax in fig.axes:
        legend_title = ""
        old_legend = ax.get_legend()
        if old_legend is not None:
            legend_title = old_legend.get_title().get_text()
        x0, x1 = ax.get_xlim()
        left = max(100.0, max(x0, x1))
        right = min(x0, x1)
        if left <= right:
            right = left - 1.0
        pad = 0.01 * abs(left - right)
        ax.set_xlim(left - pad, right)
        # Keep legend fixed at top-right and visually separated from the zero line.
        if old_legend is not None:
            old_legend.remove()
        configure_pub259_legend(
            ax,
            title=legend_title or None,
            edgecolor="none",
            loc="upper right",
            bbox_to_anchor=(0.98, 1.05),
        )
    return fig


def _save_figure_outputs(
        fig: plt.Figure,
        stem: Path,
        *,
        dpi: int,
        inkscape_path: str | None = None,
) -> tuple[Path, Path | None]:
    png_path, svg_path, ppt_svg_path = save_pub259_figure(
        fig,
        stem,
        png_dpi=dpi,
        inkscape_path=inkscape_path,
    )
    # Keep only PNG + PowerPoint-safe SVG outputs in this workflow.
    try:
        if svg_path is not None and svg_path.exists():
            svg_path.unlink()
    except OSError as err:
        print(f"[Warning] Could not remove intermediate SVG '{svg_path}': {err}")
    return png_path, ppt_svg_path


def _create_context(*, use_cache: bool = True, force_reprocess: bool = False) -> TGAPlotContext:
    prep_cfg = PreparationConfig.load_from_file(str(SCRIPT_DIR / "config.json"))
    print("[Info] Configuration file successfully loaded")

    # Force Curie-point correction for all runs in this script.
    prep_cfg.temperature_correction.use = "yes"
    prep_cfg.temperature_correction.calibration_file = str(DEFAULT_CALIBRATION_FILE)
    if not Path(prep_cfg.temperature_correction.calibration_file).exists():
        raise FileNotFoundError(
            "Curie calibration file not found: "
            f"{prep_cfg.temperature_correction.calibration_file}"
        )
    print(
        "[Info] Curie-point temperature correction forced: "
        f"use=yes, calibration_file={prep_cfg.temperature_correction.calibration_file}"
    )

    try:
        project_path = get_path_for_folder(PROJECT_NAME)
    except TypeError as exc:
        raise RuntimeError(
            "Could not resolve project path because H2LAB_SHAREPOINT_PATH is not set."
        ) from exc

    if not project_path:
        raise RuntimeError(
            "Could not resolve project path because H2LAB_SHAREPOINT_PATH is not set."
        )

    os.chdir(project_path)
    sheet_loader = GoogleSheetLoader()
    df_meta = sheet_loader.load_sheet(SHEET_NAME)
    return TGAPlotContext(
        project_path=Path(project_path),
        prep_cfg=prep_cfg,
        df_meta=df_meta,
        use_cache=use_cache,
        force_reprocess=force_reprocess,
    )


def _build_theory_metrics(
        *,
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        lime_column: str,
        df_comp: pd.DataFrame | None,
        pb_mode: str,
        zn_fraction: float,
) -> dict[str, dict[str, float]]:
    if df_comp is None:
        return {}
    metrics_by_exp: dict[str, dict[str, float]] = {}
    for exp_id in _flatten_experiment_ids(experiment_groups):
        df_meta_single = context.get_metadata_rows(exp_id)
        if df_meta_single.empty or "material" not in df_meta_single.columns:
            continue
        material = str(df_meta_single["material"].iloc[0])
        lime_frac = _parse_lime_fraction(df_meta_single, lime_column, exp_id)
        _, _, initial_weight = context.load_raw_experiment(exp_id)
        metrics = _compute_theory_metrics_for_experiment(
            material=material,
            lime_frac=lime_frac,
            initial_weight_mg=float(initial_weight),
            df_comp=df_comp,
            pb_mode=pb_mode,
            zn_fraction=zn_fraction,
        )
        if metrics:
            metrics_by_exp[exp_id] = metrics
    return metrics_by_exp


def _write_theory_summary_csv(
        *,
        out_path: Path,
        context: TGAPlotContext,
        experiment_groups: list[list[str]],
        theory_metrics_by_exp: dict[str, dict[str, float]],
        lime_column: str,
        bases: list[str],
) -> None:
    rows: list[dict[str, Any]] = []
    for exp_id in _flatten_experiment_ids(experiment_groups):
        metrics = theory_metrics_by_exp.get(exp_id)
        if not metrics:
            continue
        meta_rows = context.get_metadata_rows(exp_id)
        material = str(meta_rows["material"].iloc[0]) if not meta_rows.empty and "material" in meta_rows.columns else ""
        lime_frac = _parse_lime_fraction(meta_rows, lime_column, exp_id)
        for basis in bases:
            rows.append(
                {
                    "experiment_id": exp_id,
                    "material": material,
                    "lime_pct": np.nan if np.isnan(lime_frac) else 100.0 * lime_frac,
                    "basis": basis,
                    "theoretical_mass_loss_pct": metrics.get(
                        "mass_loss_mixture_pct" if basis == "mixture" else "mass_loss_eafd_pct", np.nan
                    ),
                    "theoretical_mass_remaining_pct": metrics.get(
                        "mass_remaining_mixture_pct" if basis == "mixture" else "mass_remaining_eafd_pct", np.nan
                    ),
                    "theoretical_zn_path_pct": metrics.get(
                        "zn_path_mixture_pct" if basis == "mixture" else "zn_path_eafd_pct", np.nan
                    ),
                    "theoretical_zn_remaining_proxy_pct": metrics.get(
                        "zn_remaining_mixture_proxy_pct" if basis == "mixture" else "zn_remaining_eafd_proxy_pct",
                        np.nan
                    ),
                }
            )
    if not rows:
        print("[Info] No theory metrics available; CSV not written.")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"[Info] Theory summary CSV saved to: {out_path}")


def _selected_bases(mode: str) -> list[str]:
    if mode == "both":
        return ["mixture", "eafd"]
    return [mode]


def _poster_output_dir(context: TGAPlotContext) -> Path:
    out_dir = context.project_path / "diagram" / "poster"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def export_poster_dm_temperature_per_dust(
        *,
        context: TGAPlotContext,
        rel_mass_targets_by_basis: dict[str, dict[str, float]],
        lime_column: str,
        theory_overlay: str,
        theory_metrics_by_exp: dict[str, dict[str, float]],
        dpi: int,
        inkscape_path: str | None = None,
        reference_temp: float = REFERENCE_TEMPERATURE,
) -> list[Path]:
    out_dir = _poster_output_dir(context)
    saved_paths: list[Path] = []
    for group_idx, group in enumerate(DEFAULT_EXPERIMENT_GROUPS):
        fig, material_label = plot_dm_temperature_single_dust(
            context=context,
            group=group,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            group_idx=group_idx,
            basis="eafd",
            lime_column=lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            reference_temp=reference_temp,
        )
        fig.subplots_adjust(top=0.975,
                            bottom=0.11,
                            left=0.125,
                            right=0.99)
        out_stem = out_dir / f"{_sanitize_material_stem(material_label)}_dm_vs_temperature_eafd"
        png_path, ppt_svg_path = _save_figure_outputs(
            fig,
            out_stem,
            dpi=dpi,
            inkscape_path=inkscape_path,
        )
        print(f"[Info] Poster figure saved to: {png_path}")
        if ppt_svg_path is not None:
            print(f"[Info] Poster PowerPoint SVG saved to: {ppt_svg_path}")
        saved_paths.append(png_path)
        plt.close(fig)
    return saved_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create 4-panel TGA plots with dual mass-basis support and optional theory overlays."
    )
    parser.add_argument(
        "--reference-temp",
        type=float,
        default=REFERENCE_TEMPERATURE,
        help=f"Reference temperature for mass equalization (default: {REFERENCE_TEMPERATURE}).",
    )
    parser.add_argument(
        "--mass-basis",
        choices=MASS_BASIS_CHOICES,
        default="both",
        help="Mass basis for plotting/equalization: mixture, eafd, or both.",
    )
    parser.add_argument(
        "--lime-column",
        type=str,
        default=DEFAULT_LIME_COLUMN,
        help=f"Metadata column containing lime mass fraction/percent (default: '{DEFAULT_LIME_COLUMN}').",
    )
    parser.add_argument(
        "--dust-composition",
        type=str,
        default=None,
        help="Optional path to dust composition Excel file for theory overlays.",
    )
    parser.add_argument(
        "--zn-fraction",
        type=float,
        default=1.0,
        help="Fraction of Zn path used in theoretical model (0..1, default: 1.0).",
    )
    parser.add_argument(
        "--pb-mode",
        choices=("retain", "evaporate", "chlorinate"),
        default="evaporate",
        help="Pb mode for theoretical model (default: evaporate).",
    )
    parser.add_argument(
        "--theory-overlay",
        choices=THEORY_OVERLAY_CHOICES,
        default="both",
        help="Overlay theoretical markers: off, mass-loss, zn-path, or both.",
    )
    parser.add_argument(
        "--theory-summary-csv",
        type=str,
        default=None,
        help="Optional CSV output path for theoretical summary metrics.",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=str(DEFAULT_SAVE_PATH),
        help=f"Output image stem/file (default: {DEFAULT_SAVE_PATH}). Saves PNG and PowerPoint SVG (_ppt.svg).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=600,
        help="DPI for saved figure (default: 600).",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open plot window (useful with --save).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not load existing processed dataframe cache files.",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Force reprocessing for required experiments and overwrite their cache files.",
    )
    parser.add_argument(
        "--process-all-no-cache",
        action="store_true",
        help=(
            "Process all txt files and overwrite cache without checking existing processed files "
            "before plotting."
        ),
    )
    parser.add_argument(
        "--inkscape-path",
        type=str,
        default=None,
        help="Optional full path to inkscape executable for PPT-safe SVG export.",
    )
    parser.add_argument(
        "--dmdt-temp-window-start",
        type=float,
        default=DEFAULT_DMDT_TEMP_WINDOW_START_C,
        help=(
            "Lower temperature bound for enlarged dmdt vs temperature plot "
            f"(default: {DEFAULT_DMDT_TEMP_WINDOW_START_C})."
        ),
    )
    parser.add_argument(
        "--enlarged-scale",
        type=float,
        default=DEFAULT_ENLARGED_SCALE,
        help=(
            "Figure scale factor for enlarged dmdt vs temperature plot "
            f"(default: {DEFAULT_ENLARGED_SCALE})."
        ),
    )
    parser.add_argument(
        "--poster-per-dust",
        action="store_true",
        help="Export one T-dm (temperature vs relative mass) figure per dust to SharePoint diagram/poster using EAFD basis.",
    )
    args = parser.parse_args()

    context = _create_context(
        use_cache=not args.no_cache,
        force_reprocess=args.force_reprocess or args.process_all_no_cache,
    )
    if args.process_all_no_cache:
        print("[Info] Processing all files without cache check...")
        context.process_all_files(force_reprocess=True)

    bases = _selected_bases(args.mass_basis)
    rel_mass_targets_by_basis: dict[str, dict[str, float]] = {}
    for basis in bases:
        rel_mass_targets_by_basis[basis] = compute_equalization_targets(
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            basis=basis,
            lime_column=args.lime_column,
            reference_temp=args.reference_temp,
        )

    df_comp = _load_composition_df(args.dust_composition)
    theory_overlay = _select_theory_overlay_mode(args.theory_overlay, has_comp=df_comp is not None)
    theory_metrics_by_exp = _build_theory_metrics(
        context=context,
        experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
        lime_column=args.lime_column,
        df_comp=df_comp,
        pb_mode=args.pb_mode,
        zn_fraction=args.zn_fraction,
    )

    if args.poster_per_dust:
        export_poster_dm_temperature_per_dust(
            context=context,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            lime_column=args.lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            dpi=args.dpi,
            inkscape_path=args.inkscape_path,
            reference_temp=args.reference_temp,
        )
        return

    save_path = Path(args.save)
    if not save_path.is_absolute():
        save_path = context.project_path / save_path
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_stem = save_path.with_suffix("")

    figures: list[tuple[str, str, plt.Figure]] = []
    for basis in bases:
        fig_dmdt_temp = plot_dmdt_temperature_4dust(
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            basis=basis,
            lime_column=args.lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            reference_temp=args.reference_temp,
        )
        fig_dmdt_temp.subplots_adjust(top=0.975,
                                      bottom=0.11,
                                      left=0.11,
                                      right=0.985,
                                      hspace=0,
                                      wspace=0)
        fig_dmdt_temp_enlarged = plot_dmdt_temperature_4dust_enlarged(
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            basis=basis,
            lime_column=args.lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            temperature_min_c=args.dmdt_temp_window_start,
            enlarged_scale=args.enlarged_scale,
            reference_temp=args.reference_temp,
        )
        fig_dmdt_temp_enlarged.subplots_adjust(top=0.985,
                                               bottom=0.095,
                                               left=0.09,
                                               right=0.985,
                                               hspace=0,
                                               wspace=0)
        fig_dm_temp = plot_dm_temperature_4dust(
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            basis=basis,
            lime_column=args.lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            reference_temp=args.reference_temp,
        )
        fig_dm_temp.subplots_adjust(top=0.975,
                                    bottom=0.11,
                                    left=0.125,
                                    right=0.99,
                                    hspace=0,
                                    wspace=0)
        fig_dmdt_dm = plot_dmdt_dm_4dust(
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            rel_mass_targets_by_basis=rel_mass_targets_by_basis,
            basis=basis,
            lime_column=args.lime_column,
            theory_overlay=theory_overlay,
            theory_metrics_by_exp=theory_metrics_by_exp,
            reference_temp=args.reference_temp,
        )
        fig_dmdt_dm.subplots_adjust(top=0.98,
                                    bottom=0.16,
                                    left=0.11,
                                    right=0.98,
                                    hspace=0,
                                    wspace=0)
        figures.extend(
            [
                (basis, "dmdt_vs_temperature", fig_dmdt_temp),
                (
                    basis,
                    f"dmdt_vs_temperature_enlarged_from_{int(round(args.dmdt_temp_window_start))}C",
                    fig_dmdt_temp_enlarged,
                ),
                (basis, "dm_vs_temperature", fig_dm_temp),
                (basis, "dmdt_vs_dm", fig_dmdt_dm),
            ]
        )
        plt.show()

    for basis, metric, fig in figures:
        if len(bases) == 1 and basis == "mixture":
            out_stem = save_stem.parent / f"{save_stem.name}_{metric}"
        else:
            out_stem = save_stem.parent / f"{save_stem.name}_{basis}_{metric}"
        png_path, ppt_svg_path = _save_figure_outputs(
            fig,
            out_stem,
            dpi=args.dpi,
            inkscape_path=args.inkscape_path,
        )
        print(f"[Info] Figure saved to: {png_path}")
        if ppt_svg_path is not None:
            print(f"[Info] PowerPoint SVG saved to: {ppt_svg_path}")

    if args.theory_summary_csv:
        out_csv = Path(args.theory_summary_csv)
        if not out_csv.is_absolute():
            out_csv = context.project_path / out_csv
        _write_theory_summary_csv(
            out_path=out_csv,
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            theory_metrics_by_exp=theory_metrics_by_exp,
            lime_column=args.lime_column,
            bases=bases,
        )

    for _, _, fig in figures:
        plt.close(fig)


if __name__ == "__main__":
    main()
