"""
MediaCrate - The universal multimedia downloader

Copyright 2026 Justagwas

This program is licensed under the GNU General Public License v3.0
See the LICENSE file in the project root for the full license text.

Official Project Page:
https://justagwas.com/projects/mediacrate

Official Source Code:
https://github.com/justagwas/mediacrate
https://sourceforge.net/projects/mediacrate

SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import os
import sys
import ctypes

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from mediacrate.core.config import APP_NAME, APP_VERSION

MUTEX_NAME = "MediaCrateMutex"


class SingleInstanceGuard:
    def __init__(self, mutex_name: str) -> None:
        self._mutex_name = str(mutex_name or "").strip() or "MediaCrateMutex"
        self._handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, 0, self._mutex_name)
            if not handle:
                return False
            error_already_exists = 183
            if kernel32.GetLastError() == error_already_exists:
                kernel32.CloseHandle(handle)
                return False
            self._handle = handle
            return True
        except Exception:
            return True

    def release(self) -> None:
        if os.name != "nt":
            return
        if self._handle is None:
            return
        try:
            ctypes.windll.kernel32.CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None


def _build_loading_splash() -> QSplashScreen:
    screen = QGuiApplication.primaryScreen()
    dpi_scale = 1.0
    dpr = 1.0
    if screen is not None:
        try:
            logical_dpi = float(screen.logicalDotsPerInch())
            if logical_dpi > 0:
                dpi_scale = max(1.0, min(3.0, logical_dpi / 96.0))
        except Exception:
            dpi_scale = 1.0
        try:
            dpr = max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            dpr = 1.0

    width = int(round(460 * dpi_scale))
    height = int(round(180 * dpi_scale))
    px_width = max(1, int(round(width * dpr)))
    px_height = max(1, int(round(height * dpr)))

    pixmap = QPixmap(px_width, px_height)
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(QColor("#0A0A0B"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#141416"))
    border_px = max(1, int(round(1 * dpi_scale)))
    painter.drawRect(0, 0, width, height)
    painter.setPen(QColor("#D20F39"))
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(
        border_px,
        border_px,
        width - (2 * border_px),
        height - (2 * border_px),
    )
    title_font = QFont("Segoe UI")
    title_font.setBold(True)
    title_font.setPointSizeF(max(11.0, 16.0 * dpi_scale))
    subtitle_font = QFont("Segoe UI")
    subtitle_font.setWeight(QFont.DemiBold)
    subtitle_font.setPointSizeF(max(8.0, 10.0 * dpi_scale))
    painter.setFont(title_font)
    painter.setPen(QColor("#F4F4F5"))
    x_margin = max(14, int(round(24 * dpi_scale)))
    title_y = max(40, int(round(78 * dpi_scale)))
    subtitle_y = max(58, int(round(108 * dpi_scale)))
    painter.drawText(x_margin, title_y, f"{APP_NAME} is loading")
    painter.setFont(subtitle_font)
    painter.setPen(QColor("#B7B7BC"))
    painter.drawText(x_margin, subtitle_y, "Initializing components...")
    painter.end()
    splash = QSplashScreen(pixmap, Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
    message_font = QFont("Segoe UI")
    message_font.setWeight(QFont.DemiBold)
    message_font.setPointSizeF(max(7.0, 9.0 * dpi_scale))
    splash.setFont(message_font)
    return splash


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_NAME)
    splash = _build_loading_splash()
    splash.show()
    app.processEvents()
    instance_guard = SingleInstanceGuard(MUTEX_NAME)
    if not instance_guard.acquire():
        splash.close()
        QMessageBox.information(
            None,
            APP_NAME,
            f"{APP_NAME} is already running.",
        )
        return 0

    try:
        splash.showMessage(
            f"{APP_NAME} is loading...",
            Qt.AlignBottom | Qt.AlignHCenter,
            QColor("#B7B7BC"),
        )
        app.processEvents()

        from mediacrate.app_controller import AppController

        splash.showMessage(
            "Loading main window...",
            Qt.AlignBottom | Qt.AlignHCenter,
            QColor("#B7B7BC"),
        )
        app.processEvents()
        try:
            controller = AppController(app)
        except RuntimeError as exc:
            splash.close()
            QMessageBox.critical(
                None,
                APP_NAME,
                str(exc),
            )
            return 1
        controller.run()
        splash.finish(controller.window)
        return app.exec()
    finally:
        splash.close()
        instance_guard.release()


if __name__ == "__main__":
    raise SystemExit(main())
