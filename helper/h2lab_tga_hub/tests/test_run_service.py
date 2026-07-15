from pathlib import Path

from h2lab_tga_app.infra.state_store import JsonStateStore


def test_state_store_roundtrip(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")
    store.save_recent_run("run1", {"reference_temp_c": 950.0})
    state = store.load_recent_state()
    assert state["recent_runs"][0] == "run1"
    assert state["last_config"]["reference_temp_c"] == 950.0


def test_layout_preset_lifecycle(tmp_path: Path) -> None:
    store = JsonStateStore(tmp_path / "state.json")
    store.save_layout_preset("legacy_default", {"subplot_layout": [{"id": "1"}]})
    presets = store.load_layout_presets()
    assert "legacy_default" in presets
    assert presets["legacy_default"]["subplot_layout"][0]["id"] == "1"

    store.delete_layout_preset("legacy_default")
    presets_after = store.load_layout_presets()
    assert "legacy_default" not in presets_after
