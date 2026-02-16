from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from .theme import ThemePalette


def apply_dialog_theme(
    widget: QWidget,
    theme: ThemePalette,
    *,
    apply_titlebar_theme: Callable[[QWidget], None] | None = None,
    button_setup: Callable[[QPushButton], None] | None = None,
) -> None:
    style = (
        f"QDialog, QMessageBox {{ background: {theme.panel_bg}; color: {theme.text_primary}; }}"
        f"QLabel {{ color: {theme.text_primary}; background: transparent; }}"
        f"QPushButton {{ background: {theme.panel_bg}; color: {theme.text_primary}; border: 1px solid {theme.border}; border-radius: 6px; padding: 5px 10px; font: 600 9.5pt 'Segoe UI'; min-height: 24px; }}"
        f"QPushButton:hover {{ background: {theme.accent}; color: {theme.text_primary}; }}"
        f"QPushButton:disabled {{ background: {theme.disabled_bg}; color: {theme.disabled_fg}; border-color: {theme.border}; }}"
    )
    widget.setStyleSheet(style)
    palette = widget.palette()
    palette.setColor(QPalette.Window, QColor(theme.panel_bg))
    palette.setColor(QPalette.WindowText, QColor(theme.text_primary))
    palette.setColor(QPalette.Base, QColor(theme.app_bg))
    palette.setColor(QPalette.AlternateBase, QColor(theme.panel_bg))
    palette.setColor(QPalette.Text, QColor(theme.text_primary))
    palette.setColor(QPalette.Button, QColor(theme.panel_bg))
    palette.setColor(QPalette.ButtonText, QColor(theme.text_primary))
    palette.setColor(QPalette.ToolTipBase, QColor(theme.panel_bg))
    palette.setColor(QPalette.ToolTipText, QColor(theme.text_primary))
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)
    if apply_titlebar_theme is not None:
        apply_titlebar_theme(widget)
    for button in widget.findChildren(QPushButton):
        if button_setup is not None:
            button_setup(button)
        else:
            button.setCursor(Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor)


def build_message_box(
    *,
    parent: QWidget,
    theme: ThemePalette,
    app_name: str,
    icon: QMessageBox.Icon,
    title: str,
    text: str,
    window_icon: QIcon | None = None,
    buttons: QMessageBox.StandardButtons = QMessageBox.Ok,
    default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    apply_titlebar_theme: Callable[[QWidget], None] | None = None,
    button_setup: Callable[[QPushButton], None] | None = None,
) -> QMessageBox:
    box = QMessageBox(parent)
    box.setOption(QMessageBox.DontUseNativeDialog, True)
    box.setIcon(icon)
    box.setWindowTitle(str(title or app_name))
    box.setText(str(text or ""))
    box.setStandardButtons(buttons)
    if default_button != QMessageBox.NoButton:
        box.setDefaultButton(default_button)
    if window_icon is not None and not window_icon.isNull():
        box.setWindowIcon(window_icon)
    apply_dialog_theme(
        box,
        theme,
        apply_titlebar_theme=apply_titlebar_theme,
        button_setup=button_setup,
    )
    return box


def exec_dialog(dialog: QWidget, *, on_after: Callable[[], None] | None = None) -> int:
    try:
        return int(dialog.exec())
    finally:
        if on_after is not None:
            on_after()
        else:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
