from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QListWidget, QMainWindow, QTabWidget, QTableWidget, QWidget


@dataclass(frozen=True)
class UILocation:
    ui_location_path: str
    view_name: str
    tab_name: str
    widget_class: str
    widget_object_name: str
    window_title: str


def resolve_ui_location(widget: QWidget, local_pos: QPoint | None = None) -> UILocation:
    tab_name = _find_tab_name(widget)
    view_name = tab_name or "Unknown"
    window_title = ""
    main = widget.window()
    if isinstance(main, QMainWindow):
        window_title = main.windowTitle()

    path_parts: list[str] = []
    current: QWidget | None = widget
    while current is not None:
        class_name = current.metaObject().className()
        object_name = current.objectName().strip()
        suffix = _index_suffix(current, local_pos if current is widget else None)
        if object_name:
            path_parts.append(f"{class_name}({object_name}){suffix}")
        else:
            path_parts.append(f"{class_name}{suffix}")
        parent = current.parentWidget()
        current = parent
    path_parts.reverse()

    return UILocation(
        ui_location_path="/".join(path_parts),
        view_name=view_name,
        tab_name=tab_name or "Unknown",
        widget_class=widget.metaObject().className(),
        widget_object_name=widget.objectName().strip(),
        window_title=window_title,
    )


def _find_tab_name(widget: QWidget) -> str:
    current: QWidget | None = widget
    while current is not None:
        parent = current.parentWidget()
        if isinstance(parent, QTabWidget):
            idx = parent.indexOf(current)
            if idx >= 0:
                return parent.tabText(idx)
        current = parent
    return ""


def _index_suffix(widget: QWidget, local_pos: QPoint | None) -> str:
    if local_pos is None:
        return ""
    if isinstance(widget, QTableWidget):
        item = widget.itemAt(local_pos)
        if item is not None:
            return f"[row={item.row()},col={item.column()}]"
    if isinstance(widget, QListWidget):
        item = widget.itemAt(local_pos)
        if item is not None:
            return f"[row={widget.row(item)}]"
    return ""

