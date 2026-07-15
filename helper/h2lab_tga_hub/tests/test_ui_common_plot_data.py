import numpy as np
import pandas as pd
import pytest

from h2lab_tga_app.ui_common.plot_data import build_subplot_figure

plotly = pytest.importorskip("plotly")


def test_build_subplot_figure_smoke() -> None:
    frames = {
        "RT1": pd.DataFrame({"temperature_C": [20, 100], "dm_filtered_pct": [100, 90]}),
        "RT2": pd.DataFrame({"temperature_C": [20, 100], "dm_filtered_pct": [100, 92]}),
    }
    layout = [
        {
            "id": "1",
            "x_col": "temperature_C",
            "y_col": "dm_filtered_pct",
            "title": "Mass",
            "x_label": "Temperature [°C]",
            "y_label": "Relative Mass [%]",
        }
    ]

    fig, warnings = build_subplot_figure(layout, frames)
    assert len(fig.data) == 2
    assert warnings == []


def test_build_subplot_figure_decimates_large_traces() -> None:
    n = 12000
    frames = {
        "RT1": pd.DataFrame(
            {
                "temperature_C": np.linspace(20.0, 1200.0, n),
                "dm_filtered_pct": np.linspace(100.0, 70.0, n),
            }
        )
    }
    layout = [
        {
            "id": "1",
            "x_col": "temperature_C",
            "y_col": "dm_filtered_pct",
            "title": "Mass",
            "x_label": "Temperature [°C]",
            "y_label": "Relative Mass [%]",
        }
    ]

    fig, warnings = build_subplot_figure(layout, frames, max_points_per_trace=1000)
    assert warnings == []
    assert len(fig.data) == 1
    assert len(fig.data[0].x) <= 1001
