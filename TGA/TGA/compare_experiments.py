import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from helper.TGA import PreparationConfig, TGAExperiment, GoogleSheetLoader
from setting import get_path_for_folder

try:
    from setting import (
        apply_pub259_plot_style,
        get_pub259_plot_size_in,
        resolve_pub259_figure_stem,
        resolve_pub259_mirrored_presentation_png_path,
    )
except Exception:
    def get_pub259_plot_size_in() -> tuple[float, float]:
        return (13.333, 7.5)

    def apply_pub259_plot_style(font_size: int = 11) -> None:
        plt.rcParams.update(
            {
                "font.family": "Arial",
                "font.size": font_size,
                "axes.labelsize": font_size,
                "legend.fontsize": 8,
                "xtick.labelsize": font_size,
                "ytick.labelsize": font_size,
                "figure.figsize": get_pub259_plot_size_in(),
            }
        )

    def resolve_pub259_mirrored_presentation_png_path(path):
        return None


FIGURE_TARGET = "paper"  # options: "paper", "presentation"
FIGURE_DOMAIN = "TGA"  # options: "EMI", "TGA", "SEM", "Composition", "analysis"

def _match_experiment_id(stem: str, experiment_id: str) -> Optional[str]:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(experiment_id)}(?:_\d+)?(?![A-Za-z0-9])"
    )
    match = pattern.search(stem)
    return match.group(0) if match else None


def _find_experiment_files(experiment_id: str, data_dir: Path) -> List[Path]:
    matches = []
    for p in data_dir.glob("*.txt"):
        if _match_experiment_id(p.stem, experiment_id):
            matches.append(p)
    if not matches:
        raise FileNotFoundError(
            f"No matching .txt files found for experiment ID '{experiment_id}' in {data_dir}"
        )
    return sorted(matches)


def _get_metadata_rows(df_meta: pd.DataFrame, experiment_id: str) -> pd.DataFrame:
    if "id" not in df_meta.columns:
        raise KeyError("'id' column missing in metadata sheet")
    pattern = re.compile(rf"^{re.escape(experiment_id)}(_\d+)?$")
    mask = df_meta["id"].fillna("").astype(str).str.match(pattern)
    meta = df_meta.loc[mask].copy()
    if meta.empty:
        print(f"[Warning] No metadata entry found for '{experiment_id}'")
    return meta


def _pick_column(df: pd.DataFrame, key: str) -> str:
    matches = [c for c in df.columns if key in c]
    if not matches:
        raise ValueError(f"Expected column containing '{key}' not found. Columns: {list(df.columns)}")
    return matches[0]


def _extract_replicate_series(
    df: pd.DataFrame,
    temp_col: str,
    dm_key: str,
    dmdt_key: str,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    dm_col = _pick_column(df, dm_key)
    dmdt_col = _pick_column(df, dmdt_key)
    subset = df[[temp_col, dm_col, dmdt_col]].dropna()
    if subset.empty:
        return np.array([]), np.array([]), np.array([])
    subset = subset.groupby(temp_col, as_index=False).mean()
    subset = subset.sort_values(temp_col)
    t = subset[temp_col].to_numpy()
    dm = subset[dm_col].to_numpy()
    dmdt = subset[dmdt_col].to_numpy()
    return t, dm, dmdt


def _mean_replicates_on_grid(
    dfs: List[pd.DataFrame],
    temp_col: str = "temperature_C",
    dm_key: str = "dm_filtered_pct",
    dmdt_key: str = "dmdt_filtered_pctmin",
    step_c: float = 1.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    series = []
    for df in dfs:
        t, dm, dmdt = _extract_replicate_series(df, temp_col, dm_key, dmdt_key)
        if t.size < 2:
            continue
        series.append((t, dm, dmdt))

    if not series:
        raise ValueError("No valid replicate data to average.")

    t_min = max(np.min(t) for t, _, _ in series)
    t_max = min(np.max(t) for t, _, _ in series)

    start = float(np.ceil(t_min))
    stop = float(np.floor(t_max))
    if stop < start:
        start, stop = t_min, t_max
    grid = np.arange(start, stop + step_c, step_c)
    if grid.size == 0:
        grid = np.array([t_min], dtype=float)

    dm_stack = []
    dmdt_stack = []
    for t, dm, dmdt in series:
        dm_stack.append(np.interp(grid, t, dm))
        dmdt_stack.append(np.interp(grid, t, dmdt))

    dm_arr = np.vstack(dm_stack)
    dmdt_arr = np.vstack(dmdt_stack)

    dm_mean = np.nanmean(dm_arr, axis=0)
    dm_std = np.nanstd(dm_arr, axis=0)
    dmdt_mean = np.nanmean(dmdt_arr, axis=0)
    dmdt_std = np.nanstd(dmdt_arr, axis=0)

    return grid, dm_mean, dm_std, dmdt_mean, dmdt_std


def _build_metadata_table(
    experiment_ids: Iterable[str],
    df_meta: pd.DataFrame,
    metadata_columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    rows = []
    for exp_id in experiment_ids:
        if not exp_id or exp_id == "-":
            continue
        meta_rows = _get_metadata_rows(df_meta, exp_id)
        if meta_rows.empty:
            rows.append({"id": exp_id})
            continue
        row = meta_rows.iloc[0].to_dict()
        if "id" not in row:
            row["id"] = exp_id
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    table = pd.DataFrame(rows)
    default_cols = ["id", "material", "Lime m-%", "Suffix"]
    columns = metadata_columns or default_cols
    cols = [c for c in columns if c in table.columns]
    if "id" in table.columns and "id" not in cols:
        cols = ["id"] + cols
    if not cols:
        cols = list(table.columns)
    return table[cols]


def compare_experiment_groups(
    experiment_groups: List[List[str]],
    project_folder: str = r"PUB_25_9 Lime in EAFD Recycling",
    data_subdir: str = r"TGA/data/boudouard_equilibrium",
    config_path: str = "config.json",
    sheet_name: str = r"H2Lab_PUB_25_9 Lime in EAFD Recycling",
    metadata_columns: Optional[List[str]] = None,
    figsize_per_row: tuple = (12, 2.8),
    save_path: Optional[os.PathLike] = None,
):
    apply_pub259_plot_style(font_size=11)
    # ---- Project setup ----
    config_file = config_path
    prep_cfg = PreparationConfig.load_from_file(str(config_file))
    print("[Info] Configuration file successfully loaded\n")

    project_path = Path(get_path_for_folder(project_folder))
    os.chdir(project_path)

    data_dir = project_path / data_subdir
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")



    sheet_loader = GoogleSheetLoader(show_sheetnames=False)
    df_meta = sheet_loader.load_sheet(sheet_name)

    # ---- Figure layout ----
    n_groups = len(experiment_groups)
    fig_w, fig_h = get_pub259_plot_size_in()
    fig = plt.figure(figsize=(fig_w, fig_h))
    gs = fig.add_gridspec(n_groups, 3, width_ratios=[1.0, 1.0, 0.9], wspace=0.25)

    for row, group in enumerate(experiment_groups):
        ax_mass = fig.add_subplot(gs[row, 0])
        ax_kin = fig.add_subplot(gs[row, 1], sharex=ax_mass)
        ax_meta = fig.add_subplot(gs[row, 2])
        ax_meta.axis("off")

        for i, exp_id in enumerate(group):
            if not exp_id or exp_id == "-":
                continue

            replicate_files = _find_experiment_files(exp_id, data_dir)
            replicate_dfs = []
            for file in replicate_files:
                rep_id = _match_experiment_id(file.stem, exp_id) or exp_id
                exp = TGAExperiment(
                    file_path=str(file),
                    config=prep_cfg,
                    df_meta=df_meta,
                    experiment_id=rep_id,
                )
                replicate_dfs.append(exp.df.copy())

            grid, dm_mean, dm_std, dmdt_mean, dmdt_std = _mean_replicates_on_grid(
                replicate_dfs, step_c=1.0
            )

            meta_rows = _get_metadata_rows(df_meta, exp_id)
            label_suffix = ""
            if not meta_rows.empty and "Lime m-%" in meta_rows.columns:
                lime = meta_rows["Lime m-%"].iloc[0]
                try:
                    lime_pct = int(float(lime) * 100)
                    label_suffix = f", {lime_pct}% CaO"
                except Exception:
                    label_suffix = ""

            label = f"{exp_id} (n={len(replicate_dfs)}{label_suffix})"

            ax_mass.plot(
                grid,
                dm_mean,
                label=label,
                linewidth=1.2,
                color=f"C{i}",
            )
            ax_mass.fill_between(
                grid,
                dm_mean - dm_std,
                dm_mean + dm_std,
                color=f"C{i}",
                alpha=0.2,
                linewidth=0,
            )
            ax_kin.plot(
                grid,
                dmdt_mean,
                label=label,
                linewidth=1.0,
                color=f"C{i}",
            )
            ax_kin.fill_between(
                grid,
                dmdt_mean - dmdt_std,
                dmdt_mean + dmdt_std,
                color=f"C{i}",
                alpha=0.2,
                linewidth=0,
            )

        # Metadata table for this group
        meta_table = _build_metadata_table(group, df_meta, metadata_columns)
        if not meta_table.empty:
            display = meta_table.copy().astype(str)
            table = ax_meta.table(
                cellText=display.values,
                colLabels=display.columns,
                loc="center",
                cellLoc="left",
            )
            table.auto_set_font_size(False)
            table.set_fontsize(7)
            table.scale(1, 1.2)
            ax_meta.set_title("Metadata", fontsize=9)

        # Row label by material (if available)
        if not meta_table.empty and "material" in meta_table.columns:
            material = str(meta_table["material"].iloc[0])
            ax_mass.text(
                -0.12,
                0.5,
                material,
                transform=ax_mass.transAxes,
                fontsize=12,
                va="center",
                ha="right",
            )

        ax_mass.spines["top"].set_visible(False)
        ax_mass.spines["right"].set_visible(False)
        ax_kin.spines["top"].set_visible(False)
        ax_kin.spines["right"].set_visible(False)
        ax_kin.axhline(0, color="grey", linestyle="dashed", linewidth=0.6)

        ax_mass.legend(frameon=False, loc="lower left", fontsize=8)

        if row == n_groups - 1:
            ax_mass.set_xlabel("Temperature [°C]")
            ax_kin.set_xlabel("Temperature [°C]")

        ax_mass.set_ylabel("Relative Mass [%]")
        ax_kin.set_ylabel("Relative Reaction Kinetics [%/min]")

    fig.tight_layout()

    if save_path:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=600)
        mirrored_png_path = resolve_pub259_mirrored_presentation_png_path(save_path)
        if mirrored_png_path is not None:
            mirrored_png_path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(mirrored_png_path, dpi=600)
        print(f"[Info] Figure saved to: {save_path}")
    else:
        plt.show()

    return fig


if __name__ == "__main__":
    experiment_groups = [
        ["RT54", "RT64"],
    ]

    compare_experiment_groups(
        experiment_groups=experiment_groups,
        data_subdir=r"TGA/data/boudouard_equilibrium",
        metadata_columns=["id", "material", "Lime m-%", "Suffix"],
        save_path=resolve_pub259_figure_stem(FIGURE_TARGET, FIGURE_DOMAIN, "compare_experiment_groups").with_suffix(".png"),
    )







