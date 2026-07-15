from __future__ import annotations

import argparse
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

from Composition.evaluate_dust_composition import load_dust_composition
from TGAplotting import (
    BAR_COLORS,
    DEFAULT_DMDT_TEMP_WINDOW_START_C,
    DEFAULT_ENLARGED_SCALE,
    DEFAULT_EXPERIMENT_GROUPS,
    DEFAULT_LIME_COLUMN,
    FIGURE_DOMAIN,
    FIGURE_TARGET,
    LINE_STYLES,
    PAPER_MODE,
    REFERENCE_TEMPERATURE,
    _create_context,
    _format_material_label,
    _normalize_float,
    _parse_lime_fraction,
    _save_figure_outputs,
    compute_equalization_targets,
    load_experiment,
)
from setting import (
    apply_pub259_plot_style,
    configure_pub259_legend,
    get_paper_figsize,
    get_pub259_plot_font,
    resolve_pub259_figure_stem,
)

DEFAULT_OUT = resolve_pub259_figure_stem(
    FIGURE_TARGET,
    FIGURE_DOMAIN,
    "normalized_zn_4dust",
).with_suffix(".png")
DEFAULT_COMPOSITION_SHEET = 0


def _build_composition_index(df_comp: pd.DataFrame) -> dict[str, pd.Series]:
    required_cols = {"Dust", "ZnO", "CaO", "SiO2"}
    missing = [col for col in required_cols if col not in df_comp.columns]
    if missing:
        raise ValueError(
            f"Composition workbook missing required columns: {missing}. "
            f"Available columns: {list(df_comp.columns)}"
        )

    index: dict[str, pd.Series] = {}
    for _, row in df_comp.iterrows():
        dust = str(row["Dust"]).strip().upper()
        if not dust or dust == "NAN":
            continue
        index[dust] = row
    return index


def _lookup_norm_metadata(
    composition_index: dict[str, pd.Series],
    material: str,
) -> tuple[float, float, float]:
    key = str(material).strip().upper()
    if key not in composition_index:
        raise KeyError(f"No composition row found for material '{material}'.")

    row = composition_index[key]
    zno = _normalize_float(row["ZnO"])
    cao = _normalize_float(row["CaO"])
    sio2 = _normalize_float(row["SiO2"])

    if pd.isna(zno) or zno <= 0:
        raise ValueError(f"Material '{material}' has invalid ZnO value: {row['ZnO']}")
    if pd.isna(cao) or cao <= 0:
        raise ValueError(f"Material '{material}' has invalid CaO value: {row['CaO']}")
    if pd.isna(sio2) or sio2 <= 0:
        raise ValueError(f"Material '{material}' has invalid SiO2 value: {row['SiO2']}")

    return float(zno), float(cao), float(sio2)


def _compute_mixture_basicity(
    *,
    cao_dust: float,
    sio2_dust: float,
    lime_frac: float,
    experiment_id: str,
) -> float:
    if pd.isna(lime_frac) or lime_frac < 0 or lime_frac >= 1:
        raise ValueError(
            f"Experiment '{experiment_id}' has invalid lime fraction for basicity calculation: {lime_frac}"
        )

    dust_frac = 1.0 - float(lime_frac)
    mixture_cao = cao_dust * dust_frac + 100.0 * float(lime_frac)
    mixture_sio2 = sio2_dust * dust_frac
    if mixture_sio2 <= 0:
        raise ValueError(f"Experiment '{experiment_id}' has non-positive mixture SiO2 for basicity calculation.")
    return mixture_cao / mixture_sio2


def _plot_metric_4dust_normalized(
    *,
    context,
    experiment_groups: list[list[str]],
    rel_mass_targets_by_basis: dict[str, dict[str, float]],
    composition_index: dict[str, pd.Series],
    y_mode: str,
    y_label: str,
    draw_zero_line: bool,
    x_label: str,
    lime_column: str,
    reference_temp: float,
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

    fig, axes = plt.subplots(2, 2, figsize=active_size, sharex=True, sharey=True)
    axes_flat = axes.flatten()

    for group_idx, (group, ax) in enumerate(zip(experiment_groups, axes_flat)):
        dust_color = BAR_COLORS[group_idx % len(BAR_COLORS)]
        material_label = f"Group {group_idx + 1}"
        for candidate in group:
            if candidate == "-":
                continue
            meta_rows = context.get_metadata_rows(candidate)
            if not meta_rows.empty and "material" in meta_rows.columns:
                material_label = _format_material_label(str(meta_rows["material"].iloc[0]))
                break

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
            if df_meta_single.empty or "material" not in df_meta_single.columns:
                raise ValueError(f"Missing material metadata for experiment '{exp_id}'.")

            material = str(df_meta_single["material"].iloc[0])
            zno, cao_dust, sio2_dust = _lookup_norm_metadata(composition_index, material)
            lime_frac = _parse_lime_fraction(df_meta_single, lime_column, exp_id)
            basicity = _compute_mixture_basicity(
                cao_dust=cao_dust,
                sio2_dust=sio2_dust,
                lime_frac=lime_frac,
                experiment_id=exp_id,
            )

            if y_mode == "dm_filtered_pct":
                y = df["dm_filtered_pct"] / zno
            elif y_mode == "dmdt_filtered_pctmin":
                y = df["dmdt_filtered_pctmin"] / zno
            else:
                raise ValueError(f"Unsupported y_mode: {y_mode}")

            ax.plot(
                df["temperature_C"],
                y,
                color=dust_color,
                linestyle=LINE_STYLES[line_idx % len(LINE_STYLES)],
                linewidth=1.2,
                label=f"CaO/SiO2 = {basicity:.2f}",
            )

        if draw_zero_line:
            ax.axhline(y=0, color="grey", linestyle="--", linewidth=0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        configure_pub259_legend(
            ax,
            title=material_label,
            loc="lower left",
        )

    axes[1, 0].set_xlabel(x_label)
    axes[1, 1].set_xlabel(x_label)
    axes[0, 0].set_ylabel(y_label)
    axes[1, 0].set_ylabel(y_label)
    return fig


def plot_dm_temperature_4dust_normalized(
    *,
    context,
    experiment_groups: list[list[str]],
    rel_mass_targets_by_basis: dict[str, dict[str, float]],
    composition_index: dict[str, pd.Series],
    lime_column: str,
    reference_temp: float = REFERENCE_TEMPERATURE,
) -> plt.Figure:
    return _plot_metric_4dust_normalized(
        context=context,
        experiment_groups=experiment_groups,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        composition_index=composition_index,
        y_mode="dm_filtered_pct",
        y_label="Relative Mass / ZnO\n[% per wt% ZnO]",
        draw_zero_line=False,
        x_label="Temperature [°C]",
        lime_column=lime_column,
        reference_temp=reference_temp,
    )


def plot_dmdt_temperature_4dust_normalized_enlarged(
    *,
    context,
    experiment_groups: list[list[str]],
    rel_mass_targets_by_basis: dict[str, dict[str, float]],
    composition_index: dict[str, pd.Series],
    lime_column: str,
    reference_temp: float = REFERENCE_TEMPERATURE,
    temperature_min_c: float = DEFAULT_DMDT_TEMP_WINDOW_START_C,
    enlarged_scale: float = DEFAULT_ENLARGED_SCALE,
) -> plt.Figure:
    base_w, base_h = get_paper_figsize(PAPER_MODE)
    scale = enlarged_scale if enlarged_scale > 0 else 1.0
    fig = _plot_metric_4dust_normalized(
        context=context,
        experiment_groups=experiment_groups,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        composition_index=composition_index,
        y_mode="dmdt_filtered_pctmin",
        y_label="Reaction Kinetics / ZnO\n[%/min per wt% ZnO]",
        draw_zero_line=True,
        x_label="Temperature [°C]",
        lime_column=lime_column,
        reference_temp=reference_temp,
        figsize=(base_w * scale, base_h * scale),
    )

    for ax in fig.axes:
        x0, x1 = ax.get_xlim()
        xmax = max(x0, x1)
        if xmax > temperature_min_c:
            ax.set_xlim(float(temperature_min_c), float(xmax))
    return fig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create ZnO-normalized 4-dust TGA plots with CaO/SiO2 legend labels."
    )
    parser.add_argument(
        "--reference-temp",
        type=float,
        default=REFERENCE_TEMPERATURE,
        help=f"Reference temperature for mass equalization (default: {REFERENCE_TEMPERATURE}).",
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
        help="Optional path to the dust composition workbook.",
    )
    parser.add_argument(
        "--composition-sheet",
        type=str,
        default=str(DEFAULT_COMPOSITION_SHEET),
        help="Dust composition workbook sheet name or index (default: 0).",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=str(DEFAULT_OUT),
        help=(
            f"Output image stem/file (default: {DEFAULT_OUT}). "
            "Saves separate PNG and PowerPoint SVG outputs for both plots."
        ),
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
        help="Do not open plot windows.",
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
    args = parser.parse_args()

    context = _create_context(
        use_cache=not args.no_cache,
        force_reprocess=args.force_reprocess or args.process_all_no_cache,
    )
    if args.process_all_no_cache:
        print("[Info] Processing all files without cache check...")
        context.process_all_files(force_reprocess=True)

    rel_mass_targets_by_basis = {
        "mixture": compute_equalization_targets(
            context=context,
            experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
            basis="mixture",
            lime_column=args.lime_column,
            reference_temp=args.reference_temp,
        )
    }

    comp_sheet: int | str = (
        int(args.composition_sheet)
        if str(args.composition_sheet).isdigit()
        else args.composition_sheet
    )
    df_comp, comp_path = load_dust_composition(
        sheet_name=comp_sheet,
        excel_path_override=args.dust_composition,
    )
    print(f"[Info] Loaded dust composition workbook: {comp_path}")
    composition_index = _build_composition_index(df_comp)

    save_path = Path(args.save)
    if not save_path.is_absolute():
        save_path = context.project_path / save_path
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_stem = save_path.with_suffix("")

    fig_dm = plot_dm_temperature_4dust_normalized(
        context=context,
        experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        composition_index=composition_index,
        lime_column=args.lime_column,
        reference_temp=args.reference_temp,
    )
    fig_dm.subplots_adjust(top=0.975, bottom=0.11, left=0.125, right=0.99, hspace=0.065, wspace=0.03)

    fig_dmdt = plot_dmdt_temperature_4dust_normalized_enlarged(
        context=context,
        experiment_groups=DEFAULT_EXPERIMENT_GROUPS,
        rel_mass_targets_by_basis=rel_mass_targets_by_basis,
        composition_index=composition_index,
        lime_column=args.lime_column,
        reference_temp=args.reference_temp,
        temperature_min_c=args.dmdt_temp_window_start,
        enlarged_scale=args.enlarged_scale,
    )
    fig_dmdt.subplots_adjust(top=0.985, bottom=0.095, left=0.09, right=0.985, hspace=0.04, wspace=0.035)

    outputs = [
        ("dm_vs_temperature_normalized_zno", fig_dm),
        (
            f"dmdt_vs_temperature_normalized_zno_enlarged_from_{int(round(args.dmdt_temp_window_start))}C",
            fig_dmdt,
        ),
    ]

    for suffix, fig in outputs:
        out_stem = save_stem.parent / f"{save_stem.name}_{suffix}"
        png_path, ppt_svg_path = _save_figure_outputs(
            fig,
            out_stem,
            dpi=args.dpi,
            inkscape_path=args.inkscape_path,
        )
        print(f"[Info] Figure saved to: {png_path}")
        if ppt_svg_path is not None:
            print(f"[Info] PowerPoint SVG saved to: {ppt_svg_path}")

    if not args.no_show:
        plt.show()

    for _, fig in outputs:
        plt.close(fig)


if __name__ == "__main__":
    main()
