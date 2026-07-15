from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """Find the H2Lab repo root by locating helper/TGA.py while walking upward."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "helper" / "TGA.py").exists():
            return candidate
    raise RuntimeError(
        f"Could not resolve workspace root containing helper/TGA.py. Start: {current}"
    )
