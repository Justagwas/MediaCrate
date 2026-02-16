from __future__ import annotations

from .base_worker import BaseWorker
from ..core.update_service import UpdateService


class UpdateWorker(BaseWorker):
    def __init__(self, service: UpdateService, current_version: str) -> None:
        super().__init__()
        self._service = service
        self._current_version = str(current_version or "")

    def run(self) -> None:
        def execute():
            self.statusChanged.emit("update", "checking")
            return self._service.check_for_updates(self._current_version, stop_event=self._stop_event)

        def on_result(result) -> None:
            self.statusChanged.emit("update", "done")
            self.finishedSummary.emit(result)

        def on_interrupted(_exc: InterruptedError) -> None:
            self.statusChanged.emit("update", "stopped")
            self.finishedSummary.emit(None)

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
