from __future__ import annotations

from typing import Any

import pandas as pd


def extract_theory_value(result: dict[str, Any]) -> float | None:
    value = result.get("mass_loss_pct")
    if value is None or pd.isna(value):
        return None
    return float(value)
