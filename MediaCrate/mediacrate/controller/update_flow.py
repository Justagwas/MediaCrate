from __future__ import annotations

import sys
import webbrowser
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QWidget,
    QVBoxLayout,
)

from ..core.config import DEFAULT_DOWNLOAD_URL
from ..core.models import UpdateCheckResult
from ..core.update_service import UpdateService
from ..ui.dialogs import apply_dialog_theme
from ..workers.update_install_worker import UpdateInstallWorker
from ..workers.update_worker import UpdateWorker


def _apply_update_dialog_chrome(dialog: QDialog, parent: QWidget | None) -> None:
    apply_titlebar = getattr(parent, "apply_windows_titlebar_theme", None) if parent is not None else None
    theme = getattr(parent, "theme", None) if parent is not None else None
    if theme is not None:
        apply_dialog_theme(
            dialog,
            theme,
            apply_titlebar_theme=apply_titlebar if callable(apply_titlebar) else None,
            button_setup=lambda button: button.setCursor(
                Qt.PointingHandCursor if button.isEnabled() else Qt.ArrowCursor
            ),
        )
    elif callable(apply_titlebar):
        try:
            apply_titlebar(dialog)
        except Exception:
            pass
    for checkbox in dialog.findChildren(QCheckBox):
        checkbox.setCursor(Qt.PointingHandCursor if checkbox.isEnabled() else Qt.ArrowCursor)
    for view in dialog.findChildren(QAbstractItemView):
        view.setCursor(Qt.PointingHandCursor if view.isEnabled() else Qt.ArrowCursor)
    dialog.setCursor(Qt.ArrowCursor)


def _exec_modal(dialog: QDialog, parent: QWidget | None) -> int:
    try:
        return int(dialog.exec())
    finally:
        refreshed = False
        refresh_cursor = getattr(parent, "refresh_cursor_state", None) if parent is not None else None
        if callable(refresh_cursor):
            try:
                refresh_cursor()
                refreshed = True
            except Exception:
                pass
        if not refreshed:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()


class _UpdateInstallProgressDialog(QDialog):
    canceled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Updating MediaCrate")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        self._title = QLabel("Installing update...", self)
        layout.addWidget(self._title)
        self._status = QLabel("Preparing update...", self)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._progress.setStyleSheet(
            "QProgressBar { border: 1px solid #2c2c2c; border-radius: 6px; text-align: center; } "
            "QProgressBar::chunk { background-color: #19b34b; border-radius: 5px; }"
        )
        layout.addWidget(self._progress)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self.canceled.emit)
        buttons.addWidget(self._cancel_btn)
        layout.addLayout(buttons)

    def set_progress(self, percent: int, message: str) -> None:
        clamped = max(0, min(100, int(percent)))
        self._progress.setValue(clamped)
        status = str(message or "").strip() or "Updating..."
        self._status.setText(status)

    def set_cancel_enabled(self, enabled: bool) -> None:
        self._cancel_btn.setEnabled(bool(enabled))


class UpdateFlowCoordinator(QObject):
    def __init__(
        self,
        *,
        owner: QObject,
        service: UpdateService,
        current_version: str,
        set_update_busy: Callable[[bool], None],
        show_info: Callable[[str, str], None],
        show_warning: Callable[[str, str], None],
        ask_yes_no: Callable[..., int],
        request_restart: Callable[[], None],
        open_url: Callable[[str], object] | None = None,
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self._service = service
        self._current_version = str(current_version or "")
        self._set_update_busy = set_update_busy
        self._show_info = show_info
        self._show_warning = show_warning
        self._ask_yes_no = ask_yes_no
        self._request_restart = request_restart
        self._open_url = open_url or webbrowser.open
        self._thread: QThread | None = None
        self._worker: UpdateWorker | None = None
        self._install_thread: QThread | None = None
        self._install_worker: UpdateInstallWorker | None = None
        self._active_install_result: UpdateCheckResult | None = None
        self._pending_install_result: UpdateCheckResult | None = None
        self._install_progress_dialog: _UpdateInstallProgressDialog | None = None
        # True once installer handoff succeeds and the app should close.
        self._close_after_install_handoff = False
        self._manual_request = False

    def _dialog_parent(self) -> QWidget | None:
        owner_window = getattr(self._owner, "window", None)
        if isinstance(owner_window, QWidget):
            return owner_window
        active = QApplication.activeWindow()
        if isinstance(active, QWidget):
            return active
        return None

    def _open_url_checked(self, url: str) -> None:
        result = self._open_url(url)
        if isinstance(result, bool) and (not result):
            raise RuntimeError("No browser handler accepted the update URL.")

    def running_thread(self) -> QThread | None:
        if self._install_thread and self._install_thread.isRunning():
            return self._install_thread
        return self._thread

    def is_running(self) -> bool:
        check_running = bool(self._thread and self._thread.isRunning())
        install_running = bool(self._install_thread and self._install_thread.isRunning())
        return bool(check_running or install_running)

    def stop(self) -> None:
        if self._worker:
            self._worker.stop()
        if self._install_worker:
            self._install_worker.stop()

    def start_check(self, *, manual: bool) -> bool:
        if self.is_running():
            if manual:
                self._show_info("Update check", "An update check is already in progress.")
            return False

        self._set_update_busy(True)
        self._manual_request = bool(manual)
        self._active_install_result = None
        self._pending_install_result = None
        self._close_after_install_handoff = False
        thread = QThread(self._owner)
        worker = UpdateWorker(self._service, self._current_version)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.errorRaised.connect(self._on_update_error, Qt.ConnectionType.QueuedConnection)
        worker.finishedSummary.connect(self._on_update_summary, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._on_update_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def _on_update_error(self, _job_id: str, error: str) -> None:
        if self._manual_request:
            self._show_warning("Update check", f"Unable to check for updates.\n{error}")

    def _on_update_summary(self, result: UpdateCheckResult | None) -> None:
        try:
            if result is None:
                return
            if result.update_available:
                self._handle_available_update(result)
                return
            if self._manual_request:
                self._show_info("Up to date", "You are already on the latest version.")
        except Exception as exc:
            if self._manual_request:
                self._show_warning("Update check", f"Unable to process update response.\n{exc}")

    def _handle_available_update(self, result: UpdateCheckResult) -> None:
        notes_lines = [f"- {line}" for line in (result.notes or []) if str(line or "").strip()]
        details_text = ""
        if notes_lines:
            details_text = "\n\nWhat's new:\n" + "\n".join(notes_lines[:8])
        can_install = bool(
            result.install_supported
            and result.setup_url
            and result.setup_sha256
            and int(result.setup_size or 0) > 0
        )
        if not getattr(sys, "frozen", False):
            can_install = False
        if result.requires_manual_update:
            can_install = False
        if result.requires_manual_update:
            minimum_supported = str(result.minimum_supported_version or "1.0.0").strip() or "1.0.0"
            details_text += (
                "\n\nYour current version is below the minimum supported "
                f"auto-update baseline ({minimum_supported})."
            )
        proceed, auto_install = self._ask_update_install_preference(
            latest_version=str(result.latest_version or ""),
            details_text=details_text,
            install_supported=can_install,
        )
        if not proceed:
            return
        if can_install and auto_install:
            self._queue_or_start_install_after_check(result)
            return
        fallback_url = str(result.download_url or "").strip() or str(DEFAULT_DOWNLOAD_URL or "").strip()
        if fallback_url:
            try:
                self._open_url_checked(fallback_url)
            except Exception as exc:
                self._show_warning("Update check", f"Unable to open update page.\n{exc}")
            return
        self._show_warning("Update check", "No trusted update URL was available.")

    def _queue_or_start_install_after_check(self, result: UpdateCheckResult) -> None:
        thread = self._thread
        if thread is not None:
            try:
                if thread.isRunning():
                    self._pending_install_result = result
                    return
            except RuntimeError:
                pass
        self._pending_install_result = None
        self._start_install(result)

    def _ask_update_install_preference(
        self,
        *,
        latest_version: str,
        details_text: str = "",
        install_supported: bool = True,
    ) -> tuple[bool, bool]:
        parent = self._dialog_parent()
        dialog = QDialog(parent)
        dialog.setModal(True)
        dialog.setWindowTitle("Update available")
        if parent is not None:
            icon = parent.windowIcon()
            if not icon.isNull():
                dialog.setWindowIcon(icon)
        _apply_update_dialog_chrome(dialog, parent)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel(f"Version {str(latest_version or 'latest')} is available.", dialog)
        title.setStyleSheet("font: 700 9pt 'Segoe UI';")
        layout.addWidget(title)

        auto_install_checkbox = QCheckBox("Install update automatically", dialog)
        auto_install_checkbox.setChecked(bool(install_supported))
        auto_install_checkbox.setEnabled(bool(install_supported))
        layout.addWidget(auto_install_checkbox)

        mode_hint = QLabel("", dialog)
        mode_hint.setWordWrap(True)
        mode_hint.setStyleSheet("font: 600 8pt 'Segoe UI';")

        def _refresh_hint() -> None:
            if auto_install_checkbox.isEnabled() and auto_install_checkbox.isChecked():
                mode_hint.setText(
                    "MediaCrate will download, verify, install the update, and relaunch automatically."
                )
            elif auto_install_checkbox.isEnabled():
                mode_hint.setText(
                    "MediaCrate will open the download page so you can install the update manually."
                )
            else:
                mode_hint.setText(
                    "Automatic install is unavailable here. MediaCrate will open the download page."
                )

        auto_install_checkbox.toggled.connect(lambda _checked=False: _refresh_hint())
        _refresh_hint()
        layout.addWidget(mode_hint)

        extra = str(details_text or "").strip()
        if extra:
            details = QLabel(extra, dialog)
            details.setWordWrap(True)
            details.setStyleSheet("font: 600 8pt 'Segoe UI';")
            layout.addWidget(details)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        later_button = QPushButton("Not now", dialog)
        continue_button = QPushButton("Update", dialog)
        continue_button.setDefault(True)
        buttons.addWidget(later_button)
        buttons.addWidget(continue_button)
        layout.addLayout(buttons)

        later_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)

        accepted = _exec_modal(dialog, parent) == QDialog.Accepted
        auto_install = bool(auto_install_checkbox.isEnabled() and auto_install_checkbox.isChecked())
        return accepted, auto_install

    def _ask_update_handoff(
        self,
        *,
        version: str,
        default_restart: bool = True,
        requires_elevation: bool = False,
    ) -> tuple[bool, bool]:
        parent = self._dialog_parent()
        dialog = QDialog(parent)
        dialog.setModal(True)
        dialog.setWindowTitle("Install update")
        if parent is not None:
            icon = parent.windowIcon()
            if not icon.isNull():
                dialog.setWindowIcon(icon)
        _apply_update_dialog_chrome(dialog, parent)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("Update is ready to install", dialog)
        title.setStyleSheet("font: 700 9pt 'Segoe UI';")
        body = QLabel(
            (
                f"MediaCrate v{str(version or 'latest')} has been downloaded.\n\n"
                "The installer will now take over.\n\n"
                "Click Update to close MediaCrate and begin installation."
            ),
            dialog,
        )
        body.setWordWrap(True)
        body.setStyleSheet("font: 600 8pt 'Segoe UI';")
        uac_hint = QLabel(
            "Windows may ask for administrator permission to continue this update.",
            dialog,
        )
        uac_hint.setWordWrap(True)
        uac_hint.setStyleSheet("font: 600 8pt 'Segoe UI';")
        uac_hint.setVisible(bool(requires_elevation))
        restart_checkbox = QCheckBox("Restart MediaCrate after update", dialog)
        restart_checkbox.setChecked(bool(default_restart))

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        abort_button = QPushButton("Abort", dialog)
        continue_button = QPushButton("Update", dialog)
        continue_button.setDefault(True)
        buttons.addWidget(abort_button)
        buttons.addWidget(continue_button)

        layout.addWidget(title)
        layout.addWidget(body)
        layout.addWidget(uac_hint)
        layout.addWidget(restart_checkbox)
        layout.addLayout(buttons)

        abort_button.clicked.connect(dialog.reject)
        continue_button.clicked.connect(dialog.accept)

        accepted = _exec_modal(dialog, parent) == QDialog.Accepted
        restart_after = bool(restart_checkbox.isChecked())
        return accepted, restart_after

    def _on_update_finished(self) -> None:
        self._worker = None
        self._thread = None
        pending_install = self._pending_install_result
        self._pending_install_result = None
        if pending_install is not None:
            self._start_install(pending_install)
            return
        self._set_busy_done()

    def _start_install(self, result: UpdateCheckResult) -> None:
        self._set_update_busy(True)
        self._active_install_result = result
        self._show_install_progress_dialog()
        thread = QThread(self._owner)
        worker = UpdateInstallWorker(self._service, result)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.errorRaised.connect(self._on_install_error, Qt.ConnectionType.QueuedConnection)
        worker.finishedSummary.connect(self._on_install_summary, Qt.ConnectionType.QueuedConnection)
        worker.handoffRequested.connect(self._on_install_handoff_requested, Qt.ConnectionType.QueuedConnection)
        worker.progressChanged.connect(self._on_install_progress, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._on_install_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._install_thread = thread
        self._install_worker = worker
        thread.start()

    def _show_install_progress_dialog(self) -> None:
        dialog = self._install_progress_dialog
        if dialog is None:
            parent = self._dialog_parent()
            dialog = _UpdateInstallProgressDialog(parent)
            dialog.set_progress(0, "Preparing update...")
            dialog.canceled.connect(self._on_install_cancel_requested)
            self._install_progress_dialog = dialog
        dialog.set_cancel_enabled(True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _close_install_progress_dialog(self) -> None:
        dialog = self._install_progress_dialog
        if dialog is None:
            return
        try:
            dialog.close()
            dialog.deleteLater()
        except Exception:
            pass
        self._install_progress_dialog = None

    def _on_install_progress(self, _job_id: str, percent: float, message: str) -> None:
        self._show_install_progress_dialog()
        dialog = self._install_progress_dialog
        if dialog is None:
            return
        dialog.set_progress(int(round(float(percent))), str(message or ""))

    def _on_install_handoff_requested(self, payload: object) -> None:
        info = payload if isinstance(payload, dict) else {}
        version = str(info.get("version") or "")
        requires_elevation = bool(info.get("requires_elevation", False))
        self._on_install_progress(
            "update",
            99.0,
            "Ready to hand off to installer. Waiting for your confirmation...",
        )
        dialog = self._install_progress_dialog
        if dialog is not None:
            dialog.set_cancel_enabled(False)
        continue_update, restart_after = self._ask_update_handoff(
            version=version,
            default_restart=True,
            requires_elevation=requires_elevation,
        )
        worker = self._install_worker
        if worker is None:
            return
        worker.set_handoff_decision(
            continue_update=bool(continue_update),
            restart_after_update=bool(restart_after),
        )

    def _on_install_cancel_requested(self) -> None:
        dialog = self._install_progress_dialog
        if dialog is not None:
            dialog.set_progress(0, "Canceling update...")
        worker = self._install_worker
        if worker is None:
            return
        worker.stop()

    def _on_install_error(self, _job_id: str, error: str) -> None:
        self._close_install_progress_dialog()
        self._show_warning("Update install", f"Unable to install update.\n{error}")
        fallback_url = str(DEFAULT_DOWNLOAD_URL or "").strip()
        if self._active_install_result is not None:
            fallback_url = str(self._active_install_result.download_url or "").strip() or fallback_url
        if not fallback_url:
            return
        answer = self._ask_yes_no(
            "Update install",
            "Would you like to open the download page instead?",
            default_button=QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self._open_url_checked(fallback_url)
        except Exception as exc:
            self._show_warning("Update install", f"Unable to open update page.\n{exc}")

    def _on_install_summary(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        status = str(result.get("status") or "")
        if status == "canceled":
            self._close_install_progress_dialog()
            self._show_info("Update canceled", "Update was canceled before installation started.")
            return
        if status == "aborted":
            self._close_install_progress_dialog()
            self._show_info("Update canceled", "Update was aborted. No installer was launched.")
            return
        if status != "ready":
            return
        version = str(result.get("version") or "").strip() or "latest"
        if bool(result.get("restart_after_update", True)):
            message = f"Handing off to installer for v{version} (app will restart after update)."
        else:
            message = f"Handing off to installer for v{version}..."
        self._on_install_progress("update", 100.0, message)
        self._close_after_install_handoff = True

    def _on_install_finished(self) -> None:
        self._close_install_progress_dialog()
        close_after_handoff = bool(self._close_after_install_handoff)
        self._close_after_install_handoff = False
        self._active_install_result = None
        self._install_worker = None
        self._install_thread = None
        self._set_busy_done()
        if not close_after_handoff:
            return
        try:
            self._request_restart()
        except Exception as exc:
            self._show_warning("Update install", f"Unable to restart automatically.\n{exc}")

    def _set_busy_done(self) -> None:
        try:
            self._set_update_busy(False)
        except Exception:
            pass
        self._manual_request = False
