from __future__ import annotations


import sys
import webbrowser
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Slot
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

from ..ui.update_dialog import get_update_dialog, ask_update_install_preference, ask_update_handoff, show_update_failure, show_update_notice
from ..core.config import DEFAULT_DOWNLOAD_URL
from ..core.models import UpdateCheckResult
from ..core.update_service import UpdateService
from ..ui.dialogs import apply_dialog_theme
from ..ui.widgets.controls import SquareCheckBoxStyle
from ..workers.update_install_worker import UpdateInstallWorker
from ..workers.update_worker import UpdateWorker
from .request_context import connect_request










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
        owner.window._update_install_gate = owner._update_blocked_reason
        owner.window._update_install_confirm = owner._confirm_update_install
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
        self._install_progress_dialog: QDialog | None = None
        self._close_after_install_handoff = False
        self._manual_request = False
        self._generation = 0
        self._stopped = False

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
        return self._thread is not None or self._install_thread is not None

    def stop(self) -> None:
        self._generation += 1
        self._stopped = True
        self._pending_install_result = None
        self._close_after_install_handoff = False
        if self._worker:
            self._worker.stop()
        if self._install_worker:
            self._install_worker.stop()

    def start_check(self, *, manual: bool) -> bool:
        if getattr(self._owner, "_closing", False):
            return False
        if self.is_running():
            if manual:
                self._show_info("Update check", "An update check is already in progress.")
            return False

        self._stopped = False
        self._generation += 1
        self._set_update_busy(True)
        self._manual_request = bool(manual)
        self._active_install_result = None
        self._pending_install_result = None
        self._close_after_install_handoff = False
        thread = QThread(self._owner)
        worker = UpdateWorker(self._service, self._current_version)
        generation = self._generation
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        connect_request(worker.errorRaised, self, thread, self._on_update_error, lambda: self._worker is worker and generation == self._generation and not self._stopped and not getattr(self._owner, "_closing", False))
        connect_request(worker.finishedSummary, self, thread, self._on_update_summary, lambda: self._worker is worker and generation == self._generation and not self._stopped and not getattr(self._owner, "_closing", False))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_update_finished, Qt.ConnectionType.QueuedConnection)
        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def _on_update_error(self, _job_id: str, error: str) -> None:
        if self._manual_request:
            self._on_install_error("check", "Could not check for updates.\n" + error)

    def _on_update_summary(self, result: UpdateCheckResult | None) -> None:
        try:
            if result is None:
                return
            if result.update_available:
                self._handle_available_update(result)
                return
            if self._manual_request:
                show_update_notice(self._dialog_parent(), "You are up to date", "You are using the latest version of MediaCrate.")
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
            details_text += "\n\nThis version needs a manual update. Download and run the latest installer."
        generation = self._generation
        proceed, auto_install = self._ask_update_install_preference(
            latest_version=str(result.latest_version or ""),
            details_text=details_text,
            install_supported=can_install,
        )
        if not proceed or generation != self._generation or self._stopped or getattr(self._owner, "_closing", False):
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
        if self._thread is not None:
            self._pending_install_result = result
            return
        self._pending_install_result = None
        self._start_install(result)

    def _ask_update_install_preference(self, *, latest_version: str, details_text: str = "", install_supported: bool = True) -> tuple[bool, bool]:
        return ask_update_install_preference(self._dialog_parent(), latest_version=latest_version, details_text=details_text, install_supported=install_supported)

    def _ask_update_handoff(self, *, version: str, default_restart: bool = True, requires_elevation: bool = False) -> tuple[bool, bool]:
        return ask_update_handoff(self._dialog_parent(), version=version, default_restart=default_restart, requires_elevation=requires_elevation)

    @Slot()
    def _on_update_finished(self) -> None:
        thread = self._thread
        if thread is not None:
            if not thread.wait(0):
                QTimer.singleShot(10, self._on_update_finished)
                return
            thread.deleteLater()
        self._worker = None
        self._thread = None
        pending_install = self._pending_install_result
        self._pending_install_result = None
        if pending_install is not None:
            self._start_install(pending_install)
            return
        if not getattr(self._owner, "_closing", False):
            self._set_busy_done()

    def _start_install(self, result: UpdateCheckResult) -> None:
        if self._stopped or getattr(self._owner, "_closing", False):
            return
        self._set_update_busy(True)
        self._active_install_result = result
        get_update_dialog(self._dialog_parent()).set_progress(0, "Preparing update...")
        self._show_install_progress_dialog()
        thread = QThread(self._owner)
        worker = UpdateInstallWorker(self._service, result)
        generation = self._generation
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        connect_request(worker.errorRaised, self, thread, self._on_install_error, lambda: self._install_worker is worker and generation == self._generation and not self._stopped and not getattr(self._owner, "_closing", False))
        connect_request(worker.finishedSummary, self, thread, self._on_install_summary, lambda: self._install_worker is worker and generation == self._generation and not self._stopped and not getattr(self._owner, "_closing", False))
        connect_request(worker.handoffRequested, self, thread, self._on_install_handoff_requested, lambda: self._install_worker is worker and generation == self._generation and not self._stopped and not getattr(self._owner, "_closing", False))
        connect_request(worker.progressChanged, self, thread, self._on_install_progress, lambda: self._install_worker is worker and generation == self._generation and not self._stopped and not getattr(self._owner, "_closing", False))
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_install_finished, Qt.ConnectionType.QueuedConnection)
        self._install_thread = thread
        self._install_worker = worker
        thread.start()

    def _show_install_progress_dialog(self) -> None:
        dialog = get_update_dialog(self._dialog_parent())
        if not getattr(dialog, "_mediacrate_cancel_connected", False):
            dialog.canceled.connect(self._on_install_cancel_requested)
            dialog._mediacrate_cancel_connected = True
        self._install_progress_dialog = dialog
        dialog.show()

    def _close_install_progress_dialog(self) -> None:
        dialog = self._install_progress_dialog
        self._install_progress_dialog = None
        if dialog is not None:
            dialog.hide()

    def _on_install_progress(self, _job_id: str, percent: float, message: str) -> None:
        dialog = self._install_progress_dialog
        if dialog is not None and dialog.choice == "cancel":
            return
        if dialog is None:
            return
        dialog.set_progress(int(round(float(percent))), str(message or ""))

    def _on_install_handoff_requested(self, payload: object) -> None:
        info = payload if isinstance(payload, dict) else {}
        version = str(info.get("version") or "")
        requires_elevation = bool(info.get("requires_elevation", False))
        dialog = self._install_progress_dialog
        if dialog is None or dialog.choice == "cancel":
            if self._install_worker is not None:
                self._install_worker.stop()
            return
        if dialog is not None:
            dialog.set_cancel_enabled(False)
        worker = self._install_worker
        generation = self._generation
        continue_update, restart_after = self._ask_update_handoff(
            version=version,
            default_restart=True,
            requires_elevation=requires_elevation,
        )
        if worker is None or worker is not self._install_worker or generation != self._generation or self._stopped or getattr(self._owner, "_closing", False):
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
        self.cancel_install_handoff(error)

    def cancel_install_handoff(self, error: str) -> None:
        self._close_after_install_handoff = False
        try:
            self._owner._abort_update_shutdown()
        except (OSError, RuntimeError, ValueError) as exc:
            error += f"\nUnable to clear the update handoff: {exc}"
        choice = show_update_failure(self._dialog_parent(), error, retry=not getattr(self._owner, "_closing", False))
        if choice == "manual":
            self._open_url_checked(DEFAULT_DOWNLOAD_URL)
        elif choice == "retry":
            QTimer.singleShot(0, self._retry_failed_update)

    def _on_install_summary(self, payload: object) -> None:
        result = payload if isinstance(payload, dict) else {}
        status = str(result.get("status") or "")
        if status == "canceled":
            self._owner._abort_update_shutdown()
            self._close_install_progress_dialog()
            return
        if status == "aborted":
            self._owner._abort_update_shutdown()
            self._close_install_progress_dialog()
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

    @Slot()
    def _on_install_finished(self) -> None:
        thread = self._install_thread
        if thread is not None:
            if not thread.wait(0):
                QTimer.singleShot(10, self._on_install_finished)
                return
            thread.deleteLater()
        close_after_handoff = bool(self._close_after_install_handoff)
        if not close_after_handoff and (self._install_progress_dialog is None or self._install_progress_dialog.state != "failed"):
            self._close_install_progress_dialog()
        self._close_after_install_handoff = False
        self._active_install_result = None
        self._install_worker = None
        self._install_thread = None
        if not getattr(self._owner, "_closing", False):
            self._set_busy_done()
        if not close_after_handoff or self._stopped or getattr(self._owner, "_closing", False):
            return
        try:
            self._request_restart()
        except Exception as exc:
            self.cancel_install_handoff(f"Unable to close for the update.\n{exc}")

    def _set_busy_done(self) -> None:
        try:
            self._set_update_busy(False)
        except Exception:
            pass
        self._manual_request = False

    def _retry_failed_update(self) -> None:
        if self._install_worker is not None or self._worker is not None:
            QTimer.singleShot(100, self._retry_failed_update)
        elif not self._stopped:
            self.start_check(manual=True)
