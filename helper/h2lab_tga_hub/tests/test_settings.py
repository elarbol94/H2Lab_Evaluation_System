from pathlib import Path

from h2lab_tga_app.config.settings import DEFAULT_PROJECT_REL_PATH, load_settings


def test_load_settings_from_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("H2LAB_SHAREPOINT_PATH", str(tmp_path))
    settings = load_settings()
    assert settings.sharepoint_root == tmp_path.resolve()
    assert settings.project_rel_path == DEFAULT_PROJECT_REL_PATH


def test_load_settings_project_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("H2LAB_SHAREPOINT_PATH", str(tmp_path))
    settings = load_settings(project_rel_path="H2Lab_INT_25_1 Controlled Leaching")
    assert settings.project_rel_path == "H2Lab_INT_25_1 Controlled Leaching"
