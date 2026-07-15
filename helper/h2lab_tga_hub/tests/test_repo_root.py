from pathlib import Path

import pytest

from h2lab_tga_app.infra.repo_root import find_repo_root


def test_find_repo_root_from_deep_path(tmp_path: Path) -> None:
    (tmp_path / "helper").mkdir()
    (tmp_path / "helper" / "TGA.py").write_text("# marker", encoding="utf-8")

    deep = tmp_path / "helper" / "h2lab_tga_hub" / "src" / "h2lab_tga_app" / "infra"
    deep.mkdir(parents=True)
    root = find_repo_root(deep)

    assert root == tmp_path


def test_find_repo_root_raises_when_marker_missing(tmp_path: Path) -> None:
    start = tmp_path / "a" / "b" / "c"
    start.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="helper/TGA.py"):
        find_repo_root(start)
