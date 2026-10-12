from collections import deque

from .models import DownloadState


class BatchScheduler:
    def __init__(self, condition, cancel_token, interrupt_state) -> None:
        self.condition = condition
        self.cancel_token = cancel_token
        self.interrupt_state = interrupt_state
        self.ready = deque()
        self.parked = {}
        self.in_flight = set()
        self.jobs = {}
        self.pending = 0
        self.accepting = True

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
                while self.ready:
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
