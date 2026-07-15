from pathlib import Path

from h2lab_tga_app.data.discovery import discover_experiments


def test_discover_experiments(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "abc_RT54_sample.txt").write_text("x", encoding="utf-8")
    (raw / "trial.csv").write_text("a,b\n1,2", encoding="utf-8")

    experiments = discover_experiments(raw)
    ids = [e.id for e in experiments]
    assert "RT54" in ids
    assert any(e.file_path.suffix == ".csv" for e in experiments)


def test_discovery_skips_generated_and_metadata_files(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir(parents=True)
    (root / "input_RT1.txt").write_text("x", encoding="utf-8")
    (root / "input_RT1.processed.parquet").write_text("x", encoding="utf-8")
    (root / ".h2lab_tga").mkdir(parents=True)
    (root / ".h2lab_tga" / "hidden_RT2.txt").write_text("x", encoding="utf-8")

    experiments = discover_experiments(root)
    files = [e.file_path.name for e in experiments]

    assert "input_RT1.txt" in files
    assert "input_RT1.processed.parquet" not in files
    assert "hidden_RT2.txt" not in files
