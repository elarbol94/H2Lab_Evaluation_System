from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from h2lab_tga_app.domain.tasks import TaskSeverity, TaskStatus


@dataclass(frozen=True)
class FeedbackDialogResult:
    comment_text: str
    severity: TaskSeverity
    status: TaskStatus


class FeedbackCommentDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Feedback Task")
        self.setModal(True)
        self.setProperty("_feedback_internal", True)

        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Describe the issue or improvement idea.")
        self.comment_edit.setMinimumHeight(120)

        self.severity_combo = QComboBox()
        for value in TaskSeverity:
            self.severity_combo.addItem(value.value)
        self.severity_combo.setCurrentText(TaskSeverity.MEDIUM.value)

        self.status_combo = QComboBox()
        for value in TaskStatus:
            self.status_combo.addItem(value.value)
        self.status_combo.setCurrentText(TaskStatus.OPEN.value)

        form.addRow("Comment", self.comment_edit)
        form.addRow("Severity", self.severity_combo)
        form.addRow("Status", self.status_combo)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def result_payload(self) -> FeedbackDialogResult:
        return FeedbackDialogResult(
            comment_text=self.comment_edit.toPlainText().strip(),
            severity=TaskSeverity(self.severity_combo.currentText()),
            status=TaskStatus(self.status_combo.currentText()),
        )

    def _on_accept(self) -> None:
        if not self.comment_edit.toPlainText().strip():
            QMessageBox.warning(self, "Missing Comment", "Please enter a comment.")
            return
        self.accept()

