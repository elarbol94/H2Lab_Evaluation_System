from __future__ import annotations

from pathlib import Path

import pandas as pd


def parse_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)
