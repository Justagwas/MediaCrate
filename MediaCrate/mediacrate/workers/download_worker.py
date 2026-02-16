from __future__ import annotations

from .base_worker import BaseWorker
from ..core.download_service import DownloadService
from ..core.models import DownloadJob, DownloadSummary


class DownloadWorker(BaseWorker):
    def __init__(
        self,
        service: DownloadService,
        jobs: list[DownloadJob],
        concurrency: int,
        *,
        retry_count: int = 0,
        retry_profile: str = "basic",
        skip_existing_files: bool = True,
        filename_template: str = "",
        conflict_policy: str = "skip",
        speed_limit_kbps: int = 0,
    ) -> None:
        super().__init__()
        self._service = service
        self._jobs = jobs
        self._concurrency = max(1, int(concurrency))
        self._retry_count = max(0, int(retry_count))
        self._retry_profile = str(retry_profile or "basic").strip().lower()
        self._skip_existing_files = bool(skip_existing_files)
        self._filename_template = str(filename_template or "").strip()
        self._conflict_policy = str(conflict_policy or "skip").strip().lower()
        self._speed_limit_kbps = max(0, int(speed_limit_kbps))

    def run(self) -> None:
        def execute() -> DownloadSummary:
            return self._service.run_batch(
                self._jobs,
                self._concurrency,
                self._stop_event,
                progress_cb=self._on_progress,
                status_cb=self._on_status,
                log_cb=self._on_log,
                retry_count=self._retry_count,
                retry_profile=self._retry_profile,
                skip_existing_files=self._skip_existing_files,
                filename_template=self._filename_template,
                conflict_policy=self._conflict_policy,
                speed_limit_kbps=self._speed_limit_kbps,
            )

        def on_result(summary: DownloadSummary) -> None:
            self.finishedSummary.emit(summary)

        def on_error(exc: Exception) -> None:
            self.errorRaised.emit("global", str(exc))
            self.finishedSummary.emit(
                DownloadSummary(
                    total=len(self._jobs),
                    completed=0,
                    failed=len(self._jobs),
                    skipped=0,
                    cancelled=0,
                    retried=0,
                    results=[],
                )
            )

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )

    def pause_job(self, job_id: str) -> None:
        self._service.pause_job(str(job_id or "").strip())

    def resume_job(self, job_id: str) -> None:
        self._service.resume_job(str(job_id or "").strip())

    def stop_job(self, job_id: str) -> None:
        self._service.stop_job(str(job_id or "").strip())

    def enqueue_job(self, job: DownloadJob) -> bool:
        try:
            return bool(self._service.enqueue_batch_job(job))
        except Exception:
            return False

    def _on_status(self, job_id: str, state: str) -> None:
        self.statusChanged.emit(job_id, str(state or ""))

    def _on_log(self, message: str) -> None:
        self.logChanged.emit(str(message or ""))

    def _on_progress(self, job_id: str, percent: float, message: str) -> None:
        self.progressChanged.emit(str(job_id or ""), float(percent), str(message or ""))
