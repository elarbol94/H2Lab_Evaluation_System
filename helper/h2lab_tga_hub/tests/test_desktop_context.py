from pathlib import Path

from h2lab_tga_app.desktop.context import RunServiceFactory, build_context


class _FakeProcessor:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs


def test_build_context_lazy_run_service(tmp_path: Path) -> None:
    data_root = tmp_path / "dataset"
    data_root.mkdir(parents=True)
    ctx = build_context(selected_data_root=data_root)
    assert "run_service_factory" in ctx
    assert ctx["run_service_factory"]._cached is None


def test_run_service_factory_caches_instance(monkeypatch, tmp_path: Path) -> None:
    data_root = tmp_path / "dataset"
    data_root.mkdir(parents=True)
    ctx = build_context(selected_data_root=data_root)

    import h2lab_tga_app.desktop.context as context_mod

    monkeypatch.setattr(context_mod, "TGAProcessor", _FakeProcessor)

    factory: RunServiceFactory = ctx["run_service_factory"]
    first = factory.get()
    second = factory.get()
    assert first is second


def test_build_context_with_selected_data_root(tmp_path: Path) -> None:
    selected = tmp_path / "any_folder"
    selected.mkdir(parents=True)

    ctx = build_context(selected_data_root=selected)
    assert ctx["resolver"].project_root() == selected
