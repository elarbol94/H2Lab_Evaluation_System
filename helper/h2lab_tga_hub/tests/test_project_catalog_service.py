from pathlib import Path

from h2lab_tga_app.services.project_catalog_service import ProjectCatalogService


def test_project_catalog_discovers_valid_h2lab_projects(tmp_path: Path) -> None:
    valid = tmp_path / "H2Lab_PUB_25_9 Lime in EAFD Recycling"
    (valid / "app" / "data" / "raw_data").mkdir(parents=True)
    (valid / "TGA").mkdir(parents=True)
    (valid / "TGA" / "config.json").write_text("{}", encoding="utf-8")

    no_raw = tmp_path / "H2Lab_INT_25_8 Metal Bath Carbon"
    no_raw.mkdir()

    not_h2lab = tmp_path / "sandbox"
    (not_h2lab / "app" / "data" / "raw_data").mkdir(parents=True)

    catalog = ProjectCatalogService(tmp_path)
    projects = catalog.list_projects()

    assert len(projects) == 1
    assert projects[0].name == valid.name
    assert projects[0].has_tga_config is True


def test_project_catalog_marks_missing_tga_config(tmp_path: Path) -> None:
    folder = tmp_path / "H2Lab_INT_25_3 TGA Documentation Program"
    (folder / "app" / "data" / "raw_data").mkdir(parents=True)

    projects = ProjectCatalogService(tmp_path).list_projects()
    assert len(projects) == 1
    assert projects[0].has_tga_config is False


def test_project_catalog_accepts_tga_only_projects(tmp_path: Path) -> None:
    folder = tmp_path / "H2Lab_INT_25_1 Controlled Leaching"
    (folder / "TGA").mkdir(parents=True)

    projects = ProjectCatalogService(tmp_path).list_projects()
    assert len(projects) == 1
    assert projects[0].name == folder.name


def test_project_catalog_accepts_non_prefixed_project_names(tmp_path: Path) -> None:
    prefixed = tmp_path / "H2Lab_PUB_25_9 Lime in EAFD Recycling"
    (prefixed / "TGA").mkdir(parents=True)

    non_prefixed = tmp_path / "PUB_25_10 Pyro Hydro Combination"
    (non_prefixed / "app" / "data" / "raw_data").mkdir(parents=True)

    unrelated = tmp_path / "sandbox"
    (unrelated / "app" / "data" / "raw_data").mkdir(parents=True)

    projects = ProjectCatalogService(tmp_path).list_projects()
    names = [project.name for project in projects]
    assert "H2Lab_PUB_25_9 Lime in EAFD Recycling" in names
    assert "PUB_25_10 Pyro Hydro Combination" in names
    assert "sandbox" not in names
