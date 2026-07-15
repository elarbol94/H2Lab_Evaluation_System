from __future__ import annotations

import pandas as pd
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class CatalogView(QWidget):
    def __init__(self, ctx: dict, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx

        layout = QVBoxLayout(self)
        button_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        button_row.addWidget(self.refresh_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Experiment ID", "File", "Path"])
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        experiments = self.ctx["catalog_service"].list_experiments()
        df = pd.DataFrame(
            {
                "experiment_id": [e.id for e in experiments],
                "file": [e.file_path.name for e in experiments],
                "path": [str(e.file_path) for e in experiments],
            }
        )

        self.table.setRowCount(len(df))
        for row in range(len(df)):
            self.table.setItem(row, 0, QTableWidgetItem(str(df.iloc[row, 0])))
            self.table.setItem(row, 1, QTableWidgetItem(str(df.iloc[row, 1])))
            self.table.setItem(row, 2, QTableWidgetItem(str(df.iloc[row, 2])))
        self.table.resizeColumnsToContents()
