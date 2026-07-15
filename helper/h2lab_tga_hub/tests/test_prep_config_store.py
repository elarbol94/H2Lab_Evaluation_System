from pathlib import Path

from h2lab_tga_app.infra.prep_config_store import PrepConfigStore


def test_prep_config_store_roundtrip(tmp_path: Path) -> None:
    store = PrepConfigStore(tmp_path / "prep_overrides.json")
    source = tmp_path / "RT54.txt"
    source.write_text("x", encoding="utf-8")

    store.set_default_config_path(tmp_path / "config.json")
    store.save_override(source, {"cut_reactive": {"lower_temp": 80}})

    loaded = store.get_override(source)
    assert loaded["cut_reactive"]["lower_temp"] == 80

    store.delete_override(source)
    assert store.get_override(source) == {}


def test_prep_config_store_normalizes_path_keys(tmp_path: Path) -> None:
    store = PrepConfigStore(tmp_path / "prep_overrides.json")
    source = tmp_path / "nested" / "RT55.txt"
    source.parent.mkdir(parents=True)
    source.write_text("x", encoding="utf-8")

    store.save_override(source, {"process_file": "yes"})
    relative = source.relative_to(tmp_path)
    loaded = store.get_override(tmp_path / relative)

    assert loaded["process_file"] == "yes"
