from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from h2lab_tga_app.config.settings import ENV_KEYS, Settings
from h2lab_tga_app.infra.repo_root import find_repo_root


LOCAL_REPO_ENV_KEY = "H2LAB_LOCAL_REPO_ROOT"
PROJECT_PATTERN = re.compile(r"^(?:H2Lab_)?[A-Za-z][A-Za-z0-9]*_\d{2}_\d+")


@dataclass
class PathResolver:
    settings: Settings

    def project_root(self) -> Path:
        if self.settings.selected_data_root is not None:
            return self.settings.selected_data_root.resolve()
        return self.settings.sharepoint_root / self.settings.project_rel_path

    def raw_data_root(self) -> Path:
        if self.settings.selected_data_root is not None:
            return self.project_root()

        primary = self.project_root() / self.settings.raw_data_rel_path
        if primary.exists():
            return primary

        tga_fallback = self.project_root() / "TGA"
        if tga_fallback.exists():
            return tga_fallback

        return primary

    def metadata_root(self) -> Path:
        return self.project_root() / self.settings.metadata_dir_name

    def outputs_root(self) -> Path:
        return self.metadata_root() / "outputs"

    def state_file(self) -> Path:
        return self.metadata_root() / "state" / "state.json"

    def prep_overrides_file(self) -> Path:
        return self.metadata_root() / "state" / "prep_overrides.json"

    def tasks_db_path(self) -> Path:
        return self.metadata_root() / "tasks" / "tasks.sqlite"

    def tga_config_path(self) -> Path:
        candidates = self.tga_config_candidates()
        for candidate in candidates:
            if candidate.exists():
                return candidate
        preferred_missing = self._preferred_missing_config_candidate()
        if preferred_missing is not None:
            return preferred_missing
        return self.project_root() / "TGA" / "config.json"

    def tga_config_candidates(self) -> list[Path]:
        root = self.project_root()
        candidates: list[Path] = []
        seen: set[Path] = set()

        def add(path: Path) -> None:
            normalized = path.resolve(strict=False)
            if normalized in seen:
                return
            seen.add(normalized)
            candidates.append(path)

        for candidate_root in [root, *root.parents]:
            add(candidate_root / "TGA" / "config.json")
            add(candidate_root / "config.json")

        project_names = self._infer_project_names(root)
        for lookup_root in self.config_lookup_roots():
            for project_name in project_names:
                for variant in self._project_name_variants(project_name):
                    add(lookup_root / variant / "TGA" / "config.json")

        add(root / "TGA" / "config.json")
        return candidates

    def config_lookup_roots(self) -> list[Path]:
        roots: list[Path] = []
        seen: set[Path] = set()

        def add(root: Path) -> None:
            if not root.exists() or not root.is_dir() or root in seen:
                return
            seen.add(root)
            roots.append(root)

        explicit_repo_root = os.getenv(LOCAL_REPO_ENV_KEY)
        if explicit_repo_root:
            add(Path(explicit_repo_root).expanduser().resolve())

        auto_repo_root = self._local_repo_root()
        if auto_repo_root is not None:
            add(auto_repo_root)

        for key in ENV_KEYS:
            value = os.getenv(key)
            if not value:
                continue
            add(Path(value).expanduser().resolve())

        for root in self._common_local_roots():
            add(root)
        return roots

    def tga_config_lookup_roots(self) -> list[Path]:
        return self.config_lookup_roots()

    def inferred_project_key(self) -> str:
        names = self._infer_project_names(self.project_root())
        if not names:
            return ""
        return self._canonical_project_key(names[0])

    @staticmethod
    def _common_local_roots() -> list[Path]:
        root = (Path.home() / "PycharmProjects" / "H2Lab").resolve()
        if root.exists() and root.is_dir():
            return [root]
        return []

    @staticmethod
    def _local_repo_root() -> Path | None:
        try:
            return find_repo_root(Path(__file__).resolve())
        except RuntimeError:
            return None

    def _infer_project_names(self, root: Path) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()

        def add(value: str | None) -> None:
            if not value:
                return
            key = self._canonical_project_key(value)
            if key in seen:
                return
            seen.add(key)
            names.append(value)

        from_settings = self._project_segment(self.settings.project_rel_path)
        add(from_settings)
        for candidate_root in [root, *root.parents]:
            if self._looks_like_project_name(candidate_root.name):
                add(candidate_root.name)
        return names

    @staticmethod
    def _project_segment(raw: str) -> str | None:
        for segment in raw.replace("\\", "/").split("/"):
            if PathResolver._looks_like_project_name(segment):
                return segment
        return None

    @staticmethod
    def _looks_like_project_name(name: str) -> bool:
        return bool(PROJECT_PATTERN.match(name))

    @staticmethod
    def _canonical_project_key(name: str) -> str:
        lowered = name.lower()
        if lowered.startswith("h2lab_"):
            lowered = lowered[len("h2lab_") :]
        return lowered

    @staticmethod
    def _project_name_variants(name: str) -> list[str]:
        variants = [name]
        if name.startswith("H2Lab_"):
            stripped = name[len("H2Lab_") :]
            if stripped:
                variants.append(stripped)
        else:
            variants.append(f"H2Lab_{name}")
        return variants

    def _preferred_missing_config_candidate(self) -> Path | None:
        project_names = self._infer_project_names(self.project_root())
        if not project_names:
            return None
        for lookup_root in self.config_lookup_roots():
            for project_name in project_names:
                for variant in self._project_name_variants(project_name):
                    return lookup_root / variant / "TGA" / "config.json"
        return None

    def run_dir(self, run_id: str) -> Path:
        return self.outputs_root() / "runs" / run_id

    def ensure_layout(self) -> None:
        (self.outputs_root() / "runs").mkdir(parents=True, exist_ok=True)
        self.state_file().parent.mkdir(parents=True, exist_ok=True)
