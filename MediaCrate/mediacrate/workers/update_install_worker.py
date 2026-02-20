from __future__ import annotations

import threading
import time

from PySide6.QtCore import Signal

from .base_worker import BaseWorker
from ..core.models import UpdateCheckResult
from ..core.update_service import UpdateService


class UpdateInstallWorker(BaseWorker):
    handoffRequested = Signal(object)

    def __init__(self, service: UpdateService, check_result: UpdateCheckResult) -> None:
        super().__init__()
        self._service = service
        self._check_result = check_result
        self._handoff_event = threading.Event()
        self._handoff_continue = False
        self._handoff_restart = True

    def stop(self) -> None:
        super().stop()
        self._handoff_event.set()

    def set_handoff_decision(self, *, continue_update: bool, restart_after_update: bool) -> None:
        self._handoff_continue = bool(continue_update)
        self._handoff_restart = bool(restart_after_update)
        self._handoff_event.set()

    def run(self) -> None:
        def execute():
            self.statusChanged.emit("update", "installing")
            self.progressChanged.emit("update", 0.0, "Preparing update...")
            prepared = None
            try:
                prepared = self._service.prepare_update(
                    self._check_result,
                    stop_event=self._stop_event,
                    progress_cb=lambda percent, message: self.progressChanged.emit(
                        "update",
                        float(max(0, min(100, int(percent)))),
                        str(message or ""),
                    ),
                )
                self.progressChanged.emit(
                    "update",
                    99.0,
                    "Ready to hand off to installer. Waiting for your confirmation...",
                )
                self._handoff_continue = False
                self._handoff_restart = True
                self._handoff_event.clear()
                self.handoffRequested.emit(
                    {
                        "version": str(getattr(prepared, "latest_version", "") or self._check_result.latest_version or ""),
                        "requires_elevation": bool(getattr(prepared, "requires_elevation", False)),
                    }
                )
                while not self._handoff_event.is_set():
                    if self._stop_event.is_set():
                        raise InterruptedError("Update operation stopped.")
                    time.sleep(0.05)
                if not self._handoff_continue:
                    self._service.discard_prepared_update(prepared)
                    return {
                        "status": "aborted",
                        "url": str(self._check_result.download_url or ""),
                    }
                self._service.launch_prepared_update(
                    prepared,
                    restart_after_update=bool(self._handoff_restart),
                )
                return {
                    "status": "ready",
                    "version": str(getattr(prepared, "latest_version", "") or self._check_result.latest_version or ""),
                    "restart_after_update": bool(self._handoff_restart),
                }
            except InterruptedError:
                if prepared is not None:
                    try:
                        self._service.discard_prepared_update(prepared)
                    except Exception:
                        pass
                raise
            except Exception:
                if prepared is not None:
                    try:
                        self._service.discard_prepared_update(prepared)
                    except Exception:
                        pass
                raise

        def on_result(payload) -> None:
            self.statusChanged.emit("update", "install-ready")
            self.finishedSummary.emit(payload)

        def on_interrupted(_exc: InterruptedError) -> None:
            self.statusChanged.emit("update", "stopped")
            self.finishedSummary.emit({"status": "canceled"})

        def on_error(exc: Exception) -> None:
            self.statusChanged.emit("update", "error")
            self.errorRaised.emit("update", str(exc))
            self.finishedSummary.emit(None)

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
            on_interrupted=on_interrupted,
        )
