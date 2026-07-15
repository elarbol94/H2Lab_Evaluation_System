from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EqualizationTarget:
    material: str
    rel_mass: float
