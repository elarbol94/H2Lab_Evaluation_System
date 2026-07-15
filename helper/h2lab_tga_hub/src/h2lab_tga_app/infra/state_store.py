from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load_recent_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"recent_runs": [], "last_config": {}, "layout_presets": {}}
        with self.path.open("r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("recent_runs", [])
        state.setdefault("last_config", {})
        state.setdefault("layout_presets", {})
        return state

    def save_recent_run(self, run_id: str, config: dict[str, Any]) -> None:
        state = self.load_recent_state()
        recent_runs = [run_id] + [r for r in state.get("recent_runs", []) if r != run_id]
        state["recent_runs"] = recent_runs[:20]
        state["last_config"] = config
        self._write_state(state)

    def load_layout_presets(self) -> dict[str, dict[str, Any]]:
        state = self.load_recent_state()
        return state.get("layout_presets", {})

    def save_layout_preset(self, name: str, layout: dict[str, Any]) -> None:
        if not name.strip():
            raise ValueError("Preset name must not be empty.")
        state = self.load_recent_state()
        presets = state.setdefault("layout_presets", {})
        presets[name.strip()] = layout
        self._write_state(state)

    def delete_layout_preset(self, name: str) -> None:
        state = self.load_recent_state()
        presets = state.setdefault("layout_presets", {})
        presets.pop(name, None)
        self._write_state(state)

    def _write_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
