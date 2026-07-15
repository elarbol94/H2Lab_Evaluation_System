from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

UNIT_LABELS = {
    "temperature_C": "Temperature [°C]",
    "time_min": "Time [min]",
    "dm_filtered_pct": "Relative Mass [%]",
    "dm_original_pct": "Relative Mass [%]",
    "dmdt_filtered_pctmin": "Reaction Kinetics [%/min]",
    "dmdt_original_pctmin": "Reaction Kinetics [%/min]",
    "CO": "CO Flowrate [ml/min]",
}


def auto_axis_label(column: str) -> str:
    return UNIT_LABELS.get(column, column)


def make_subplot_spec(x_col: str, y_col: str) -> dict[str, str]:
    return {
        "id": uuid4().hex[:8],
        "x_col": x_col,
        "y_col": y_col,
        "title": f"{y_col} vs {x_col}",
        "x_label": auto_axis_label(x_col),
        "y_label": auto_axis_label(y_col),
    }


def normalize_layout(layout: list[dict[str, str]] | None, available_columns: list[str]) -> list[dict[str, str]]:
    if not available_columns:
        return []

    fallback_x = "temperature_C" if "temperature_C" in available_columns else available_columns[0]
    fallback_y = "dm_filtered_pct" if "dm_filtered_pct" in available_columns else available_columns[min(1, len(available_columns) - 1)]

    if not layout:
        return [make_subplot_spec(fallback_x, fallback_y)]

    normalized = deepcopy(layout)
    for spec in normalized:
        spec.setdefault("id", uuid4().hex[:8])
        x_col = spec.get("x_col", fallback_x)
        y_col = spec.get("y_col", fallback_y)
        spec["x_col"] = x_col if x_col in available_columns else fallback_x
        spec["y_col"] = y_col if y_col in available_columns else fallback_y
        spec.setdefault("title", f"{spec['y_col']} vs {spec['x_col']}")
        spec.setdefault("x_label", auto_axis_label(spec["x_col"]))
        spec.setdefault("y_label", auto_axis_label(spec["y_col"]))
    return normalized
