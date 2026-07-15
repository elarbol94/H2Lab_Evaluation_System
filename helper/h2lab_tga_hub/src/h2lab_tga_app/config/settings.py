from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


ENV_KEYS = ("H2LAB_SHAREPOINT_PATH", "SHAREPOINT_PATH", "H2LAB_SHAREPOINT")
PROJECT_ENV_KEYS = ("H2LAB_PROJECT_REL_PATH",)
DEFAULT_PROJECT_REL_PATH = "H2Lab_PUB_25_9 Lime in EAFD Recycling"


@dataclass(frozen=True)
class Settings:
    sharepoint_root: Path
    project_rel_path: str = DEFAULT_PROJECT_REL_PATH
    selected_data_root: Path | None = None
    raw_data_rel_path: str = "app/data/raw_data"
    outputs_rel_path: str = "app/outputs"
    state_rel_path: str = "app/outputs/state/state.json"
    metadata_dir_name: str = ".h2lab_tga"


def _load_dotenv_if_available() -> None:
    if load_dotenv is None:
        return
    here = Path(__file__).resolve()
    app_root = here.parents[3]
    candidates = [app_root / ".env", Path.cwd() / ".env"]
    for env_file in candidates:
        if env_file.exists():
            load_dotenv(env_file, override=False)


def _read_sharepoint_env() -> str | None:
    for key in ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


def _read_project_env() -> str | None:
    for key in PROJECT_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


def load_settings(project_rel_path: str | None = None) -> Settings:
    _load_dotenv_if_available()
    sharepoint = _read_sharepoint_env()
    if not sharepoint:
        raise RuntimeError(
            "Missing SharePoint root. Set H2LAB_SHAREPOINT_PATH (preferred), "
            "SHAREPOINT_PATH, or H2LAB_SHAREPOINT."
        )
    root = Path(sharepoint).expanduser().resolve()
    if not root.exists():
        raise RuntimeError(f"Configured SharePoint root does not exist: {root}")
    resolved_project = project_rel_path or _read_project_env() or DEFAULT_PROJECT_REL_PATH
    return Settings(sharepoint_root=root, project_rel_path=resolved_project)
