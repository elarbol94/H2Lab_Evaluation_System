from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExperimentRef:
    id: str
    file_path: Path
    material: str | None = None
    lime_pct: float | None = None


@dataclass(frozen=True)
class ProjectRef:
    name: str
    rel_path: str
    abs_path: Path
    has_tga_config: bool


@dataclass(frozen=True)
class RunConfig:
    reference_temp_c: float = 950.0
    compute_theory: bool = True
    save_plots: bool = True


@dataclass
class RunArtifact:
    run_id: str
    created_at: datetime
    manifest_path: Path
    outputs: dict[str, Any] = field(default_factory=dict)
    status: RunStatus = RunStatus.PENDING


@dataclass
class SubplotSpec:
    id: str
    x_col: str
    y_col: str
    title: str
    x_label: str
    y_label: str
    style: dict[str, Any] = field(default_factory=dict)


@dataclass
class VisualizationLayout:
    subplots: list[SubplotSpec] = field(default_factory=list)
    preset_name: str | None = None
