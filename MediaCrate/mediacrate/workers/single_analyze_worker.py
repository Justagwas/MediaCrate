from __future__ import annotations

from .base_worker import BaseWorker
from ..core.download_service import DownloadService
from ..core.models import UrlAnalysisResult, DEFAULT_FORMAT_CHOICES


class SingleAnalyzeWorker(BaseWorker):
    def __init__(self, service: DownloadService, url: str, *, timeout_seconds: float | None = None) -> None:
        super().__init__()
        self._service = service
        self._url = str(url or "").strip()
        self._timeout_seconds = timeout_seconds

    def run(self) -> None:
        def execute() -> UrlAnalysisResult | None:
            if self.is_cancelled():
                return None
            self.statusChanged.emit("single", "running")
            analysis_result = self._service.analyze_url(self._url, timeout_seconds=self._timeout_seconds)
            if self.is_cancelled():
                return None
            return analysis_result

        def on_result(result: UrlAnalysisResult | None) -> None:
            if result is None:
                return
            self.finishedSummary.emit((self._url, result))

        def on_error(exc: Exception) -> None:
            if self.is_cancelled():
                return
            self.errorRaised.emit("single", str(exc))
            self.finishedSummary.emit(
                (
                    self._url,
                    UrlAnalysisResult(
                        url_raw=self._url,
                        url_normalized="",
                        is_valid=False,
                        formats=list(DEFAULT_FORMAT_CHOICES),
                        qualities=["BEST QUALITY"],
                        error=str(exc),
                    ),
                )
            )

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )
