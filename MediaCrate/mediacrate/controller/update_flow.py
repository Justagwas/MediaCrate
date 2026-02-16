from __future__ import annotations

import webbrowser
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt
from PySide6.QtWidgets import QMessageBox

from ..core.models import UpdateCheckResult
from ..core.update_service import UpdateService
from ..workers.update_worker import UpdateWorker


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
        self._open_url = open_url or webbrowser.open
        self._thread: QThread | None = None
        self._worker: UpdateWorker | None = None
        self._manual_request = False

    def running_thread(self) -> QThread | None:
        return self._thread

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.isRunning())

    def stop(self) -> None:
        if self._worker:
            self._worker.stop()

    def start_check(self, *, manual: bool) -> bool:
        if self.is_running():
            if manual:
                self._show_info("Update check", "An update check is already in progress.")
            return False

        self._set_update_busy(True)
        self._manual_request = bool(manual)
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
        question = self._ask_yes_no(
            "Update available",
            f"A new version (v{result.latest_version}) is available.\nOpen download page now?",
            default_button=QMessageBox.Yes,
        )
        if question != QMessageBox.Yes:
            return
        if not result.download_url:
            return
        try:
            self._open_url(result.download_url)
        except Exception as exc:
            if self._manual_request:
                self._show_warning("Update check", f"Unable to open update page.\n{exc}")

    def _on_update_finished(self) -> None:
        try:
            self._set_update_busy(False)
        except Exception:
            pass
        self._worker = None
        self._thread = None
        self._manual_request = False
