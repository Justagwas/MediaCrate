from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QTimer

from ..core.download_service import normalize_download_state
from ..core.models import BatchEntry, BatchEntryStatus, DownloadResult, DownloadState, DownloadSummary, TERMINAL_DOWNLOAD_STATES


class DownloadFlowWindow(Protocol):
    def append_log(self, text: str) -> None: ...

    def update_batch_entry(self, entry: BatchEntry) -> None: ...

    def set_download_progress_count(self, completed: int, total: int) -> None: ...

    def set_download_progress(self, percent: float) -> None: ...

    def reset_download_progress(self) -> None: ...


class DownloadFlowController(Protocol):
    window: DownloadFlowWindow
    _download_progress_by_job: dict[str, float]
    _download_attempts_by_job: dict[str, int]
    _download_state_by_job: dict[str, str]
    _download_url_by_job: dict[str, str]
    _post_processing_notice_job_ids: set[str]
    _active_download_is_multi: bool
    _download_total_jobs: int
    _download_completed_jobs: int
    _last_download_progress_hundredths: int
    _batch_entries_by_id: dict[str, BatchEntry]
    _job_to_batch_entry_id: dict[str, str]
    _batch_entry_to_active_job_id: dict[str, str]
    _download_ui_refresh_timer: QTimer
    _batch_stats_dirty: bool
    _overall_progress_dirty: bool
    _pause_resume_dirty: bool

    def _format_classified_error(self, message: str) -> str: ...

    def _classify_download_error(self, message: str) -> tuple[str, bool]: ...

    def _failure_hint(self, category: str) -> str: ...

    def _is_terminal_batch_state(self, state: str) -> bool: ...

    def _mark_batch_queue_dirty(self) -> None: ...

    def _update_batch_stats_header(self) -> None: ...

    def _refresh_overall_download_progress(self) -> None: ...

    def _refresh_single_pause_resume_ui(self) -> None: ...

    def _apply_download_summary_error_result(
        self,
        *,
        result: DownloadResult,
        entry_id: str,
        failed_categories: dict[str, int],
        retryable_failed_entry_ids: list[str],
    ) -> bool: ...

    def _record_download_history(
        self,
        state: str,
        *,
        url: str,
        job_id: str,
        output_path: str,
        details: str,
    ) -> None: ...


class DownloadFlow:
    @staticmethod
    def _is_completed_state(state: str) -> bool:
        normalized = normalize_download_state(state)
        return normalized in {DownloadState.DONE.value, DownloadState.SKIPPED.value}

    @staticmethod
    def initialize(controller: DownloadFlowController) -> None:
        if hasattr(controller, "_download_ui_refresh_timer"):
            return
        timer = QTimer(controller)
        timer.setSingleShot(True)
        timer.setInterval(24)
        timer.timeout.connect(lambda: DownloadFlow.flush_deferred_ui_refresh(controller))
        controller._download_ui_refresh_timer = timer
        controller._batch_stats_dirty = False
        controller._overall_progress_dirty = False
        controller._pause_resume_dirty = False
        controller._download_completed_jobs = 0

    @staticmethod
    def mark_ui_refresh_dirty(
        controller: DownloadFlowController,
        *,
        batch_stats: bool = False,
        overall_progress: bool = False,
        pause_resume: bool = False,
    ) -> None:
        timer = getattr(controller, "_download_ui_refresh_timer", None)
        if timer is None:
            if batch_stats and hasattr(controller, "_update_batch_stats_header"):
                controller._update_batch_stats_header()
            if overall_progress and hasattr(controller, "_refresh_overall_download_progress"):
                controller._refresh_overall_download_progress()
            if pause_resume and hasattr(controller, "_refresh_single_pause_resume_ui"):
                controller._refresh_single_pause_resume_ui()
            return
        controller._batch_stats_dirty = bool(getattr(controller, "_batch_stats_dirty", False) or batch_stats)
        controller._overall_progress_dirty = bool(
            getattr(controller, "_overall_progress_dirty", False) or overall_progress
        )
        controller._pause_resume_dirty = bool(getattr(controller, "_pause_resume_dirty", False) or pause_resume)
        timer.start()

    @staticmethod
    def flush_deferred_ui_refresh(controller: DownloadFlowController) -> None:
        batch_stats_dirty = bool(getattr(controller, "_batch_stats_dirty", False))
        overall_progress_dirty = bool(getattr(controller, "_overall_progress_dirty", False))
        pause_resume_dirty = bool(getattr(controller, "_pause_resume_dirty", False))
        controller._batch_stats_dirty = False
        controller._overall_progress_dirty = False
        controller._pause_resume_dirty = False
        if batch_stats_dirty:
            controller._update_batch_stats_header()
        if overall_progress_dirty:
            controller._refresh_overall_download_progress()
        if pause_resume_dirty:
            controller._refresh_single_pause_resume_ui()

    @staticmethod
    def on_worker_error(controller: DownloadFlowController, job_id: str, message: str) -> None:
        classified = controller._format_classified_error(message)
        category, _retryable = controller._classify_download_error(message)
        hint = controller._failure_hint(category)
        controller.window.append_log(f"[{job_id}] {classified}")
        controller.window.append_log(f"[{job_id}] Hint: {hint}")
        mapped_entry_id = controller._job_to_batch_entry_id.get(str(job_id or "").strip())
        if not mapped_entry_id:
            return
        entry = controller._batch_entries_by_id.get(mapped_entry_id)
        if entry is None:
            return
        entry.error = f"{classified} | {hint}"
        if not controller._is_terminal_batch_state(entry.status):
            entry.status = BatchEntryStatus.FAILED.value
        controller.window.update_batch_entry(entry)
        controller._mark_batch_queue_dirty()

    @staticmethod
    def on_download_log(controller: DownloadFlowController, message: str) -> None:
        value = str(message or "").strip()
        if not value:
            return
        lowered = value.lower()
        if "has already been downloaded" in lowered:
            controller.window.append_log("Already downloaded. File exists, skipping.")
            return
        if "error:" in lowered or "warning:" in lowered:
            category, _retryable = controller._classify_download_error(value)
            if category == "unknown":
                controller.window.append_log(value)
            else:
                controller.window.append_log(f"[{category.upper()}] {value}")

    @staticmethod
    def on_download_progress(controller: DownloadFlowController, job_id: str, percent: float, message: str) -> None:
        identifier = str(job_id or "").strip()
        if not identifier or identifier not in controller._download_progress_by_job:
            return
        clamped = max(0.0, min(100.0, float(percent)))
        previous = float(controller._download_progress_by_job.get(identifier, 0.0))
        if clamped < previous:
            clamped = previous
        lowered_message = str(message or "").strip().lower()
        if (
            (("post-processing" in lowered_message) or ("post processing" in lowered_message) or (clamped >= 99.0))
            and identifier not in controller._post_processing_notice_job_ids
        ):
            controller._post_processing_notice_job_ids.add(identifier)
            controller.window.append_log("Post-processing...")
        if abs(clamped - previous) < 0.005:
            return
        controller._download_progress_by_job[identifier] = clamped
        mapped_entry_id = controller._job_to_batch_entry_id.get(identifier, identifier)
        entry = controller._batch_entries_by_id.get(mapped_entry_id)
        if entry is not None:
            entry.progress_percent = clamped
            if entry.status == BatchEntryStatus.DOWNLOAD_QUEUED.value:
                entry.status = BatchEntryStatus.DOWNLOADING.value
            controller.window.update_batch_entry(entry)
        DownloadFlow.mark_ui_refresh_dirty(controller, overall_progress=True)

    @staticmethod
    def should_skip_download_status_update(
        controller: DownloadFlowController,
        identifier: str,
        normalized: str,
    ) -> bool:
        if identifier and (identifier not in controller._download_state_by_job):
            return True
        previous_state = normalize_download_state(controller._download_state_by_job.get(identifier, "")) if identifier else ""
        if identifier and previous_state == normalized:
            return True
        if identifier:
            controller._download_state_by_job[identifier] = normalized
        return False

    @staticmethod
    def append_download_status_log(controller: DownloadFlowController, *, job_url: str, normalized: str) -> None:
        if controller._active_download_is_multi:
            if normalized == DownloadState.QUEUED.value:
                controller.window.append_log(f"{job_url} -> {DownloadState.QUEUED.value}")
            elif normalized in {DownloadState.ERROR.value, DownloadState.CANCELLED.value}:
                controller.window.append_log(f"{job_url} -> {normalized}")
            return
        if normalized in TERMINAL_DOWNLOAD_STATES:
            controller.window.append_log(f"{job_url} -> {normalized}")

    @staticmethod
    def clear_active_entry_mapping_for_terminal(
        controller: DownloadFlowController,
        *,
        mapped_entry_id: str,
        normalized: str,
    ) -> None:
        if normalized in TERMINAL_DOWNLOAD_STATES and mapped_entry_id:
            controller._batch_entry_to_active_job_id.pop(mapped_entry_id, None)

    @staticmethod
    def update_download_attempt_tracking(
        controller: DownloadFlowController,
        *,
        identifier: str,
        normalized: str,
        entry: BatchEntry | None,
    ) -> None:
        if identifier not in controller._download_attempts_by_job:
            return
        if normalized == DownloadState.DOWNLOADING.value and controller._download_attempts_by_job[identifier] == 0:
            controller._download_attempts_by_job[identifier] = 1
        elif normalized == DownloadState.RETRYING.value:
            controller._download_attempts_by_job[identifier] += 1
        if entry is None:
            return
        entry.attempts = controller._download_attempts_by_job[identifier]
        if normalized == DownloadState.RETRYING.value:
            entry.status = BatchEntryStatus.DOWNLOAD_QUEUED.value
        elif normalized == DownloadState.DOWNLOADING.value:
            entry.status = BatchEntryStatus.DOWNLOADING.value
        elif normalized == DownloadState.PAUSED.value:
            entry.status = BatchEntryStatus.PAUSED.value
        controller.window.update_batch_entry(entry)

    @staticmethod
    def apply_download_terminal_entry_state(
        controller: DownloadFlowController,
        *,
        normalized: str,
        entry: BatchEntry | None,
    ) -> None:
        if entry is None:
            return
        if normalized == DownloadState.ERROR.value:
            entry.status = BatchEntryStatus.FAILED.value
            if not entry.error:
                entry.error = "Download failed. Retry or check URL/settings."
        elif normalized == DownloadState.DONE.value:
            entry.status = BatchEntryStatus.DONE.value
            entry.error = ""
            entry.progress_percent = 100.0
        elif normalized == DownloadState.CANCELLED.value:
            entry.status = BatchEntryStatus.CANCELLED.value
        elif normalized == DownloadState.SKIPPED.value:
            entry.status = BatchEntryStatus.SKIPPED.value
            entry.progress_percent = 100.0
            entry.error = ""
        controller.window.update_batch_entry(entry)
        controller._mark_batch_queue_dirty()

    @staticmethod
    def set_download_terminal_progress(
        controller: DownloadFlowController,
        *,
        identifier: str,
        normalized: str,
    ) -> None:
        if normalized in TERMINAL_DOWNLOAD_STATES:
            if normalized in {DownloadState.DONE.value, DownloadState.SKIPPED.value}:
                controller._download_progress_by_job[identifier] = 100.0
            DownloadFlow.mark_ui_refresh_dirty(controller, overall_progress=True)

    @staticmethod
    def apply_download_terminal_state(
        controller: DownloadFlowController,
        *,
        identifier: str,
        normalized: str,
        entry: BatchEntry | None,
    ) -> None:
        if (not identifier) or (normalized not in TERMINAL_DOWNLOAD_STATES):
            return
        DownloadFlow.set_download_terminal_progress(controller, identifier=identifier, normalized=normalized)
        DownloadFlow.apply_download_terminal_entry_state(controller, normalized=normalized, entry=entry)

    @staticmethod
    def on_download_status(controller: DownloadFlowController, job_id: str, state: str) -> None:
        identifier = str(job_id or "").strip()
        normalized = normalize_download_state(state)
        previous_state = normalize_download_state(controller._download_state_by_job.get(identifier, ""))
        if normalized in TERMINAL_DOWNLOAD_STATES:
            controller._post_processing_notice_job_ids.discard(identifier)
        if DownloadFlow.should_skip_download_status_update(controller, identifier, normalized):
            return
        if DownloadFlow._is_completed_state(previous_state) and (not DownloadFlow._is_completed_state(normalized)):
            controller._download_completed_jobs = max(0, controller._download_completed_jobs - 1)
        elif (not DownloadFlow._is_completed_state(previous_state)) and DownloadFlow._is_completed_state(normalized):
            controller._download_completed_jobs += 1
        job_url = controller._download_url_by_job.get(identifier, identifier)
        DownloadFlow.append_download_status_log(controller, job_url=job_url, normalized=normalized)
        mapped_entry_id = controller._job_to_batch_entry_id.get(identifier, identifier)
        entry = controller._batch_entries_by_id.get(mapped_entry_id)
        DownloadFlow.clear_active_entry_mapping_for_terminal(
            controller,
            mapped_entry_id=mapped_entry_id,
            normalized=normalized,
        )
        DownloadFlow.update_download_attempt_tracking(
            controller,
            identifier=identifier,
            normalized=normalized,
            entry=entry,
        )
        DownloadFlow.apply_download_terminal_state(
            controller,
            identifier=identifier,
            normalized=normalized,
            entry=entry,
        )
        DownloadFlow.mark_ui_refresh_dirty(
            controller,
            batch_stats=bool(controller._active_download_is_multi),
            pause_resume=True,
        )

    @staticmethod
    def refresh_multi_overall_download_progress(controller: DownloadFlowController) -> None:
        total = max(1, int(controller._download_total_jobs or len(controller._download_progress_by_job)))
        downloaded = max(0, int(controller._download_completed_jobs))
        if downloaded > total:
            downloaded = total
        overall_hundredths = int(round((downloaded / total) * 10000))
        if overall_hundredths == controller._last_download_progress_hundredths:
            return
        controller._last_download_progress_hundredths = overall_hundredths
        if hasattr(controller.window, "set_download_progress_count"):
            controller.window.set_download_progress_count(downloaded, total)

    @staticmethod
    def refresh_single_overall_download_progress(controller: DownloadFlowController) -> None:
        total = sum(max(0.0, min(100.0, float(value))) for value in controller._download_progress_by_job.values())
        overall_percent = total / len(controller._download_progress_by_job)
        overall_hundredths = int(round(overall_percent * 100))
        if overall_hundredths == controller._last_download_progress_hundredths:
            return
        controller._last_download_progress_hundredths = overall_hundredths
        controller.window.set_download_progress(overall_hundredths / 100.0)

    @staticmethod
    def refresh_overall_download_progress(controller: DownloadFlowController) -> None:
        if not controller._download_progress_by_job:
            if not controller._active_download_is_multi:
                controller.window.reset_download_progress()
            controller._last_download_progress_hundredths = 0
            return
        if controller._active_download_is_multi:
            DownloadFlow.refresh_multi_overall_download_progress(controller)
            return
        DownloadFlow.refresh_single_overall_download_progress(controller)

    @staticmethod
    def process_download_summary_result(
        controller: DownloadFlowController,
        *,
        result: DownloadResult,
        failed_categories: dict[str, int],
        retryable_failed_entry_ids: list[str],
    ) -> bool:
        updated_entries = False
        state = normalize_download_state(result.state)
        job_id = str(result.job_id or "").strip()
        entry_id = controller._job_to_batch_entry_id.get(job_id, "")
        if state == DownloadState.ERROR.value:
            updated_entries = controller._apply_download_summary_error_result(
                result=result,
                entry_id=entry_id,
                failed_categories=failed_categories,
                retryable_failed_entry_ids=retryable_failed_entry_ids,
            )

        detail = str(result.error or "").strip()
        controller._record_download_history(
            state,
            url=str(result.url or controller._download_url_by_job.get(job_id, "")),
            job_id=job_id,
            output_path=str(result.output_path or ""),
            details=detail,
        )
        return updated_entries

    @staticmethod
    def on_download_finished(controller: DownloadFlowController) -> None:
        DownloadFlow.flush_deferred_ui_refresh(controller)
