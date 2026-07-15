from __future__ import annotations

from typing import Any

import pandas as pd


def _decimate_xy(x: pd.Series, y: pd.Series, max_points: int) -> tuple[pd.Series, pd.Series]:
    if max_points <= 0 or len(x) <= max_points:
        return x, y
    step = max(1, len(x) // max_points)
    x_dec = x.iloc[::step]
    y_dec = y.iloc[::step]
    if x_dec.index[-1] != x.index[-1]:
        x_dec = pd.concat([x_dec, x.iloc[[-1]]], ignore_index=False)
        y_dec = pd.concat([y_dec, y.iloc[[-1]]], ignore_index=False)
    return x_dec.reset_index(drop=True), y_dec.reset_index(drop=True)


def build_subplot_figure(
    layout: list[dict[str, str]],
    frames: dict[str, pd.DataFrame],
    *,
    max_points_per_trace: int = 2500,
):
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    warnings: list[str] = []
    if not layout:
        return go.Figure(), ["No subplots configured."]
    if not frames:
        return go.Figure(), ["No processed data available."]

    fig = make_subplots(rows=len(layout), cols=1, subplot_titles=[spec.get("title", "") for spec in layout])

    for row_idx, spec in enumerate(layout, start=1):
        x_col = spec["x_col"]
        y_col = spec["y_col"]
        plotted = False
        for exp_id, frame in frames.items():
            if x_col not in frame.columns or y_col not in frame.columns:
                continue
            if not pd.api.types.is_numeric_dtype(frame[x_col]) or not pd.api.types.is_numeric_dtype(frame[y_col]):
                continue
            x_vals, y_vals = _decimate_xy(frame[x_col], frame[y_col], max_points_per_trace)
            fig.add_trace(
                go.Scatter(
                    x=x_vals,
                    y=y_vals,
                    mode="lines",
                    name=exp_id,
                    legendgroup=exp_id,
                    showlegend=(row_idx == 1),
                ),
                row=row_idx,
                col=1,
            )
            plotted = True

        fig.update_xaxes(title_text=spec.get("x_label", x_col), row=row_idx, col=1)
        fig.update_yaxes(title_text=spec.get("y_label", y_col), row=row_idx, col=1)

        if not plotted:
            warnings.append(
                f"Subplot {row_idx} skipped for all experiments due to missing/non-numeric columns: ({x_col}, {y_col})"
            )

    fig.update_layout(height=max(450, 320 * len(layout)), template="plotly_white")
    return fig, warnings


def to_layout_payload(layout: list[dict[str, Any]]) -> dict[str, Any]:
    return {"subplot_layout": layout}


def build_stage_preview_figure(
    stage_frames: dict[str, pd.DataFrame],
    *,
    max_points_per_trace: int = 1800,
):
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    warnings: list[str] = []
    if not stage_frames:
        return go.Figure(), ["No stage preview data available."]

    stage_order = ["raw_loaded", "pre_filtered", "derived", "post_filtered", "final_cut"]
    present = [name for name in stage_order if name in stage_frames]
    if not present:
        present = sorted(stage_frames.keys())

    specs = [
        ("temperature_C", "dm_original_mg", "Mass Delta [mg]"),
        ("temperature_C", "dm_filtered_pct", "Relative Mass [%]"),
        ("temperature_C", "dmdt_filtered_pctmin", "Reaction Kinetics [%/min]"),
    ]
    fig = make_subplots(rows=len(specs), cols=1, subplot_titles=[title for _, _, title in specs])

    for row_idx, (x_col, y_col, y_label) in enumerate(specs, start=1):
        plotted = False
        for stage_name in present:
            frame = stage_frames.get(stage_name)
            if frame is None or x_col not in frame.columns or y_col not in frame.columns:
                continue
            if not pd.api.types.is_numeric_dtype(frame[x_col]) or not pd.api.types.is_numeric_dtype(frame[y_col]):
                continue
            x_vals, y_vals = _decimate_xy(frame[x_col], frame[y_col], max_points_per_trace)
            fig.add_trace(
                go.Scatter(
                    x=x_vals,
                    y=y_vals,
                    mode="lines",
                    name=stage_name,
                    legendgroup=stage_name,
                    showlegend=(row_idx == 1),
                ),
                row=row_idx,
                col=1,
            )
            plotted = True
        fig.update_xaxes(title_text="Temperature [°C]", row=row_idx, col=1)
        fig.update_yaxes(title_text=y_label, row=row_idx, col=1)
        if not plotted:
            warnings.append(f"Could not plot row {row_idx}: missing columns ({x_col}, {y_col}).")

    fig.update_layout(height=980, template="plotly_white")
    return fig, warnings
