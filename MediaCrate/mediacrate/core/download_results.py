from __future__ import annotations

import threading

from .models import DownloadJob, DownloadResult


class DownloadResultBuffer:
    def __init__(self, jobs: list[DownloadJob]) -> None:
        self._lock = threading.Lock()
        self._initial_order = [job.job_id for job in jobs]
        self._results: dict[str, DownloadResult] = {}

    def record(self, result: DownloadResult) -> None:
        with self._lock:
            self._results[result.job_id] = result

    def snapshot(self) -> list[DownloadResult]:
        with self._lock:
            order = dict.fromkeys([*self._initial_order, *self._results])
            return [self._results[key] for key in order if key in self._results]
