from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import pandas as pd

from h2lab_tga_app.config.paths import PathResolver
from h2lab_tga_app.config.settings import Settings, load_settings
from h2lab_tga_app.domain.models import RunConfig
from h2lab_tga_app.infra.prep_config_store import PrepConfigStore
from h2lab_tga_app.infra.state_store import JsonStateStore
from h2lab_tga_app.pipeline.orchestrator import PipelineOrchestrator
from h2lab_tga_app.pipeline.tga_adapter import TGAProcessor
from h2lab_tga_app.services.catalog_service import CatalogService
from h2lab_tga_app.services.prep_config_service import PrepConfigService
from h2lab_tga_app.services.run_service import RunService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run H2Lab TGA processing batch from CLI.")
    parser.add_argument(
        "--data-root",
        type=str,
        default="",
        help="Absolute or relative folder to scan for experiments.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default="",
        help="Legacy mode: project folder name under H2LAB_SHAREPOINT_PATH.",
    )
    parser.add_argument(
        "--experiments",
        type=str,
        default="",
        help="Comma-separated experiment IDs (for example: RT54,RT55). Empty means all.",
    )
    parser.add_argument(
        "--reference-temp-c",
        type=float,
        default=950.0,
        help="Reference temperature in degC for equalization anchor metadata.",
    )
    parser.add_argument(
        "--no-theory",
        action="store_true",
        help="Disable theoretical mass-loss computation.",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Disable quicklook plot export.",
    )
    return parser


def _settings_from_args(args: argparse.Namespace) -> Settings:
    if args.data_root:
        root = Path(args.data_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"Selected data root does not exist: {root}")
        return Settings(
            sharepoint_root=root,
            project_rel_path=root.name,
            selected_data_root=root,
        )
    return load_settings(project_rel_path=args.project or None)


def make_run_cli(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        settings = _settings_from_args(args)
    except RuntimeError as exc:
        print(str(exc))
        return 1

    resolver = PathResolver(settings)
    if not resolver.project_root().exists():
        print(f"Selected data root does not exist: {resolver.project_root()}")
        return 1
    resolver.ensure_layout()

    state_store = JsonStateStore(resolver.state_file())
    prep_store = PrepConfigStore(resolver.prep_overrides_file())
    prep_config_service = PrepConfigService(resolver.tga_config_path(), prep_store)

    processor = TGAProcessor(
        project_root=resolver.project_root(),
        config_path=resolver.tga_config_path(),
        df_meta=pd.DataFrame(),
        df_comp=pd.DataFrame(),
        prep_config_service=prep_config_service,
    )
    run_service = RunService(
        PipelineOrchestrator(resolver.outputs_root(), processor),
        state_store,
    )
    catalog = CatalogService(resolver.raw_data_root())
    experiments = catalog.list_experiments()
    if not experiments:
        print(f"No experiments found in {resolver.raw_data_root()}")
        return 1

    selected_ids = {s.strip().upper() for s in args.experiments.split(",") if s.strip()}
    if selected_ids:
        experiments = [e for e in experiments if e.id.upper() in selected_ids]
        missing = sorted(selected_ids - {e.id.upper() for e in experiments})
        if missing:
            print(f"Warning: requested IDs not found: {', '.join(missing)}")
    if not experiments:
        print("No matching experiments selected.")
        return 1

    cfg = RunConfig(
        reference_temp_c=args.reference_temp_c,
        compute_theory=not args.no_theory,
        save_plots=not args.no_plots,
    )
    artifact = run_service.run(experiments, cfg)
    print(f"Run ID: {artifact.run_id}")
    print(f"Status: {artifact.status.value}")
    print(f"Manifest: {artifact.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(make_run_cli())
