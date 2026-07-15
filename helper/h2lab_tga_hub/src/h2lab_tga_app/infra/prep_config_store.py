from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class PrepConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "default_config_path": "",
            "overrides": {},
        }

    @staticmethod
    def normalize_path(path: str | Path) -> str:
        return str(Path(path).expanduser().resolve())

    def load_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_state()
        with self.path.open("r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("schema_version", 1)
        state.setdefault("default_config_path", "")
        state.setdefault("overrides", {})
        return state

    def set_default_config_path(self, path: str | Path) -> None:
        state = self.load_state()
        state["default_config_path"] = self.normalize_path(path)
        self._write_state(state)

    def get_override(self, file_path: str | Path) -> dict[str, Any]:
        state = self.load_state()
        key = self.normalize_path(file_path)
        overrides = state.get("overrides", {})
        raw = overrides.get(key, {})
        if isinstance(raw, dict):
            return raw
        return {}

    def save_override(self, file_path: str | Path, override: dict[str, Any]) -> None:
        state = self.load_state()
        key = self.normalize_path(file_path)
        state.setdefault("overrides", {})
        state["overrides"][key] = override
        self._write_state(state)

    def delete_override(self, file_path: str | Path) -> None:
        state = self.load_state()
        key = self.normalize_path(file_path)
        overrides = state.setdefault("overrides", {})
        overrides.pop(key, None)
        self._write_state(state)

    def _write_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
