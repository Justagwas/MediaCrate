from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .base_worker import BaseWorker


class StaleCleanupWorker(BaseWorker):
    def __init__(
        self,
        *,
        download_location: str,
        max_age_hours: int,
        reason: str,
        tracked_part_paths: list[str] | None = None,
        scan_download_root: bool = True,
    ) -> None:
        super().__init__()
        self._download_location = str(download_location or "").strip()
        self._max_age_hours = max(0, int(max_age_hours))
        self._reason = str(reason or "").strip() or "manual"
        self._tracked_part_paths = [str(item or "").strip() for item in (tracked_part_paths or []) if str(item or "").strip()]
        self._scan_download_root = bool(scan_download_root)

    def run(self) -> None:
        deleted_paths: list[str] = []
        pruned_paths: list[str] = []

        def execute() -> tuple[str, int, int, list[str]] | None:
            if self.is_cancelled():
                return None
            deleted, pruned = self._cleanup_stale_parts()
            deleted_paths.extend(deleted)
            pruned_paths.extend(pruned)
            if self.is_cancelled():
                return None
            return (self._reason, len(deleted_paths), self._max_age_hours, list(pruned_paths))

        def on_result(result: tuple[str, int, int, list[str]] | None) -> None:
            if result is None:
                return
            self.finishedSummary.emit(result)

        def on_error(exc: Exception) -> None:
            self.errorRaised.emit("stale-cleanup", str(exc))
            self.finishedSummary.emit((self._reason, len(deleted_paths), self._max_age_hours, list(pruned_paths)))

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )

    @staticmethod
    def _path_is_under_root(path: Path, root: Path) -> bool:
        try:
            return os.path.commonpath([str(root), str(path)]) == str(root)
        except (OSError, ValueError):
            return False

    def _cleanup_stale_parts(self) -> tuple[list[str], list[str]]:
        if self._max_age_hours <= 0 or (not self._download_location):
            return [], []
        try:
            root = Path(self._download_location).expanduser()
        except Exception:
            return [], []
        if (not root.exists()) or (not root.is_dir()):
            return [], []

        cutoff_ts = datetime.now(timezone.utc).timestamp() - (self._max_age_hours * 3600)
        deleted: list[str] = []
        pruned: list[str] = []
        root_resolved = root.resolve()

        tracked_paths: set[str] = set()
        for path_value in self._tracked_part_paths:
            try:
                tracked_paths.add(str(Path(path_value).expanduser().resolve()))
            except OSError:
                continue
        candidate_paths = set(tracked_paths)
        if self._scan_download_root:
            try:
                for part_path in root.rglob("*.part"):
                    candidate_paths.add(str(part_path.expanduser().resolve()))
            except OSError:
                pass

        for path_value in sorted(candidate_paths):
            if self.is_cancelled():
                break
            try:
                path = Path(path_value).expanduser().resolve()
                if not self._path_is_under_root(path, root_resolved):
                    continue
                if not path.exists():
                    if str(path) in tracked_paths:
                        pruned.append(str(path))
                    continue
                if (not path.is_file()) or path.stat().st_mtime > cutoff_ts:
                    continue
                path.unlink(missing_ok=True)
                deleted.append(str(path))
                if str(path) in tracked_paths:
                    pruned.append(str(path))
            except OSError:
                continue
        return deleted, pruned
