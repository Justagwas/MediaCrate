from __future__ import annotations

from .base_worker import BaseWorker
from ..core.dependency_service import dependency_status


class DependencyStatusWorker(BaseWorker):
    def run(self) -> None:
        self.run_guarded(
            execute=dependency_status,
            on_result=self.finishedSummary.emit,
            on_error=lambda exc: self.errorRaised.emit("dependency-status", str(exc)),
        )
