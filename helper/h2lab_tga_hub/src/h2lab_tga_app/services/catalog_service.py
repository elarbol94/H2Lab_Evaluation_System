from __future__ import annotations

from pathlib import Path

from h2lab_tga_app.data.discovery import discover_experiments
from h2lab_tga_app.domain.models import ExperimentRef


class CatalogService:
    def __init__(self, raw_data_root: Path) -> None:
        self.raw_data_root = raw_data_root

    def list_experiments(self) -> list[ExperimentRef]:
        return discover_experiments(self.raw_data_root)
