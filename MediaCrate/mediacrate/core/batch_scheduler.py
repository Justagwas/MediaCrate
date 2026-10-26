from collections import deque

from .models import DownloadState


def effective_batch_concurrency(requested: int, job_count: int, *, speed_limit_kbps: int = 0,
                                adaptive_enabled: bool = True) -> int:
    limit = max(1, int(requested))
    count = max(1, int(job_count))
    if adaptive_enabled and count > 1:
        if speed_limit_kbps > 0:
            limit = min(limit, 2)
        elif count >= 24:
            limit = min(limit, 6)
        elif count >= 12:
            limit = min(limit, 5)
    return min(limit, count)


class BatchScheduler:
    def __init__(self, condition, cancel_token, interrupt_state, *, concurrency: int = 1,
                 speed_limit_kbps: int = 0) -> None:
        self.condition = condition
        self.cancel_token = cancel_token
        self.interrupt_state = interrupt_state
        self.ready = deque()
        self.parked = {}
        self.in_flight = set()
        self.jobs = {}
        self.pending = 0
        self.accepting = True
        self.concurrency = concurrency
        self.speed_limit_kbps = speed_limit_kbps

    def reserve(self, job) -> bool:
        with self.condition:
            if not self.accepting or self.cancel_token.is_set() or job.job_id in self.jobs:
                return False
            self.jobs[job.job_id] = job
            self.pending += 1
            return True

    def publish(self, job) -> None:
        with self.condition:
            self.ready.append(job)
            self.condition.notify_all()

    def put(self, job) -> None:
        with self.condition:
            self.parked[job.job_id] = job
            self.condition.notify_all()

    def take(self):
        with self.condition:
            while True:
                for key, job in list(self.parked.items()):
                    if key not in self.in_flight and self.interrupt_state(key, self.cancel_token) != DownloadState.PAUSED.value:
                        self.ready.append(job)
                        del self.parked[key]
                limit = effective_batch_concurrency(
                    self.concurrency, len(self.jobs), speed_limit_kbps=self.speed_limit_kbps,
                )
                while self.ready and len(self.in_flight) < limit:
                    job = self.ready.popleft()
                    if self.interrupt_state(job.job_id, self.cancel_token) == DownloadState.PAUSED.value:
                        self.parked[job.job_id] = job
                        continue
                    self.in_flight.add(job.job_id)
                    return job
                if not self.pending:
                    self.accepting = False
                    return None
                self.condition.wait(timeout=0.1)

    def release(self, job, *, complete: bool) -> None:
        with self.condition:
            self.in_flight.discard(job.job_id)
            if complete:
                self.parked.pop(job.job_id, None)
                self.pending -= 1
                if not self.pending:
                    self.accepting = False
            self.condition.notify_all()
