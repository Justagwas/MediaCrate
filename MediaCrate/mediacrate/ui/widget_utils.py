from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget


def set_widget_pointer_cursor(widget: QWidget) -> None:
    try:
        if widget.isEnabled() and widget.isVisible():
            widget.setCursor(Qt.PointingHandCursor)
        else:
            widget.unsetCursor()
    except RuntimeError:
        return


def rounded_pixmap(source: QPixmap, target_size: QSize, radius: int) -> QPixmap:
    if source.isNull():
        return source
    safe_size = QSize(max(1, int(target_size.width())), max(1, int(target_size.height())))
    scaled = source.scaled(
        safe_size,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    rounded = QPixmap(safe_size)
    rounded.fill(Qt.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(
        0.0,
        0.0,
        float(safe_size.width()),
        float(safe_size.height()),
        float(max(0, int(radius))),
        float(max(0, int(radius))),
    )
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled)
    painter.end()
    return rounded
