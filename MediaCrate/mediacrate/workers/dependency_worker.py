from __future__ import annotations

from .base_worker import BaseWorker
from ..core.dependency_service import DependencyInstallCancelled, DependencyService


class DependencyWorker(BaseWorker):
    def __init__(self, service: DependencyService, dependency_name: str) -> None:
        super().__init__()
        self._service = service
        self._dependency_name = str(dependency_name or "").strip().lower()

    def run(self) -> None:
        name = self._dependency_name

        def execute() -> str:
            self.statusChanged.emit(name, "installing")
            return self._service.install_dependency(
                name,
                self._stop_event,
                progress_cb=lambda percent, message: self.progressChanged.emit(name, percent, message),
                log_cb=self.logChanged.emit,
            )

        def on_result(path: str) -> None:
            self.statusChanged.emit(name, "ready")
            self.finishedSummary.emit({"name": name, "installed": True, "path": path})

        def on_error(exc: Exception) -> None:
            if isinstance(exc, DependencyInstallCancelled):
                self.statusChanged.emit(name, "cancelled")
                self.logChanged.emit(str(exc))
                self.finishedSummary.emit({"name": name, "installed": False, "path": ""})
                return
            self.statusChanged.emit(name, "error")
            self.errorRaised.emit(name, str(exc))
            self.finishedSummary.emit({"name": name, "installed": False, "path": ""})

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )
