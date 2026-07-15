from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from h2lab_tga_app.infra.prep_config_store import PrepConfigStore


class PrepConfigService:
    def __init__(self, config_path: Path, store: PrepConfigStore) -> None:
        self.config_path = config_path
        self.store = store
        self._defaults = self._load_defaults(config_path)
        self.store.set_default_config_path(config_path)

    @staticmethod
    def _load_defaults(config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}
        with config_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"TGA config must be a JSON object: {config_path}")
        return raw

    def default_config(self) -> dict[str, Any]:
        return copy.deepcopy(self._defaults)

    def get_effective_config(self, file_path: str | Path) -> dict[str, Any]:
        override = self.store.get_override(file_path)
        return self._merge_configs(self._defaults, override)

    def save_override(self, file_path: str | Path, override: dict[str, Any]) -> None:
        self.validate_config_dict(override)
        if not override:
            self.store.delete_override(file_path)
            return
        self.store.save_override(file_path, override)

    def save_effective_config(self, file_path: str | Path, effective: dict[str, Any]) -> None:
        self.validate_config_dict(effective)
        override = self._diff_config(self._defaults, effective)
        if not override:
            self.store.delete_override(file_path)
            return
        self.store.save_override(file_path, override)

    def reset_override(self, file_path: str | Path) -> None:
        self.store.delete_override(file_path)

    @staticmethod
    def validate_config_dict(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("Preparation config must be a JSON object.")
        allowed = {
            "process_file",
            "pre_filter",
            "post_filter",
            "cut_reactive",
            "cut_tail",
            "gas_columns",
            "temperature_correction",
        }
        unknown = sorted(set(payload.keys()) - allowed)
        if unknown:
            raise ValueError(f"Unknown preparation config fields: {', '.join(unknown)}")

    @staticmethod
    def _merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(base)
        for key, value in override.items():
            if key in {"pre_filter", "post_filter"}:
                merged[key] = copy.deepcopy(value)
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = PrepConfigService._merge_configs(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    @staticmethod
    def _diff_config(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        diff: dict[str, Any] = {}
        for key, value in candidate.items():
            if key not in base:
                diff[key] = copy.deepcopy(value)
                continue
            base_value = base[key]
            if key in {"pre_filter", "post_filter"}:
                if value != base_value:
                    diff[key] = copy.deepcopy(value)
                continue
            if isinstance(value, dict) and isinstance(base_value, dict):
                nested = PrepConfigService._diff_config(base_value, value)
                if nested:
                    diff[key] = nested
                continue
            if value != base_value:
                diff[key] = copy.deepcopy(value)
        return diff
