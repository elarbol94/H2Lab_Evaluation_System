from h2lab_tga_app.ui_common.layout import auto_axis_label, normalize_layout


def test_auto_axis_label_defaults() -> None:
    assert auto_axis_label("temperature_C") == "Temperature [°C]"
    assert auto_axis_label("custom_col") == "custom_col"


def test_normalize_layout_fallbacks() -> None:
    cols = ["time_min", "dm_filtered_pct"]
    layout = [{"id": "a", "x_col": "missing_x", "y_col": "missing_y"}]
    normalized = normalize_layout(layout, cols)

    assert normalized[0]["x_col"] == "time_min"
    assert normalized[0]["y_col"] == "dm_filtered_pct"
    assert "title" in normalized[0]
