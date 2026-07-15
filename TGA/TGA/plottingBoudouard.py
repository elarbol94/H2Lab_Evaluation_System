from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

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

from helper.TGA import GoogleSheetLoader, PreparationConfig, TGAExperiment
from setting import (
    apply_pub259_plot_style,
    configure_pub259_legend,
    get_path_for_folder,
    get_paper_figsize,
    resolve_pub259_figure_stem,
    save_pub259_figure,
)


R = 8.314
DELTA_H = 172_000.0
DELTA_S = 173.0
P0_BAR = 1.013

PROJECT_NAME = "PUB_25_9 Lime in EAFD Recycling"
SHEET_NAME = "H2Lab_PUB_25_9 Lime in EAFD Recycling"
DATA_SUBDIR = Path("TGA") / "data"
DEFAULT_EXPERIMENT_ID = "RT54"
FIGURE_TARGET = "paper"  # options: "paper", "presentation"
FIGURE_DOMAIN = "TGA"  # options: "EMI", "TGA", "SEM", "Composition", "analysis"
PAPER_MODE = "paper_half"  # options: "paper_full", "paper_half"
SUBPLOT_LEFT = 0.14  # options: 0.10..0.20
SUBPLOT_RIGHT = 0.99  # options: 0.95..1.00
SUBPLOT_BOTTOM = 0.17  # options: 0.12..0.22
SUBPLOT_TOP = 0.99  # options: 0.92..1.00


def delta_g(T_K: np.ndarray) -> np.ndarray:
    return DELTA_H - T_K * DELTA_S


def kp(T_K: np.ndarray) -> np.ndarray:
    return np.exp(-delta_g(T_K) / (R * T_K))


def reaction_progress(
    T_K: np.ndarray, y_co0: float = 0.0, y_co20: float = 1.0, p_bar: float = 1.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    k_hat = kp(T_K) * (P0_BAR / p_bar)
    a = 4.0 + k_hat
    b = 4.0 * y_co0 + k_hat * (1.0 - y_co20)
    c = y_co0**2 - k_hat * y_co20

    disc = np.maximum(b * b - 4.0 * a * c, 0.0)
    xi = (-b + np.sqrt(disc)) / (2.0 * a)
    xi = np.clip(xi, -0.5 * y_co0, y_co20)

    y_co_eq = (y_co0 + 2.0 * xi) / (1.0 + xi)
    y_co2_eq = (y_co20 - xi) / (1.0 + xi)
    return y_co_eq, y_co2_eq, xi


def _find_experiment_file(experiment_id: str, data_dir: Path) -> Path:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(experiment_id)}(?:_\d+)?(?![A-Za-z0-9])"
    )
    candidates = sorted(data_dir.glob("*.txt"))
    matches = [path for path in candidates if pattern.search(path.stem)]
    if not matches:
        raise FileNotFoundError(
            f"No matching .txt file found for experiment ID '{experiment_id}' in {data_dir}"
        )
    return matches[0]


def _trim_to_first_tmax(df: pd.DataFrame) -> pd.DataFrame:
    max_temp = df["temperature_C"].max()
    idx = np.where(df["temperature_C"].to_numpy() == max_temp)[0]
    if idx.size == 0:
        return df.copy()
    return df.iloc[: idx[0]].copy()


def _build_real_curve(df: pd.DataFrame) -> pd.DataFrame:
    co_col, co2_col = _resolve_gas_columns(df)

    subset = df[["temperature_C", co_col, co2_col]].copy()
    subset["temperature_C"] = pd.to_numeric(subset["temperature_C"], errors="coerce")
    subset[co_col] = pd.to_numeric(subset[co_col], errors="coerce")
    subset[co2_col] = pd.to_numeric(subset[co2_col], errors="coerce")
    subset = subset.dropna()

    total = subset[co_col] + subset[co2_col]
    subset = subset.loc[total > 0].copy()
    if subset.empty:
        raise ValueError(
            "No valid experimental rows where (CO + CO2) is positive; cannot normalize."
        )

    total = subset[co_col] + subset[co2_col]
    subset["x_CO_real"] = subset[co_col] / total
    subset["x_CO2_real"] = subset[co2_col] / total

    grouped = (
        subset.groupby("temperature_C", as_index=False)[["x_CO_real", "x_CO2_real"]]
        .mean()
        .sort_values("temperature_C")
    )
    return grouped


def _resolve_gas_columns(df: pd.DataFrame) -> tuple[str, str]:
    if "CO" in df.columns and "CO2" in df.columns:
        return "CO", "CO2"
    if "Gas1" in df.columns and "Gas2" in df.columns:
        return "Gas1", "Gas2"
    raise KeyError("Experimental dataframe has neither CO/CO2 nor Gas1/Gas2 columns.")


def _build_program_curve(df: pd.DataFrame) -> pd.DataFrame:
    co_col, co2_col = _resolve_gas_columns(df)
    time_col = "time_min" if "time_min" in df.columns else "time"
    if time_col not in df.columns:
        raise KeyError(f"Expected '{time_col}' column in experiment dataframe.")

    prog = df[[time_col, "temperature_C", co_col, co2_col]].copy()
    prog = prog.rename(columns={time_col: "time_min", co_col: "CO_raw", co2_col: "CO2_raw"})
    prog["time_min"] = pd.to_numeric(prog["time_min"], errors="coerce")
    prog["temperature_C"] = pd.to_numeric(prog["temperature_C"], errors="coerce")
    prog["CO_raw"] = pd.to_numeric(prog["CO_raw"], errors="coerce")
    prog["CO2_raw"] = pd.to_numeric(prog["CO2_raw"], errors="coerce")
    prog = prog.dropna().sort_values("time_min")
    total = prog["CO_raw"] + prog["CO2_raw"]
    prog = prog.loc[total > 0].copy()
    if prog.empty:
        raise ValueError("No valid rows where (CO + CO2) is positive in program data.")
    total = prog["CO_raw"] + prog["CO2_raw"]
    prog["x_CO_real"] = prog["CO_raw"] / total
    prog["x_CO2_real"] = prog["CO2_raw"] / total
    return prog


def _classify_stage(r_co: float, low: float, high: float) -> str:
    if r_co < low:
        return "CO2-rich"
    if r_co > high:
        return "CO-rich"
    return "Transition"


def _detect_program_segments(
    prog_df: pd.DataFrame,
    *,
    low: float,
    high: float,
    min_duration_min: float,
) -> list[dict[str, float | str]]:
    if prog_df.empty:
        return []
    labels = [_classify_stage(float(v), low, high) for v in prog_df["x_CO_real"].to_numpy()]
    times = prog_df["time_min"].to_numpy(dtype=float)

    segments: list[dict[str, float | str]] = []
    start_idx = 0
    for i in range(1, len(labels)):
        if labels[i] != labels[start_idx]:
            segments.append(
                {
                    "label": labels[start_idx],
                    "start": float(times[start_idx]),
                    "end": float(times[i - 1]),
                }
            )
            start_idx = i
    segments.append(
        {
            "label": labels[start_idx],
            "start": float(times[start_idx]),
            "end": float(times[-1]),
        }
    )

    if min_duration_min <= 0 or len(segments) <= 1:
        return segments

    merged = segments.copy()
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, seg in enumerate(merged):
            duration = float(seg["end"]) - float(seg["start"])
            if duration >= min_duration_min:
                continue
            changed = True
            if i == 0:
                merged[i + 1]["start"] = seg["start"]
                del merged[i]
            elif i == len(merged) - 1:
                merged[i - 1]["end"] = seg["end"]
                del merged[i]
            else:
                prev_seg = merged[i - 1]
                next_seg = merged[i + 1]
                if prev_seg["label"] == next_seg["label"]:
                    prev_seg["end"] = next_seg["end"]
                    del merged[i + 1]
                    del merged[i]
                else:
                    prev_dur = float(prev_seg["end"]) - float(prev_seg["start"])
                    next_dur = float(next_seg["end"]) - float(next_seg["start"])
                    if prev_dur >= next_dur:
                        prev_seg["end"] = seg["end"]
                    else:
                        next_seg["start"] = seg["start"]
                    del merged[i]
            break
    return merged


def load_real_equilibrium_curve(
    experiment_id: str, project_name: str = PROJECT_NAME
) -> tuple[pd.DataFrame, pd.DataFrame]:
    prep_cfg = PreparationConfig.load_from_file(str(SCRIPT_DIR / "config.json"))

    try:
        project_path = Path(get_path_for_folder(project_name))
    except TypeError as exc:
        raise RuntimeError(
            "Could not resolve project path because H2LAB_SHAREPOINT_PATH is not set."
        ) from exc
    if not project_path:
        raise RuntimeError("Project path resolution returned empty path.")

    os.chdir(project_path)
    data_dir = project_path / DATA_SUBDIR
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    try:
        sheet_loader = GoogleSheetLoader(show_sheetnames=False)
        df_meta = sheet_loader.load_sheet(SHEET_NAME)
    except Exception as exc:
        print(f"[Warning] Could not load Google Sheet metadata; continuing without it: {exc}")
        df_meta = pd.DataFrame()
    file_path = _find_experiment_file(experiment_id=experiment_id, data_dir=data_dir)

    exp = TGAExperiment(
        file_path=str(file_path),
        config=prep_cfg,
        save_parquet=False,
        df_meta=df_meta,
        experiment_id=experiment_id,
    )
    df = _trim_to_first_tmax(exp.df)
    return _build_real_curve(df), df


def plot_boudouard_equilibrium(
    real_curve_df: pd.DataFrame,
    experiment_id: str,
    experiment_df: pd.DataFrame,
    save_path: Path,
    *,
    layout: str = "program_overview",
    show_stage_bands: bool = True,
    stage_threshold_low: float = 0.2,
    stage_threshold_high: float = 0.8,
    stage_min_duration_min: float = 2.0,
    top_theory_only: bool = False,
    show_plot: bool = True,
    inkscape_path: str | None = None,
) -> None:
    figure_size = get_paper_figsize(PAPER_MODE)
    apply_pub259_plot_style(figsize=figure_size)

    T_range_K = np.linspace(300.0, 1500.0, 500)
    x_co_theory, x_co2_theory, _ = reaction_progress(T_range_K)
    T_range_C = T_range_K - 273.15
    co_experiment_color = "#0072B2"
    co2_experiment_color = "#D55E00"

    base_w, base_h = figure_size
    fig, ax = plt.subplots(1, 1, figsize=(base_w, base_h), constrained_layout=False)
    fig.subplots_adjust(
        top=0.975,
        bottom=0.23,
        left=0.19,
        right=0.99,
        hspace=0.2,
        wspace=0.2
    )

    # Keep only the first subplot from the previous layout behavior.
    if layout == "legacy":
        prog_df = _build_program_curve(experiment_df)
        ax.plot(prog_df["time_min"], prog_df["temperature_C"], color="#404040", linewidth=1.3)
        ax.set_xlabel("Time [min]")
        ax.set_ylabel("Temperature [°C]")
    else:
        eq_co = ax.plot(
            T_range_C,
            x_co_theory * 100,
            color="#808080",
            linewidth=1.2,
            linestyle="--",
            label=r"CO$_{\mathrm{boudouard}}$",
        )[0]
        eq_co2 = ax.plot(
            T_range_C,
            x_co2_theory * 100,
            color="#9A9A9A",
            linewidth=1.2,
            linestyle=":",
            label=r"CO$_{2,\mathrm{boudouard}}$",
        )[0]
        legend_handles = [eq_co, eq_co2]
        if not top_theory_only:
            ex_co = ax.plot(
                real_curve_df["temperature_C"],
                real_curve_df["x_CO_real"] * 100,
                color=co_experiment_color,
                linestyle="-",
                linewidth=1.35,
                label="CO$_{\mathrm{experiment}}$",
            )[0]
            ex_co2 = ax.plot(
                real_curve_df["temperature_C"],
                real_curve_df["x_CO2_real"] * 100,
                color=co2_experiment_color,
                linestyle="-",
                linewidth=1.35,
                label="CO$_{2,\mathrm{experiment}}$",
            )[0]
            legend_handles.extend([ex_co, ex_co2])
        ax.set_xlabel("Temperature [°C]")
        ax.set_ylabel("Gas Fraction [%]")
        ax.set_ylim(0.0, 102)
        configure_pub259_legend(ax, handles=legend_handles, loc="best")

    ax.grid(alpha=0.28, linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    save_stem = save_path.with_suffix("")
    png_path, svg_path, ppt_svg_path = save_pub259_figure(
        fig,
        save_stem,
        png_dpi=600,
        inkscape_path=inkscape_path,
    )
    try:
        if svg_path is not None and svg_path.exists():
            svg_path.unlink()
    except OSError as err:
        print(f"[Warning] Could not remove intermediate SVG '{svg_path}': {err}")
    print(f"[Info] Figure saved to: {png_path}")
    if ppt_svg_path is not None:
        print(f"[Info] PowerPoint SVG saved to: {ppt_svg_path}")

    if show_plot:
        plt.show()
    plt.close(fig)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot theoretical Boudouard equilibrium against one experimental CO/CO2 curve pair."
    )
    parser.add_argument(
        "--experiment-id",
        default=DEFAULT_EXPERIMENT_ID,
        help=f"Experiment ID to load from TGA data (default: {DEFAULT_EXPERIMENT_ID})",
    )
    parser.add_argument(
        "--save-path",
        default=None,
        help="Output path stem/file (default: TGA/diagram/boudouard_equilibrium_<ID>.png).",
    )
    parser.add_argument(
        "--layout",
        choices=["program_overview", "legacy"],
        default="program_overview",  # options: "program_overview", "legacy"
        help="Plot layout (default: program_overview).",
    )
    parser.add_argument(
        "--show-stage-bands",
        action="store_true",
        help="Show detected gas program stage bands in program_overview layout.",
    )
    parser.add_argument(
        "--no-stage-bands",
        dest="show_stage_bands",
        action="store_false",
        help="Hide detected gas program stage bands in program_overview layout.",
    )
    parser.set_defaults(show_stage_bands=True)
    parser.add_argument(
        "--stage-threshold-low",
        type=float,
        default=0.2,
        help="Lower CO-fraction threshold for stage detection (default: 0.2).",
    )
    parser.add_argument(
        "--stage-threshold-high",
        type=float,
        default=0.8,
        help="Upper CO-fraction threshold for stage detection (default: 0.8).",
    )
    parser.add_argument(
        "--stage-min-duration-min",
        type=float,
        default=2.0,
        help="Minimum stage duration in minutes before merging (default: 2.0).",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Save figure only, without opening an interactive window.",
    )
    parser.add_argument(
        "--inkscape-path",
        default=None,
        help="Optional full path to inkscape executable for PPT-safe SVG export.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    save_path = (
        Path(args.save_path)
        if args.save_path
        else resolve_pub259_figure_stem(FIGURE_TARGET, FIGURE_DOMAIN, f"boudouard_equilibrium_{args.experiment_id}").with_suffix(".png")
    )

    real_curve_df, experiment_df = load_real_equilibrium_curve(experiment_id=args.experiment_id)
    plot_boudouard_equilibrium(
        real_curve_df=real_curve_df,
        experiment_id=args.experiment_id,
        experiment_df=experiment_df,
        save_path=save_path,
        layout=args.layout,
        show_stage_bands=args.show_stage_bands,
        stage_threshold_low=args.stage_threshold_low,
        stage_threshold_high=args.stage_threshold_high,
        stage_min_duration_min=args.stage_min_duration_min,
        show_plot=not args.no_show,
        inkscape_path=args.inkscape_path,
    )

    # Additional variant: first subplot shows only theoretical equilibrium curves.
    theory_only_save_path = save_path.with_suffix("")
    theory_only_save_path = theory_only_save_path.parent / f"{theory_only_save_path.name}_top_theory_only.png"
    plot_boudouard_equilibrium(
        real_curve_df=real_curve_df,
        experiment_id=args.experiment_id,
        experiment_df=experiment_df,
        save_path=theory_only_save_path,
        layout=args.layout,
        show_stage_bands=args.show_stage_bands,
        stage_threshold_low=args.stage_threshold_low,
        stage_threshold_high=args.stage_threshold_high,
        stage_min_duration_min=args.stage_min_duration_min,
        top_theory_only=True,
        show_plot=not args.no_show,
        inkscape_path=args.inkscape_path,
    )


if __name__ == "__main__":
    main()













