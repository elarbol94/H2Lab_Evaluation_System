from pathlib import Path

from h2lab_tga_app.config.paths import PathResolver
from h2lab_tga_app.config.settings import Settings


def test_path_resolver_contract_for_selected_folder(tmp_path: Path) -> None:
    selected = tmp_path / "data_folder"
    selected.mkdir(parents=True)

    settings = Settings(sharepoint_root=tmp_path, selected_data_root=selected)
    resolver = PathResolver(settings)

    assert resolver.project_root() == selected
    assert resolver.raw_data_root() == selected
    assert resolver.outputs_root().as_posix().endswith(".h2lab_tga/outputs")
    assert resolver.state_file().as_posix().endswith(".h2lab_tga/state/state.json")
    assert resolver.prep_overrides_file().as_posix().endswith(".h2lab_tga/state/prep_overrides.json")
    assert resolver.tasks_db_path().as_posix().endswith(".h2lab_tga/tasks/tasks.sqlite")

    resolver.ensure_layout()
    assert (resolver.outputs_root() / "runs").exists()
    assert resolver.state_file().parent.exists()


def test_path_resolver_tga_config_lookup(tmp_path: Path) -> None:
    selected = tmp_path / "dataset"
    selected.mkdir(parents=True)
    (selected / "TGA").mkdir(parents=True)
    (selected / "TGA" / "config.json").write_text("{}", encoding="utf-8")

    resolver = PathResolver(Settings(sharepoint_root=tmp_path, selected_data_root=selected))
    assert resolver.tga_config_path() == selected / "TGA" / "config.json"


def test_path_resolver_legacy_project_mode(tmp_path: Path) -> None:
    settings = Settings(sharepoint_root=tmp_path)
    resolver = PathResolver(settings)

    assert resolver.project_root() == tmp_path / "H2Lab_PUB_25_9 Lime in EAFD Recycling"


def test_path_resolver_tga_config_fallback_from_env_root(monkeypatch, tmp_path: Path) -> None:
    project_name = "H2Lab_PUB_25_9 Lime in EAFD Recycling"
    selected = tmp_path / "OneDriveMirror" / project_name / "TGA" / "data" / "boudouard_equilibrium"
    selected.mkdir(parents=True)

    local_root = tmp_path / "LocalProjects"
    cfg = local_root / project_name / "TGA" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("H2LAB_SHAREPOINT_PATH", str(local_root))
    monkeypatch.setattr(PathResolver, "_local_repo_root", staticmethod(lambda: None))
    settings = Settings(
        sharepoint_root=tmp_path,
        project_rel_path="boudouard_equilibrium",
        selected_data_root=selected,
    )
    resolver = PathResolver(settings)

    assert resolver.tga_config_path() == cfg


def test_path_resolver_prefix_normalization_for_project_lookup(monkeypatch, tmp_path: Path) -> None:
    selected = tmp_path / "OneDriveMirror" / "PUB_25_9 Lime in EAFD Recycling" / "TGA" / "data"
    selected.mkdir(parents=True)

    local_root = tmp_path / "LocalProjects"
    cfg = local_root / "H2Lab_PUB_25_9 Lime in EAFD Recycling" / "TGA" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("H2LAB_SHAREPOINT_PATH", str(tmp_path / "OneDriveMirror"))
    monkeypatch.setenv("H2LAB_LOCAL_REPO_ROOT", str(local_root))
    monkeypatch.setattr(PathResolver, "_local_repo_root", staticmethod(lambda: None))
    monkeypatch.setattr(PathResolver, "_common_local_roots", staticmethod(lambda: []))
    settings = Settings(
        sharepoint_root=tmp_path,
        project_rel_path="PUB_25_9 Lime in EAFD Recycling",
        selected_data_root=selected,
    )
    resolver = PathResolver(settings)

    assert resolver.tga_config_path() == cfg


def test_path_resolver_missing_fallback_prefers_local_repo_candidate(monkeypatch, tmp_path: Path) -> None:
    selected = tmp_path / "OneDriveMirror" / "PUB_25_9 Lime in EAFD Recycling" / "TGA" / "data"
    selected.mkdir(parents=True)
    local_root = tmp_path / "LocalProjects"
    local_root.mkdir(parents=True)

    monkeypatch.setenv("H2LAB_LOCAL_REPO_ROOT", str(local_root))
    monkeypatch.setattr(PathResolver, "_local_repo_root", staticmethod(lambda: None))
    monkeypatch.setattr(PathResolver, "_common_local_roots", staticmethod(lambda: []))
    settings = Settings(
        sharepoint_root=tmp_path,
        project_rel_path="PUB_25_9 Lime in EAFD Recycling",
        selected_data_root=selected,
    )
    resolver = PathResolver(settings)

    assert (
        resolver.tga_config_path()
        == local_root / "PUB_25_9 Lime in EAFD Recycling" / "TGA" / "config.json"
    )


def test_path_resolver_tga_config_env_root_precedes_common_root(monkeypatch, tmp_path: Path) -> None:
    project_name = "H2Lab_PUB_25_9 Lime in EAFD Recycling"
    selected = tmp_path / "OneDriveMirror" / project_name / "TGA" / "data"
    selected.mkdir(parents=True)

    env_root = tmp_path / "EnvRoot"
    env_cfg = env_root / project_name / "TGA" / "config.json"
    env_cfg.parent.mkdir(parents=True)
    env_cfg.write_text("{\"source\":\"env\"}", encoding="utf-8")

    common_root = tmp_path / "CommonRoot"
    common_cfg = common_root / project_name / "TGA" / "config.json"
    common_cfg.parent.mkdir(parents=True)
    common_cfg.write_text("{\"source\":\"common\"}", encoding="utf-8")

    monkeypatch.setenv("H2LAB_SHAREPOINT_PATH", str(env_root))
    monkeypatch.setattr(PathResolver, "_common_local_roots", staticmethod(lambda: [common_root]))
    monkeypatch.setattr(PathResolver, "_local_repo_root", staticmethod(lambda: None))

    settings = Settings(
        sharepoint_root=tmp_path,
        project_rel_path="dataset",
        selected_data_root=selected,
    )
    resolver = PathResolver(settings)

    assert resolver.tga_config_path() == env_cfg


def test_path_resolver_tga_config_no_project_name_keeps_default(tmp_path: Path) -> None:
    selected = tmp_path / "dataset" / "raw"
    selected.mkdir(parents=True)

    settings = Settings(
        sharepoint_root=tmp_path,
        project_rel_path="dataset/raw",
        selected_data_root=selected,
    )
    resolver = PathResolver(settings)

    assert resolver.tga_config_path() == selected / "TGA" / "config.json"


def test_path_resolver_exposes_canonical_project_key(tmp_path: Path) -> None:
    selected = tmp_path / "OneDriveMirror" / "H2Lab_PUB_25_9 Lime in EAFD Recycling" / "TGA"
    selected.mkdir(parents=True)
    settings = Settings(sharepoint_root=tmp_path, selected_data_root=selected)
    resolver = PathResolver(settings)

    assert resolver.inferred_project_key() == "pub_25_9 lime in eafd recycling"
