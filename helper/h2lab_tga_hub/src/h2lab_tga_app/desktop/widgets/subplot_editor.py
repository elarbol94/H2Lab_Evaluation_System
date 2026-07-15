from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from h2lab_tga_app.ui_common.layout import auto_axis_label, make_subplot_spec, normalize_layout


class SubplotEditorWidget(QWidget):
    layout_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._available_columns: list[str] = []
        self._layout_data: list[dict[str, str]] = []
        self._updating = False

        root = QHBoxLayout(self)

        left = QVBoxLayout()
        root.addLayout(left, 1)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._load_row)
        left.addWidget(QLabel("Subplots"))
        left.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.del_btn = QPushButton("Delete")
        self.up_btn = QPushButton("Up")
        self.down_btn = QPushButton("Down")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.del_btn)
        btn_row.addWidget(self.up_btn)
        btn_row.addWidget(self.down_btn)
        left.addLayout(btn_row)

        right = QFormLayout()
        root.addLayout(right, 2)

        self.x_combo = QComboBox()
        self.y_combo = QComboBox()
        self.title_edit = QLineEdit()
        self.x_label_edit = QLineEdit()
        self.y_label_edit = QLineEdit()
        self.reset_labels_btn = QPushButton("Reset Labels")

        right.addRow("X column", self.x_combo)
        right.addRow("Y column", self.y_combo)
        right.addRow("Title", self.title_edit)
        right.addRow("X label", self.x_label_edit)
        right.addRow("Y label", self.y_label_edit)
        right.addRow("", self.reset_labels_btn)

        self.add_btn.clicked.connect(self.add_subplot)
        self.del_btn.clicked.connect(self.delete_subplot)
        self.up_btn.clicked.connect(self.move_up)
        self.down_btn.clicked.connect(self.move_down)
        self.reset_labels_btn.clicked.connect(self.reset_labels)

        self.x_combo.currentTextChanged.connect(self._save_row)
        self.y_combo.currentTextChanged.connect(self._save_row)
        self.title_edit.textChanged.connect(self._save_row)
        self.x_label_edit.textChanged.connect(self._save_row)
        self.y_label_edit.textChanged.connect(self._save_row)

    def set_available_columns(self, columns: list[str]) -> None:
        self._available_columns = columns
        self.x_combo.clear()
        self.y_combo.clear()
        self.x_combo.addItems(columns)
        self.y_combo.addItems(columns)

    def set_layout_data(self, layout_data: list[dict[str, str]]) -> None:
        self._layout_data = normalize_layout(layout_data, self._available_columns)
        self._refresh_list()
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def get_layout_data(self) -> list[dict[str, str]]:
        return self._layout_data

    def add_subplot(self) -> None:
        if not self._available_columns:
            return
        x_col = "temperature_C" if "temperature_C" in self._available_columns else self._available_columns[0]
        y_col = "dm_filtered_pct" if "dm_filtered_pct" in self._available_columns else self._available_columns[0]
        self._layout_data.append(make_subplot_spec(x_col, y_col))
        self._refresh_list()
        self.list_widget.setCurrentRow(len(self._layout_data) - 1)
        self.layout_changed.emit()

    def delete_subplot(self) -> None:
        idx = self.list_widget.currentRow()
        if idx < 0 or idx >= len(self._layout_data):
            return
        self._layout_data.pop(idx)
        if not self._layout_data and self._available_columns:
            self._layout_data.append(make_subplot_spec(self._available_columns[0], self._available_columns[0]))
        self._refresh_list()
        self.list_widget.setCurrentRow(max(0, min(idx, len(self._layout_data) - 1)))
        self.layout_changed.emit()

    def move_up(self) -> None:
        idx = self.list_widget.currentRow()
        if idx <= 0:
            return
        self._layout_data[idx - 1], self._layout_data[idx] = self._layout_data[idx], self._layout_data[idx - 1]
        self._refresh_list()
        self.list_widget.setCurrentRow(idx - 1)
        self.layout_changed.emit()

    def move_down(self) -> None:
        idx = self.list_widget.currentRow()
        if idx < 0 or idx >= len(self._layout_data) - 1:
            return
        self._layout_data[idx + 1], self._layout_data[idx] = self._layout_data[idx], self._layout_data[idx + 1]
        self._refresh_list()
        self.list_widget.setCurrentRow(idx + 1)
        self.layout_changed.emit()

    def reset_labels(self) -> None:
        idx = self.list_widget.currentRow()
        if idx < 0:
            return
        spec = self._layout_data[idx]
        spec["x_label"] = auto_axis_label(spec["x_col"])
        spec["y_label"] = auto_axis_label(spec["y_col"])
        spec["title"] = f"{spec['y_col']} vs {spec['x_col']}"
        self._load_row(idx)
        self._refresh_list()
        self.layout_changed.emit()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        for i, spec in enumerate(self._layout_data, start=1):
            item = QListWidgetItem(f"{i}. {spec.get('title', 'subplot')}")
            self.list_widget.addItem(item)

    def _load_row(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._layout_data):
            return
        self._updating = True
        spec = self._layout_data[idx]

        x_idx = self.x_combo.findText(spec["x_col"])
        y_idx = self.y_combo.findText(spec["y_col"])
        if x_idx >= 0:
            self.x_combo.setCurrentIndex(x_idx)
        if y_idx >= 0:
            self.y_combo.setCurrentIndex(y_idx)

        self.title_edit.setText(spec.get("title", ""))
        self.x_label_edit.setText(spec.get("x_label", ""))
        self.y_label_edit.setText(spec.get("y_label", ""))
        self._updating = False

    def _save_row(self) -> None:
        if self._updating:
            return
        idx = self.list_widget.currentRow()
        if idx < 0 or idx >= len(self._layout_data):
            return

        spec = self._layout_data[idx]
        spec["x_col"] = self.x_combo.currentText()
        spec["y_col"] = self.y_combo.currentText()
        spec["title"] = self.title_edit.text().strip() or f"{spec['y_col']} vs {spec['x_col']}"
        spec["x_label"] = self.x_label_edit.text().strip() or auto_axis_label(spec["x_col"])
        spec["y_label"] = self.y_label_edit.text().strip() or auto_axis_label(spec["y_col"])

        self._refresh_list()
        self.list_widget.setCurrentRow(idx)
        self.layout_changed.emit()
