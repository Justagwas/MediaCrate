from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import DownloadState


@dataclass(slots=True)
class DownloadRuntimeState:
    progress_by_job: dict[str, float] = field(default_factory=dict)
    attempts_by_job: dict[str, int] = field(default_factory=dict)
    state_by_job: dict[str, str] = field(default_factory=dict)
    url_by_job: dict[str, str] = field(default_factory=dict)
    active_download_is_multi: bool = False
    total_jobs: int = 0
    completed_jobs: int = 0
    last_progress_hundredths: int = -1
    job_to_batch_entry_id: dict[str, str] = field(default_factory=dict)
    batch_entry_to_active_job_id: dict[str, str] = field(default_factory=dict)

    def initialize_jobs(
        self,
        job_ids: list[str],
        job_urls: dict[str, str],
        *,
        active_multi: bool,
        job_to_entry: dict[str, str] | None = None,
    ) -> None:
        self.progress_by_job = {job_id: 0.0 for job_id in job_ids}
        self.attempts_by_job = {job_id: 0 for job_id in job_ids}
        self.state_by_job = {job_id: DownloadState.QUEUED.value for job_id in job_ids}
        self.url_by_job = {job_id: str(job_urls.get(job_id, "")) for job_id in job_ids}
        self.total_jobs = len(job_ids)
        self.completed_jobs = 0
        self.active_download_is_multi = bool(active_multi)
        self.job_to_batch_entry_id = dict(job_to_entry or {})
        self.batch_entry_to_active_job_id = {
            entry_id: job_id for job_id, entry_id in self.job_to_batch_entry_id.items()
        }
        self.last_progress_hundredths = -1

    def reset(self) -> None:
        self.progress_by_job = {}
        self.attempts_by_job = {}
        self.state_by_job = {}
        self.url_by_job = {}
        self.active_download_is_multi = False
        self.total_jobs = 0
        self.completed_jobs = 0
        self.job_to_batch_entry_id = {}
        self.batch_entry_to_active_job_id = {}
        self.last_progress_hundredths = -1
