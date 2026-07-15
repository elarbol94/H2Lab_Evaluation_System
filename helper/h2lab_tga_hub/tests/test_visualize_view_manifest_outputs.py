from pathlib import Path

from h2lab_tga_app.desktop.views.visualize_view import VisualizeView


def test_processed_outputs_from_manifest_prefers_manifest_paths(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run1"
    run_root.mkdir(parents=True)
    out = tmp_path / "data" / "RT1.parquet"
    out.parent.mkdir(parents=True)
    out.write_text("x", encoding="utf-8")

    view = VisualizeView.__new__(VisualizeView)
    view._manifest_data = {"outputs": {"processed": {"RT1": str(out)}}}

    paths = view._processed_outputs_from_manifest(run_root)
    assert paths == {"RT1": str(out)}
