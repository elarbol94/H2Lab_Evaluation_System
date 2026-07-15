from __future__ import annotations

import re
from pathlib import Path

from h2lab_tga_app.domain.models import ExperimentRef


SUPPORTED_SUFFIXES = {".txt", ".csv", ".parquet"}


def _infer_experiment_id(path: Path) -> str:
    m = re.search(r"(RT\d+)", path.name, flags=re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return path.stem


def discover_experiments(root: Path) -> list[ExperimentRef]:
    if not root.exists():
        return []

    files = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and ".h2lab_tga" not in p.parts
        and not p.name.lower().endswith(".processed.parquet")
    ]
    files.sort(key=lambda p: p.name.lower())

    experiments: list[ExperimentRef] = []
    for path in files:
        exp_id = _infer_experiment_id(path)
        experiments.append(ExperimentRef(id=exp_id, file_path=path))
    return experiments
