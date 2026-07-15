from pathlib import Path

import json

from h2lab_tga_app.infra.prep_config_store import PrepConfigStore
from h2lab_tga_app.services.prep_config_service import PrepConfigService


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    payload = {
        "process_file": "yes",
        "pre_filter": [{"use": "yes", "type": "MovingAverage", "params": {"sampling_rate": 10}}],
        "post_filter": [{"use": "yes", "type": "Butterworth", "params": {"cutoff": 0.002, "order": 2}}],
        "cut_reactive": {"lower_temp": 50, "upper_temp": 1200},
        "cut_tail": {"enabled": False, "threshold": -0.8, "buffer": 200},
        "temperature_correction": {"use": "no", "calibration_file": "TemperatureCalibration.json"},
        "gas_columns": {"Gas1": "CO"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_prep_config_service_merges_overrides(tmp_path: Path) -> None:
    service = PrepConfigService(_config_path(tmp_path), PrepConfigStore(tmp_path / "prep_overrides.json"))
    source = tmp_path / "RT54.txt"
    source.write_text("x", encoding="utf-8")

    service.save_override(source, {"cut_reactive": {"lower_temp": 75}})
    effective = service.get_effective_config(source)

    assert effective["cut_reactive"]["lower_temp"] == 75
    assert effective["cut_reactive"]["upper_temp"] == 1200


def test_prep_config_service_save_effective_config_reduces_to_diff(tmp_path: Path) -> None:
    store = PrepConfigStore(tmp_path / "prep_overrides.json")
    service = PrepConfigService(_config_path(tmp_path), store)
    source = tmp_path / "RT55.txt"
    source.write_text("x", encoding="utf-8")

    effective = service.default_config()
    effective["cut_reactive"]["upper_temp"] = 1100
    service.save_effective_config(source, effective)

    override = store.get_override(source)
    assert override == {"cut_reactive": {"upper_temp": 1100}}


def test_prep_config_service_rejects_unknown_keys(tmp_path: Path) -> None:
    service = PrepConfigService(_config_path(tmp_path), PrepConfigStore(tmp_path / "prep_overrides.json"))
    source = tmp_path / "RT56.txt"
    source.write_text("x", encoding="utf-8")

    try:
        service.save_override(source, {"unknown_field": 1})
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for unknown_field")


def test_prep_config_service_allows_temperature_correction_override(tmp_path: Path) -> None:
    service = PrepConfigService(_config_path(tmp_path), PrepConfigStore(tmp_path / "prep_overrides.json"))
    source = tmp_path / "RT57.txt"
    source.write_text("x", encoding="utf-8")

    service.save_override(source, {"temperature_correction": {"use": "yes"}})
    effective = service.get_effective_config(source)

    assert effective["temperature_correction"]["use"] == "yes"
    assert effective["temperature_correction"]["calibration_file"] == "TemperatureCalibration.json"
