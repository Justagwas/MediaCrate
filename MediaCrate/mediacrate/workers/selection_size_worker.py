from __future__ import annotations

from .base_worker import BaseWorker
from ..core.download_service import DownloadService


class SelectionSizeWorker(BaseWorker):
    def __init__(
        self,
        service: DownloadService,
        url: str,
        format_choice: str,
        quality_choice: str,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._url = str(url or "").strip()
        self._format_choice = str(format_choice or "").strip()
        self._quality_choice = str(quality_choice or "").strip()
        self._timeout_seconds = timeout_seconds

    def run(self) -> None:
        def execute() -> tuple[str, str, str, int | None] | None:
            if self.is_cancelled():
                return None
            self.statusChanged.emit("selection_size", "running")
            size = self._service.resolve_selection_size_bytes(
                self._url,
                self._format_choice,
                self._quality_choice,
                timeout_seconds=self._timeout_seconds,
            )
            if self.is_cancelled():
                return None
            return (self._url, self._format_choice, self._quality_choice, size)

        def on_result(result: tuple[str, str, str, int | None] | None) -> None:
            if result is None:
                return
            self.statusChanged.emit("selection_size", "done")
            self.finishedSummary.emit(result)

        def on_error(exc: Exception) -> None:
            self.statusChanged.emit("selection_size", "error")
            self.errorRaised.emit("selection_size", str(exc))
            self.finishedSummary.emit((self._url, self._format_choice, self._quality_choice, None))

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )
