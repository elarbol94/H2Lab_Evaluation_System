from __future__ import annotations

from pathlib import Path
import tempfile
import webbrowser

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QPushButton, QTextBrowser, QVBoxLayout, QWidget


class PlotWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tmp_html: Path | None = None
        self._layout = QVBoxLayout(self)

        self._fallback_text = QTextBrowser()
        self._fallback_text.setVisible(False)
        self._open_button = QPushButton("Open Plot in Browser")
        self._open_button.setVisible(False)
        self._open_button.clicked.connect(self._open_in_browser)

        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

            self._web = QWebEngineView()
            self._layout.addWidget(self._web)
            self._has_web_engine = True
        except Exception:
            self._web = None
            self._has_web_engine = False

        self._layout.addWidget(self._fallback_text)
        self._layout.addWidget(self._open_button)

    def set_html(self, html: str) -> None:
        if self._has_web_engine and self._web is not None:
            self._web.setHtml(html)
            self._fallback_text.setVisible(False)
            self._open_button.setVisible(False)
            return

        fd = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        fd.write(html.encode("utf-8"))
        fd.flush()
        fd.close()
        self._tmp_html = Path(fd.name)
        self._fallback_text.setVisible(True)
        self._open_button.setVisible(True)
        self._fallback_text.setPlainText(
            "Qt WebEngine not available. Use the button below to open the Plotly figure in your browser."
        )

    def clear_plot(self) -> None:
        if self._has_web_engine and self._web is not None:
            self._web.setHtml("<html><body></body></html>")
        self._fallback_text.clear()

    def _open_in_browser(self) -> None:
        if self._tmp_html is not None:
            webbrowser.open(QUrl.fromLocalFile(str(self._tmp_html)).toString())
