from __future__ import annotations

from pathlib import Path

import pandas as pd


def parse_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)
