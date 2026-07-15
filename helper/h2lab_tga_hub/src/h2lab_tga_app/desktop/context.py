from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from h2lab_tga_app.config.paths import PathResolver
from h2lab_tga_app.config.settings import Settings, load_settings
from h2lab_tga_app.infra.state_store import JsonStateStore
from h2lab_tga_app.infra.task_store import SQLiteTaskStore
from h2lab_tga_app.infra.prep_config_store import PrepConfigStore
from h2lab_tga_app.pipeline.orchestrator import PipelineOrchestrator
from h2lab_tga_app.pipeline.tga_adapter import TGAProcessor
from h2lab_tga_app.services.catalog_service import CatalogService
from h2lab_tga_app.services.prep_config_service import PrepConfigService
from h2lab_tga_app.services.run_service import RunService
from h2lab_tga_app.services.task_service import TaskService


class RunServiceFactory:
    def __init__(
        self,
        resolver: PathResolver,
        state_store: JsonStateStore,
        prep_config_service: PrepConfigService,
    ) -> None:
        self._resolver = resolver
        self._state_store = state_store
        self._prep_config_service = prep_config_service
        self._cached: RunService | None = None

    def get(self) -> RunService:
        if self._cached is not None:
            return self._cached

        processor = TGAProcessor(
            project_root=self._resolver.project_root(),
            config_path=self._resolver.tga_config_path(),
            df_meta=pd.DataFrame(),
            df_comp=pd.DataFrame(),
            prep_config_service=self._prep_config_service,
        )
        orchestrator = PipelineOrchestrator(self._resolver.outputs_root(), processor)
        self._cached = RunService(orchestrator, self._state_store)
        return self._cached


def build_context(
    selected_data_root: str | Path | None = None,
    settings: Settings | None = None,
) -> dict:
    if selected_data_root is not None:
        root = Path(selected_data_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"Selected folder does not exist: {root}")

        base = settings or Settings(sharepoint_root=root)
        settings = replace(
            base,
            sharepoint_root=root,
            project_rel_path=root.name,
            selected_data_root=root,
        )
    else:
        settings = settings or load_settings()

    resolver = PathResolver(settings)
    if not resolver.project_root().exists():
        raise RuntimeError(f"Selected folder does not exist: {resolver.project_root()}")
    resolver.ensure_layout()

    state_store = JsonStateStore(resolver.state_file())
    task_store = SQLiteTaskStore(resolver.tasks_db_path())
    prep_store = PrepConfigStore(resolver.prep_overrides_file())
    prep_config_service = PrepConfigService(resolver.tga_config_path(), prep_store)

    return {
        "settings": settings,
        "resolver": resolver,
        "catalog_service": CatalogService(resolver.raw_data_root()),
        "run_service_factory": RunServiceFactory(resolver, state_store, prep_config_service),
        "prep_config_service": prep_config_service,
        "state_store": state_store,
        "task_store": task_store,
        "task_service": TaskService(task_store),
    }
