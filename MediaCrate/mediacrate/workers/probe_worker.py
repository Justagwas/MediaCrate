from __future__ import annotations

from .base_worker import BaseWorker
from ..core.download_service import DownloadService
from ..core.models import FormatProbeResult


class ProbeWorker(BaseWorker):
    def __init__(self, service: DownloadService, url: str, *, timeout_seconds: float | None = None) -> None:
        super().__init__()
        self._service = service
        self._url = str(url or "").strip()
        self._timeout_seconds = timeout_seconds

    def run(self) -> None:
        def execute() -> FormatProbeResult | None:
            if self.is_cancelled():
                return None
            self.statusChanged.emit("probe", "running")
            probe_result = self._service.probe_formats(self._url, timeout_seconds=self._timeout_seconds)
            if self.is_cancelled():
                return None
            return probe_result

        def on_result(result: FormatProbeResult | None) -> None:
            if result is None:
                return
            self.statusChanged.emit("probe", "done")
            self.finishedSummary.emit(result)

        def on_error(exc: Exception) -> None:
            self.statusChanged.emit("probe", "error")
            self.errorRaised.emit("probe", str(exc))
            self.finishedSummary.emit(
                FormatProbeResult(
                    title="",
                    formats=[],
                    qualities=["BEST QUALITY"],
                    error=str(exc),
                )
            )

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )
