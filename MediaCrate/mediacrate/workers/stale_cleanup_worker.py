from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .base_worker import BaseWorker


class StaleCleanupWorker(BaseWorker):
    def __init__(self, *, download_location: str, max_age_hours: int, reason: str) -> None:
        super().__init__()
        self._download_location = str(download_location or "").strip()
        self._max_age_hours = max(0, int(max_age_hours))
        self._reason = str(reason or "").strip() or "manual"

    def run(self) -> None:
        deleted_count = [0]

        def execute() -> tuple[str, int, int] | None:
            if self.is_cancelled():
                return None
            deleted_count[0] = self._cleanup_stale_parts()
            if self.is_cancelled():
                return None
            return (self._reason, deleted_count[0], self._max_age_hours)

        def on_result(result: tuple[str, int, int] | None) -> None:
            if result is None:
                return
            self.finishedSummary.emit(result)

        def on_error(exc: Exception) -> None:
            self.errorRaised.emit("stale-cleanup", str(exc))
            self.finishedSummary.emit((self._reason, deleted_count[0], self._max_age_hours))

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )

    def _cleanup_stale_parts(self) -> int:
        if self._max_age_hours <= 0 or (not self._download_location):
            return 0
        try:
            root = Path(self._download_location).expanduser()
        except Exception:
            return 0
        if (not root.exists()) or (not root.is_dir()):
            return 0

        cutoff_ts = datetime.now(timezone.utc).timestamp() - (self._max_age_hours * 3600)
        deleted = 0
        try:
            iterator = root.rglob("*.part")
        except OSError:
            return 0

        for path in iterator:
            if self.is_cancelled():
                break
            try:
                if (not path.is_file()) or path.stat().st_mtime > cutoff_ts:
                    continue
                path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                continue
        return deleted
