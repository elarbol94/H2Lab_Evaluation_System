from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from h2lab_tga_app.domain.models import ProjectRef


class ProjectSelectionDialog(QDialog):
    def __init__(
        self,
        projects: list[ProjectRef],
        preselected_rel_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select H2Lab Project")
        self._projects = projects

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Project"))
        self.combo = QComboBox()
        row.addWidget(self.combo, 1)
        layout.addLayout(row)

        for project in projects:
            status = "TGA ready" if project.has_tga_config else "catalog only"
            self.combo.addItem(f"{project.name} ({status})", project.rel_path)

        if preselected_rel_path:
            for idx in range(self.combo.count()):
                if self.combo.itemData(idx) == preselected_rel_path:
                    self.combo.setCurrentIndex(idx)
                    break

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_project_rel_path(self) -> str:
        value = self.combo.currentData()
        return str(value) if value is not None else ""
