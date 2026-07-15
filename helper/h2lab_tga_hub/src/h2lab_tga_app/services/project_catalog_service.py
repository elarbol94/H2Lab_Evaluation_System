from __future__ import annotations

import re
from pathlib import Path

from h2lab_tga_app.domain.models import ProjectRef


PROJECT_FOLDER_PATTERN = re.compile(r"^(?:H2Lab_)?[A-Za-z][A-Za-z0-9]*_\d{2}_\d+")


class ProjectCatalogService:
    def __init__(self, sharepoint_root: Path) -> None:
        self.sharepoint_root = sharepoint_root

    def list_projects(self) -> list[ProjectRef]:
        if not self.sharepoint_root.exists():
            return []

        projects: list[ProjectRef] = []
        for folder in sorted(self.sharepoint_root.iterdir(), key=lambda p: p.name.lower()):
            if not folder.is_dir():
                continue
            if not PROJECT_FOLDER_PATTERN.match(folder.name):
                continue

            raw_data_dir = folder / "app" / "data" / "raw_data"
            tga_dir = folder / "TGA"
            if not raw_data_dir.exists() and not tga_dir.exists():
                continue

            has_tga_config = (tga_dir / "config.json").exists()
            projects.append(
                ProjectRef(
                    name=folder.name,
                    rel_path=folder.name,
                    abs_path=folder,
                    has_tga_config=has_tga_config,
                )
            )
        return projects
