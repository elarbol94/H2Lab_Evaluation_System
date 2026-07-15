from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd
from matplotlib.figure import Figure

from h2lab_tga_app.domain.models import ExperimentRef, RunConfig


class RawDataParser(Protocol):
    def parse(self, path: Path) -> pd.DataFrame: ...


class ExperimentProcessor(Protocol):
    def process(self, exp: ExperimentRef, cfg: RunConfig) -> pd.DataFrame: ...


class Visualizer(Protocol):
    def build_figures(self, processed: dict[str, pd.DataFrame]) -> dict[str, Figure]: ...
