from __future__ import annotations

import uuid
import webbrowser
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QByteArray, QObject, QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from .core.app_metadata import APP_VERSION, OFFICIAL_PAGE_URL
from .core.batch_utils import build_download_signature
from .core.config_service import DEFAULT_FILENAME_TEMPLATE, config_to_dict, default_config, load_config, save_config
from .core.dependency_service import DependencyService, dependency_status
from .controller import persistence as state_persistence
from .controller.batch_logic import (
    build_start_all_skip_log_message,
    collect_ready_deduped_entries,
    collect_start_all_counts,
    compute_batch_stats,
    entry_has_analysis_metadata,
    is_entry_eligible_for_active_enqueue,
    is_terminal_batch_state,
)
from .controller.download_runtime import DownloadRuntimeState
from .controller.download_flow import DownloadFlow
from .controller.error_policy import classify_download_error, failure_hint, format_classified_error
from .controller.history_flow import HistoryFlow
from .controller.pause_resume_logic import (
    active_job_id_for_entry as resolve_active_job_id_for_entry,
    active_multi_job_ids as resolve_active_multi_job_ids,
    active_single_job_id as resolve_active_single_job_id,
    all_jobs_paused,
    partition_multi_pause_actions,
)
from .controller.thumbnail_cache import ThumbnailCache
from .controller.thumbnail_flow import ThumbnailFlowCoordinator
from .controller.tutorial_flow import TutorialFlow
from .controller.update_flow import UpdateFlowCoordinator
from .core.download_service import (
    DownloadService,
    coerce_http_url,
    estimate_selection_size_bytes,
    normalize_download_state,
    normalize_batch_url,
    validate_url,
)
from .core.formatting import format_size_human
from .core.models import (
    AppConfig,
    BatchEntry,
    BatchEntryStatus,
    DownloadJob,
    DownloadHistoryEntry,
    DownloadResult,
    DownloadState,
    DownloadSummary,
    FormatProbeResult,
    RetryProfile,
    TERMINAL_DOWNLOAD_STATES,
    UrlAnalysisResult,
    is_audio_format_choice,
)
from .core.paths import (
    resolve_app_asset,
)
from .core.update_service import UpdateService
from .core.url_input import first_non_empty_line, iter_non_empty_lines
from .ui.dialogs import exec_dialog
from .ui.main_window import MainWindow
from .ui.theme import get_theme
from .workers.dependency_worker import DependencyWorker
from .workers.batch_analyze_worker import BatchAnalyzeWorker
from .workers.download_worker import DownloadWorker
from .workers.probe_worker import ProbeWorker
from .workers.single_analyze_worker import SingleAnalyzeWorker
from .workers.stale_cleanup_worker import StaleCleanupWorker

QUEUE_SNAPSHOT_VERSION = 1
QUEUE_SNAPSHOT_SAVE_DELAY_MS = 850
CONFIG_SAVE_DEBOUNCE_MS = 260
QUEUE_SNAPSHOT_MAX_ENTRIES = 2000
HISTORY_MAX_ENTRIES = 200
SINGLE_ANALYSIS_DEBOUNCE_MS = 500
STARTUP_STAGE_DEPENDENCY_DELAY_MS = 120
STARTUP_STAGE_QUEUE_RESTORE_DELAY_MS = 260
STARTUP_STAGE_HISTORY_DELAY_MS = 420
STARTUP_STAGE_FFMPEG_PROMPT_DELAY_MS = 1700
AUTO_UPDATE_START_DELAY_MS = 2600
BATCH_CONCURRENCY_MAX = 16
SPEED_LIMIT_KBPS_MAX = 100000
METADATA_FETCH_TIMEOUT_SECONDS = 8.0
METADATA_SLOW_WARNING_THRESHOLD_MS = 5000
THUMBNAIL_CACHE_MAX_ENTRIES = 350
THUMBNAIL_CACHE_MAX_BYTES = 24 * 1024 * 1024
THUMBNAIL_CACHE_ENTRY_TTL_SECONDS = 20 * 60
THUMBNAIL_CACHE_MAINTENANCE_INTERVAL_MS = 5 * 60 * 1000
_METADATA_FALLBACK_MESSAGE = "Metadata unavailable; using fallback download mode."
_AUDIO_OUTPUT_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".amr",
    ".flac",
    ".m4a",
    ".mp2",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
TutorialStep = dict[str, str | bool]


class AppController(QObject):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.config: AppConfig = load_config()

        icon_path = resolve_app_asset("icon.ico")
        self.window = MainWindow(
            get_theme(self.config.theme_mode),
            theme_mode=self.config.theme_mode,
            ui_scale_percent=self.config.ui_scale_percent,
            icon_path=icon_path,
        )
        self.window.set_close_handler(self._on_close_request)
        self.window.set_config(self.config)
        self.window.set_settings_visible(False, animated=False)

        self.download_service = DownloadService()
        self.dependency_service = DependencyService()
        self.update_service = UpdateService()
        self._download_runtime = DownloadRuntimeState()

        self._download_thread: QThread | None = None
        self._download_worker: DownloadWorker | None = None
        self._dependency_threads: dict[str, QThread] = {}
        self._dependency_workers: dict[str, DependencyWorker] = {}
        self._probe_thread: QThread | None = None
        self._probe_worker: ProbeWorker | None = None
        self._last_probe_url = ""
        self._probe_pending_url = ""
        self._probe_pending_show_errors = False
        self._probe_pending_reveal_all_formats = False
        self._single_analysis_thread: QThread | None = None
        self._single_analysis_worker: SingleAnalyzeWorker | None = None
        self._single_analysis_timer = QTimer(self)
        self._single_analysis_timer.setSingleShot(True)
        self._single_analysis_timer.setInterval(SINGLE_ANALYSIS_DEBOUNCE_MS)
        self._single_analysis_timer.timeout.connect(self._kick_single_url_analysis)
        self._single_analysis_pending_url = ""
        self._single_analysis_active_url = ""
        self._single_analysis_cache: dict[str, UrlAnalysisResult] = {}
        self._last_completed_single_url_normalized = ""
        self._dependency_progress_bucket: dict[str, int] = {}
        self._download_progress_by_job: dict[str, float] = {}
        self._download_attempts_by_job: dict[str, int] = {}
        self._download_state_by_job: dict[str, str] = {}
        self._download_url_by_job: dict[str, str] = {}
        self._post_processing_notice_job_ids: set[str] = set()
        self._active_download_is_multi = False
        self._download_total_jobs = 0
        self._download_completed_jobs = 0
        self._last_download_progress_hundredths = -1
        self._batch_entries_by_id: dict[str, BatchEntry] = {}
        self._batch_entry_order: list[str] = []
        self._batch_analysis_result_by_entry_id: dict[str, UrlAnalysisResult] = {}
        self._batch_analysis_threads: dict[str, QThread] = {}
        self._batch_analysis_workers: dict[str, BatchAnalyzeWorker] = {}
        self._batch_analysis_queue: deque[tuple[str, str]] = deque()
        self._batch_analysis_queued_entry_ids: set[str] = set()
        self._batch_analysis_max_workers = 4
        self._job_to_batch_entry_id: dict[str, str] = {}
        self._batch_entry_to_active_job_id: dict[str, str] = {}
        self._batch_queue_save_timer = QTimer(self)
        self._batch_queue_save_timer.setSingleShot(True)
        self._batch_queue_save_timer.setInterval(QUEUE_SNAPSHOT_SAVE_DELAY_MS)
        self._batch_queue_save_timer.timeout.connect(self._save_batch_queue_snapshot)
        self._config_save_timer = QTimer(self)
        self._config_save_timer.setSingleShot(True)
        self._config_save_timer.setInterval(CONFIG_SAVE_DEBOUNCE_MS)
        self._config_save_timer.timeout.connect(self._flush_config_save)
        self._config_dirty = False
        self._last_saved_config_payload: dict[str, object] | None = None
        self._batch_queue_snapshot_suspended = False
        self._batch_queue_snapshot_revision = 0
        self._batch_queue_snapshot_saved_revision = -1
        self._pending_retry_entry_ids: list[str] = []
        self._download_history: list[DownloadHistoryEntry] = []
        self._thumbnail_cache = ThumbnailCache(
            max_entries=THUMBNAIL_CACHE_MAX_ENTRIES,
            max_bytes=THUMBNAIL_CACHE_MAX_BYTES,
        )
        self._thumbnail_flow = ThumbnailFlowCoordinator(
            owner=self,
            cache=self._thumbnail_cache,
            validate_url=validate_url,
            get_batch_entry=self._get_batch_entry_for_thumbnail,
            set_batch_thumbnail=self.window.set_batch_entry_thumbnail,
            set_single_thumbnail=self.window.set_single_url_thumbnail,
            expected_single_thumbnail_url=self._expected_single_thumbnail_url,
            max_workers=4,
        )
        self._stale_cleanup_thread: QThread | None = None
        self._stale_cleanup_worker: StaleCleanupWorker | None = None
        self._stale_cleanup_pending_reason = ""
        self._startup_staged_initialized = False
        self._startup_ffmpeg_prompt_completed = False
        self._startup_stage_index = -1
        self._startup_stage_sequence: list[tuple[str, int, object]] = []
        self._startup_stage_timer = QTimer(self)
        self._startup_stage_timer.setSingleShot(True)
        self._startup_stage_timer.timeout.connect(self._run_next_startup_stage)
        self._tutorial_active = False
        self._tutorial_steps: list[TutorialStep] = []
        self._tutorial_index = 0
        self._tutorial_prev_settings_visible = False
        self._tutorial_prev_batch_mode = bool(self.config.batch_enabled)
        self._tutorial_wait_step_index = -1
        self._tutorial_wait_cycles = 0
        self._tutorial_step_timer = QTimer(self)
        self._tutorial_step_timer.setSingleShot(True)
        self._tutorial_step_timer.timeout.connect(self._show_tutorial_step)
        self._metadata_slow_warning_timer = QTimer(self)
        self._metadata_slow_warning_timer.setSingleShot(True)
        self._metadata_slow_warning_timer.timeout.connect(self._on_metadata_validation_slow_timeout)
        self._metadata_slow_warning_shown = False
        self._close_shutdown_pending = False
        self._close_wait_notice_shown = False
        self._allow_close_after_shutdown = False
        self._close_wait_timer = QTimer(self)
        self._close_wait_timer.setSingleShot(False)
        self._close_wait_timer.setInterval(120)
        self._close_wait_timer.timeout.connect(self._on_close_wait_tick)
        self._thumbnail_cache_maintenance_timer = QTimer(self)
        self._thumbnail_cache_maintenance_timer.setSingleShot(False)
        self._thumbnail_cache_maintenance_timer.setInterval(THUMBNAIL_CACHE_MAINTENANCE_INTERVAL_MS)
        self._thumbnail_cache_maintenance_timer.timeout.connect(self._run_thumbnail_cache_maintenance)
        self._thumbnail_cache_maintenance_timer.start()
        self._update_flow = UpdateFlowCoordinator(
            owner=self,
            service=self.update_service,
            current_version=APP_VERSION,
            set_update_busy=self.window.set_update_button_busy,
            show_info=lambda title, text: self._show_info(title, text),
            show_warning=lambda title, text: self._show_warning(title, text),
            ask_yes_no=self._ask_yes_no,
            open_url=webbrowser.open,
        )
        DownloadFlow.initialize(self)

        self._apply_metadata_fetch_policy()
        self._connect_signals()
        self._restore_geometry()

    def run(self) -> None:
        self.window.show()
        self._schedule_startup_initialization()

    def _run_startup_guarded(self, label: str, callback) -> None:                
        try:
            callback()
        except Exception as exc:
            self.window.append_log(f"[startup] {label} failed: {exc}")

    def _schedule_startup_initialization(self) -> None:
        if self._startup_staged_initialized:
            return
        self._startup_staged_initialized = True
        self._startup_stage_sequence = self._build_startup_stage_sequence()
        self._startup_stage_index = -1
        if not self._startup_stage_sequence:
            return
        initial_delay = max(0, int(self._startup_stage_sequence[0][1]))
        self._startup_stage_timer.start(initial_delay)

    def _build_startup_stage_sequence(self) -> list[tuple[str, int, object]]:
        dependency_gap = STARTUP_STAGE_DEPENDENCY_DELAY_MS
        queue_restore_gap = max(0, STARTUP_STAGE_QUEUE_RESTORE_DELAY_MS - STARTUP_STAGE_DEPENDENCY_DELAY_MS)
        history_gap = max(0, STARTUP_STAGE_HISTORY_DELAY_MS - STARTUP_STAGE_QUEUE_RESTORE_DELAY_MS)
        ffmpeg_gap = max(0, STARTUP_STAGE_FFMPEG_PROMPT_DELAY_MS - STARTUP_STAGE_HISTORY_DELAY_MS)
        stages: list[tuple[str, int, object]] = [
            ("dependency-status", dependency_gap, self._run_startup_stage_dependency),
            ("restore-batch-queue", queue_restore_gap, self._run_startup_stage_restore_queue),
            ("download-history", history_gap, self._run_startup_stage_history),
            ("stale-part-cleanup", 0, self._run_startup_stage_stale_cleanup),
            ("ffmpeg-required-gate", ffmpeg_gap, self._run_startup_stage_ffmpeg_gate),
        ]
        if self.config.auto_check_updates:
            update_gap = max(0, AUTO_UPDATE_START_DELAY_MS - STARTUP_STAGE_FFMPEG_PROMPT_DELAY_MS)
            stages.append(("auto-update-check", update_gap, self._run_startup_stage_auto_update))
        return stages

    def _run_next_startup_stage(self) -> None:
        self._startup_stage_index += 1
        if self._startup_stage_index >= len(self._startup_stage_sequence):
            self._startup_stage_sequence = []
            return
        label, _delay, callback = self._startup_stage_sequence[self._startup_stage_index]
        self._run_startup_guarded(str(label), callback)
        next_index = self._startup_stage_index + 1
        if next_index >= len(self._startup_stage_sequence):
            return
        next_delay = max(0, int(self._startup_stage_sequence[next_index][1]))
        self._startup_stage_timer.start(next_delay)

    def _run_startup_stage_dependency(self) -> None:
        self._refresh_dependency_status()

    def _run_startup_stage_restore_queue(self) -> None:
        self._attempt_restore_batch_queue_snapshot()

    def _run_startup_stage_history(self) -> None:
        self._load_download_history()

    def _run_startup_stage_stale_cleanup(self) -> None:
        self._request_stale_part_cleanup(reason="startup")

    def _run_startup_stage_ffmpeg_gate(self) -> None:
        self._prompt_startup_ffmpeg_requirement()

    def _run_startup_stage_auto_update(self) -> None:
        self.start_update_check(manual=False)

    def _prompt_startup_ffmpeg_requirement(self) -> None:
        if self._startup_ffmpeg_prompt_completed:
            return
        self._startup_ffmpeg_prompt_completed = True
        status = dependency_status().get("ffmpeg")
        if status and status.installed:
            return
        answer = self._ask_yes_no(
            "FFmpeg required",
            "FFmpeg is required for MediaCrate to merge/download media.\n\nInstall FFmpeg now?",
            default_button=QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self.install_dependency("ffmpeg")
            return
        self._show_warning("FFmpeg required", "MediaCrate requires FFmpeg and will now close.")
        QTimer.singleShot(0, self.app.quit)

    def _connect_signals(self) -> None:
        self._connect_window_action_signals()
        self._connect_window_config_signals()
        self._connect_window_batch_signals()
        self._connect_window_history_signals()

    def _connect_window_action_signals(self) -> None:
        self.window.startDownloadRequested.connect(self.start_downloads)
        self.window.singlePauseResumeRequested.connect(self._on_single_pause_resume_requested)
        self.window.multiPauseResumeAllRequested.connect(self._on_multi_pause_resume_all_requested)
        self.window.stopRequested.connect(self.stop_downloads)
        self.window.openDownloadsRequested.connect(self.open_downloads_folder)
        self.window.officialPageRequested.connect(self._open_official_page)
        self.window.checkUpdatesRequested.connect(self._on_manual_update_check)
        self.window.resetSettingsRequested.connect(self._on_reset_settings_requested)
        self.window.installDependencyRequested.connect(self.install_dependency)
        self.window.tutorialRequested.connect(self._on_tutorial_requested)
        self.window.tutorialNextRequested.connect(self._advance_tutorial)
        self.window.tutorialBackRequested.connect(self._rewind_tutorial)
        self.window.tutorialSkipRequested.connect(lambda: self._end_tutorial(completed=False))
        self.window.tutorialFinishRequested.connect(lambda: self._end_tutorial(completed=True))
        self.window.qualityDropdownOpened.connect(self._on_quality_dropdown_opened)
        self.window.singleFormatChanged.connect(self._on_single_format_changed)
        self.window.singleQualityChanged.connect(self._on_single_quality_changed)
        self.window.urlTextChanged.connect(self._on_url_text_changed)
        self.window.loadOtherFormatsRequested.connect(self._on_load_others_requested)

    def _connect_window_config_signals(self) -> None:
        self.window.themeModeChanged.connect(self._on_theme_mode_changed)
        self.window.uiScaleChanged.connect(self._on_ui_scale_changed)
        self.window.downloadLocationChanged.connect(self._on_download_location_changed)
        self.window.batchConcurrencyChanged.connect(self._on_batch_concurrency_changed)
        self.window.skipExistingFilesChanged.connect(self._on_skip_existing_files_changed)
        self.window.autoStartReadyLinksChanged.connect(self._on_auto_start_ready_links_changed)
        self.window.batchRetryCountChanged.connect(self._on_batch_retry_count_changed)
        self.window.retryProfileChanged.connect(self._on_retry_profile_changed)
        self.window.fallbackDownloadOnMetadataErrorChanged.connect(self._on_fallback_metadata_changed)
        self.window.filenameTemplateChanged.connect(self._on_filename_template_changed)
        self.window.conflictPolicyChanged.connect(self._on_conflict_policy_changed)
        self.window.speedLimitChanged.connect(self._on_speed_limit_changed)
        self.window.adaptiveConcurrencyChanged.connect(self._on_adaptive_concurrency_changed)
        self.window.batchModeChanged.connect(self._on_batch_mode_changed)
        self.window.autoCheckUpdatesChanged.connect(self._on_auto_updates_changed)
        self.window.metadataFetchDisabledChanged.connect(self._on_metadata_fetch_disabled_changed)
        self.window.disableHistoryChanged.connect(self._on_disable_history_changed)
        self.window.stalePartCleanupHoursChanged.connect(self._on_stale_part_cleanup_hours_changed)

    def _connect_window_batch_signals(self) -> None:
        self.window.multiAddUrlRequested.connect(self._on_multi_add_url)
        self.window.multiBulkAddRequested.connect(self._on_multi_bulk_add)
        self.window.multiStartAllRequested.connect(self._on_multi_start_all)
        self.window.multiStartEntryRequested.connect(self._on_multi_start_entry)
        self.window.multiPauseEntryRequested.connect(self._on_multi_pause_entry)
        self.window.multiResumeEntryRequested.connect(self._on_multi_resume_entry)
        self.window.multiEntryFormatChanged.connect(self._on_multi_entry_format_changed)
        self.window.multiEntryQualityChanged.connect(self._on_multi_entry_quality_changed)
        self.window.multiRemoveEntryRequested.connect(self._on_multi_remove_entry)
        self.window.multiExportRequested.connect(self._on_multi_export_urls)

    def _connect_window_history_signals(self) -> None:
        self.window.historyOpenFileRequested.connect(self._on_history_open_file)
        self.window.historyOpenFolderRequested.connect(self._on_history_open_folder)
        self.window.historyRetryRequested.connect(self._on_history_retry_url)
        self.window.historyClearRequested.connect(self._on_history_clear)

    def _restore_geometry(self) -> None:
        encoded = str(self.config.window_geometry or "").strip()
        if not encoded:
            return
        try:
            payload = QByteArray.fromBase64(encoded.encode("ascii"))
            if payload:
                self.window.restoreGeometry(payload)
        except (UnicodeEncodeError, ValueError):
            return

    def _flush_config_save(self) -> None:
        try:
            self.config.window_geometry = (
                self.window.saveGeometry().toBase64().data().decode("ascii")
            )
        except Exception:
            self.config.window_geometry = ""
        payload = config_to_dict(self.config)
        if (not self._config_dirty) and self._last_saved_config_payload is not None:
            if payload == self._last_saved_config_payload:
                return
        saved_path = save_config(self.config)
        if saved_path:
            self._last_saved_config_payload = dict(payload)
            self._config_dirty = False

    def _save_config(self, *, deferred: bool = False) -> None:
        self._config_dirty = True
        if deferred:
            self._config_save_timer.start()
            return
        self._config_save_timer.stop()
        self._flush_config_save()

    @property
    def _download_progress_by_job(self) -> dict[str, float]:
        return self._download_runtime.progress_by_job

    @_download_progress_by_job.setter
    def _download_progress_by_job(self, value: dict[str, float]) -> None:
        self._download_runtime.progress_by_job = dict(value or {})

    @property
    def _download_attempts_by_job(self) -> dict[str, int]:
        return self._download_runtime.attempts_by_job

    @_download_attempts_by_job.setter
    def _download_attempts_by_job(self, value: dict[str, int]) -> None:
        self._download_runtime.attempts_by_job = dict(value or {})

    @property
    def _download_state_by_job(self) -> dict[str, str]:
        return self._download_runtime.state_by_job

    @_download_state_by_job.setter
    def _download_state_by_job(self, value: dict[str, str]) -> None:
        self._download_runtime.state_by_job = dict(value or {})

    @property
    def _download_url_by_job(self) -> dict[str, str]:
        return self._download_runtime.url_by_job

    @_download_url_by_job.setter
    def _download_url_by_job(self, value: dict[str, str]) -> None:
        self._download_runtime.url_by_job = dict(value or {})

    @property
    def _active_download_is_multi(self) -> bool:
        return bool(self._download_runtime.active_download_is_multi)

    @_active_download_is_multi.setter
    def _active_download_is_multi(self, value: bool) -> None:
        self._download_runtime.active_download_is_multi = bool(value)

    @property
    def _download_total_jobs(self) -> int:
        return int(self._download_runtime.total_jobs)

    @_download_total_jobs.setter
    def _download_total_jobs(self, value: int) -> None:
        self._download_runtime.total_jobs = max(0, int(value))

    @property
    def _download_completed_jobs(self) -> int:
        return int(self._download_runtime.completed_jobs)

    @_download_completed_jobs.setter
    def _download_completed_jobs(self, value: int) -> None:
        self._download_runtime.completed_jobs = max(0, int(value))

    @property
    def _last_download_progress_hundredths(self) -> int:
        return int(self._download_runtime.last_progress_hundredths)

    @_last_download_progress_hundredths.setter
    def _last_download_progress_hundredths(self, value: int) -> None:
        self._download_runtime.last_progress_hundredths = int(value)

    @property
    def _job_to_batch_entry_id(self) -> dict[str, str]:
        return self._download_runtime.job_to_batch_entry_id

    @_job_to_batch_entry_id.setter
    def _job_to_batch_entry_id(self, value: dict[str, str]) -> None:
        self._download_runtime.job_to_batch_entry_id = dict(value or {})

    @property
    def _batch_entry_to_active_job_id(self) -> dict[str, str]:
        return self._download_runtime.batch_entry_to_active_job_id

    @_batch_entry_to_active_job_id.setter
    def _batch_entry_to_active_job_id(self, value: dict[str, str]) -> None:
        self._download_runtime.batch_entry_to_active_job_id = dict(value or {})

    @staticmethod
    def _classify_download_error(message: str) -> tuple[str, bool]:
        return classify_download_error(message)

    @staticmethod
    def _format_classified_error(message: str) -> str:
        return format_classified_error(message)

    @staticmethod
    def _failure_hint(category: str) -> str:
        return failure_hint(category)

    def _clear_batch_queue_snapshot(self) -> None:
        state_persistence.clear_path(state_persistence.queue_snapshot_path())
        self._batch_queue_snapshot_saved_revision = self._batch_queue_snapshot_revision

    def _mark_batch_queue_dirty(self) -> None:
        if self._batch_queue_snapshot_suspended:
            return
        self._batch_queue_snapshot_revision += 1
        self._batch_queue_save_timer.start()

    def _save_batch_queue_snapshot(self, *, force: bool = False) -> None:
        if self._batch_queue_snapshot_suspended and not force:
            return
        if (not force) and (self._batch_queue_snapshot_revision == self._batch_queue_snapshot_saved_revision):
            return
        path = state_persistence.queue_snapshot_path()
        entries = self._ordered_batch_entries()
        if not entries:
            self._clear_batch_queue_snapshot()
            return
        serialized = [state_persistence.serialize_batch_entry(entry) for entry in entries[:QUEUE_SNAPSHOT_MAX_ENTRIES]]
        payload = {
            "version": QUEUE_SNAPSHOT_VERSION,
            "entries": serialized,
        }
        if state_persistence.save_json_atomically(path, payload):
            self._batch_queue_snapshot_saved_revision = self._batch_queue_snapshot_revision

    def _read_queue_snapshot_entries_payload(self) -> list[object] | None:
        path = state_persistence.queue_snapshot_path()
        if not path.exists():
            return None
        raw = state_persistence.read_json(path)
        if raw is None:
            self._clear_batch_queue_snapshot()
            return []
        entries_payload = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries_payload, list) or not entries_payload:
            self._clear_batch_queue_snapshot()
            return []
        return entries_payload

    def _deserialize_restored_batch_entries(self, entries_payload: list[object]) -> list[BatchEntry]:
        restored: list[BatchEntry] = []
        for item in entries_payload:
            entry = state_persistence.deserialize_batch_entry(
                item,
                dedupe_preserve=self._dedupe_preserve,
            )
            if entry is not None:
                restored.append(entry)
        return restored

    def _apply_restored_batch_entries(self, restored: list[BatchEntry]) -> None:
        self._batch_queue_snapshot_suspended = True
        try:
            self._batch_entries_by_id = {entry.entry_id: entry for entry in restored}
            self._batch_entry_order = [entry.entry_id for entry in restored]
            self._batch_analysis_result_by_entry_id = {}
            if not self.config.batch_enabled:
                self.window.multi_mode_button.setChecked(True)
            self._refresh_batch_entries_view()
            self.window.append_log(f"Restored {len(restored)} URL(s) from previous session.")
        finally:
            self._batch_queue_snapshot_suspended = False
        self._mark_batch_queue_dirty()
        self._apply_metadata_fetch_policy_to_batch_entries()

    def _attempt_restore_batch_queue_snapshot(self) -> None:
        entries_payload = self._read_queue_snapshot_entries_payload()
        if entries_payload is None:
            return
        if not entries_payload:
            return

        response = self._ask_yes_no(
            "Restore previous queue",
            f"Restore {len(entries_payload)} Multi-URL item(s) from the previous session?",
            default_button=QMessageBox.Yes,
        )
        if response != QMessageBox.Yes:
            self._clear_batch_queue_snapshot()
            return

        restored = self._deserialize_restored_batch_entries(entries_payload)
        if not restored:
            self._clear_batch_queue_snapshot()
            return

        self._apply_restored_batch_entries(restored)

    def _is_history_disabled(self) -> bool:
        return bool(self.config.disable_history)

    @staticmethod
    def _is_finished_history_state(state: str) -> bool:
        normalized = normalize_download_state(state)
        return normalized in {DownloadState.DONE.value, DownloadState.SKIPPED.value}

    @classmethod
    def _is_unfinished_history_state(cls, state: str) -> bool:
        normalized = str(state or "").strip().lower()
        if not normalized:
            return False
        return not cls._is_finished_history_state(normalized)

    @staticmethod
    def _part_file_candidates_from_output_path(output_path: str) -> set[Path]:
        raw = str(output_path or "").strip()
        if not raw:
            return set()
        try:
            path = Path(raw).expanduser()
        except Exception:
            return set()
        candidates: set[Path] = set()
        lowered_name = str(path.name or "").lower()
        if lowered_name.endswith(".part"):
            candidates.add(path)
        else:
            candidates.add(Path(f"{path}.part"))
            try:
                candidates.add(path.with_suffix(".part"))
            except ValueError:
                pass
            if path.suffix:
                try:
                    candidates.add(path.with_suffix(f"{path.suffix}.part"))
                except ValueError:
                    pass
                stem_pattern = f"{path.stem}*.part"
            else:
                stem_pattern = f"{path.name}*.part"
            parent = path.parent
            try:
                if parent.exists() and parent.is_dir():
                    for match in parent.glob(stem_pattern):
                        if match.is_file():
                            candidates.add(match)
            except OSError:
                pass
        return candidates

    @classmethod
    def _collect_unfinished_history_part_paths(cls, entries: list[DownloadHistoryEntry]) -> set[Path]:
        paths: set[Path] = set()
        for entry in entries:
            if not cls._is_unfinished_history_state(str(entry.state or "")):
                continue
            paths.update(cls._part_file_candidates_from_output_path(str(entry.output_path or "")))
        return paths

    @staticmethod
    def _delete_part_files(paths: set[Path]) -> int:
        deleted = 0
        for path in paths:
            try:
                if path.exists() and path.is_file():
                    path.unlink(missing_ok=True)
                    deleted += 1
            except OSError:
                continue
        return deleted

    def _cleanup_unfinished_part_files_from_history_entries(self, entries: list[DownloadHistoryEntry]) -> int:
        paths = self._collect_unfinished_history_part_paths(entries)
        return self._delete_part_files(paths)

    def _cleanup_part_file_for_download_result(self, *, result_state: str, output_path: str) -> int:
        if not self._is_unfinished_history_state(result_state):
            return 0
        paths = self._part_file_candidates_from_output_path(output_path)
        return self._delete_part_files(paths)

    def _stale_cleanup_hours(self) -> int:
        return max(0, int(self.config.stale_part_cleanup_hours))

    def _is_stale_cleanup_running(self) -> bool:
        return bool(self._stale_cleanup_thread and self._stale_cleanup_thread.isRunning())

    def _request_stale_part_cleanup(self, *, reason: str) -> None:
        if self._is_download_running():
            return
        if self._stale_cleanup_hours() <= 0:
            return
        if self._is_stale_cleanup_running():
            self._stale_cleanup_pending_reason = str(reason or "").strip() or "queued"
            return
        thread = QThread(self)
        worker = StaleCleanupWorker(
            download_location=str(self.config.download_location or ""),
            max_age_hours=self._stale_cleanup_hours(),
            reason=str(reason or "").strip() or "manual",
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finishedSummary.connect(self._on_stale_cleanup_summary, Qt.ConnectionType.QueuedConnection)
        worker.errorRaised.connect(self._on_stale_cleanup_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_stale_cleanup_finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        self._stale_cleanup_thread = thread
        self._stale_cleanup_worker = worker
        thread.start()

    def _on_stale_cleanup_summary(self, payload: object) -> None:
        if (not isinstance(payload, tuple)) or len(payload) != 3:
            return
        reason = str(payload[0] or "").strip() or "manual"
        try:
            deleted_count = max(0, int(payload[1]))
        except (TypeError, ValueError):
            deleted_count = 0
        try:
            hours = max(0, int(payload[2]))
        except (TypeError, ValueError):
            hours = self._stale_cleanup_hours()
        if deleted_count <= 0:
            return
        self.window.append_log(
            f"Stale .part cleanup ({reason}): deleted {deleted_count} file(s) older than {hours}h."
        )

    def _on_stale_cleanup_error(self, _job_id: str, error: str) -> None:
        message = str(error or "").strip()
        if not message:
            return
        self.window.append_log(f"Stale .part cleanup error: {message}")

    def _on_stale_cleanup_finished(self) -> None:
        self._stale_cleanup_worker = None
        self._stale_cleanup_thread = None
        pending_reason = str(self._stale_cleanup_pending_reason or "").strip()
        self._stale_cleanup_pending_reason = ""
        if pending_reason and (not self._is_download_running()):
            self._request_stale_part_cleanup(reason=pending_reason)

    def _clear_history_file(self) -> None:
        path = state_persistence.history_path()
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _apply_history_policy(self, *, show_feedback: bool) -> None:
        if self._is_history_disabled():
            deleted_count = self._cleanup_unfinished_part_files_from_history_entries(self._download_history)
            self._download_history = []
            self._clear_history_file()
            self._refresh_history_view()
            if show_feedback:
                if deleted_count > 0:
                    self.window.append_log(
                        f"History disabled. Cleared history and deleted {deleted_count} unfinished .part file(s)."
                    )
                else:
                    self.window.append_log("History disabled. Cleared history.")
            return
        self._refresh_history_view()

    def _refresh_history_view(self) -> None:
        if self._is_history_disabled():
            self.window.set_download_history_entries([])
            return
        self.window.set_download_history_entries(self._download_history)

    def _set_download_history_entries(self, entries: list[DownloadHistoryEntry]) -> None:
        if self._is_history_disabled():
            self._download_history = []
            self._refresh_history_view()
            return
        self._download_history = entries[:HISTORY_MAX_ENTRIES]
        self._refresh_history_view()

    def _deserialize_history_entries(self, items: list[object]) -> list[DownloadHistoryEntry]:
        entries: list[DownloadHistoryEntry] = []
        for item in items:
            parsed = state_persistence.deserialize_history_entry(item)
            if parsed is not None:
                entries.append(parsed)
        return entries

    def _load_download_history(self) -> None:
        if self._is_history_disabled():
            self._download_history = []
            self._clear_history_file()
            self._refresh_history_view()
            return
        path = state_persistence.history_path()
        if not path.exists():
            self._refresh_history_view()
            return
        raw = state_persistence.read_json(path)
        if raw is None:
            self._set_download_history_entries([])
            return
        items = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._set_download_history_entries([])
            return
        self._set_download_history_entries(self._deserialize_history_entries(items))

    def _save_download_history(self) -> None:
        if self._is_history_disabled():
            self._clear_history_file()
            return
        path = state_persistence.history_path()
        payload = {
            "entries": [
                state_persistence.serialize_history_entry(item)
                for item in self._download_history[:HISTORY_MAX_ENTRIES]
            ]
        }
        state_persistence.save_json_atomically(path, payload)

    def _history_title_from_batch_entry(self, job_id: str) -> str:
        entry_id = self._job_to_batch_entry_id.get(str(job_id or "").strip(), "")
        if not entry_id:
            return ""
        batch_entry = self._batch_entries_by_id.get(entry_id)
        if batch_entry is None:
            return ""
        return str(batch_entry.title or "")

    @staticmethod
    def _history_title_from_output_path(output_path: str) -> str:
        if not str(output_path or "").strip():
            return ""
        try:
            return Path(str(output_path)).stem
        except Exception:
            return ""

    def _resolve_download_history_title(self, *, job_id: str, output_path: str) -> str:
        title = self._history_title_from_batch_entry(job_id)
        if title:
            return title
        return self._history_title_from_output_path(output_path)

    @staticmethod
    def _build_download_history_entry(
        *,
        result_state: str,
        url: str,
        title: str,
        output_path: str,
        details: str,
    ) -> DownloadHistoryEntry:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        return DownloadHistoryEntry(
            timestamp_utc=timestamp,
            url=str(url or ""),
            title=str(title or ""),
            state=str(result_state or "").strip().lower(),
            output_path=str(output_path or ""),
            details=str(details or ""),
        )

    def _record_download_history(self, result_state: str, *, url: str, job_id: str, output_path: str = "", details: str = "") -> None:
        normalized_state = normalize_download_state(result_state)
        if self._is_history_disabled():
            deleted_count = self._cleanup_part_file_for_download_result(
                result_state=normalized_state,
                output_path=str(output_path or ""),
            )
            if deleted_count > 0:
                self.window.append_log(
                    f"History disabled: deleted {deleted_count} unfinished .part file(s)."
                )
            return
        title = self._resolve_download_history_title(job_id=job_id, output_path=output_path)
        new_entry = self._build_download_history_entry(
            result_state=normalized_state,
            url=str(url or ""),
            title=title,
            output_path=str(output_path or ""),
            details=str(details or ""),
        )
        self._download_history.insert(0, new_entry)
        self._download_history = self._download_history[:HISTORY_MAX_ENTRIES]
        self._save_download_history()
        self._refresh_history_view()

    def _stop_dependency_workers(self) -> None:
        for worker in list(self._dependency_workers.values()):
            worker.stop()

    def _stop_batch_analysis_workers(self) -> None:
        for worker in list(self._batch_analysis_workers.values()):
            worker.stop()
        self._batch_analysis_queue.clear()
        self._batch_analysis_queued_entry_ids.clear()
        self._stop_metadata_slow_warning_watch_if_idle()

    def _stop_thumbnail_workers(self) -> None:
        if hasattr(self, "_thumbnail_flow"):
            self._thumbnail_flow.stop_all(clear_pending=True)

    def _stop_optional_workers(self) -> None:
        if hasattr(self, "_update_flow"):
            self._update_flow.stop()
        if self._probe_worker:
            self._probe_worker.stop()
        if self._single_analysis_worker:
            self._single_analysis_worker.stop()
        if self._stale_cleanup_worker:
            self._stale_cleanup_worker.stop()
        self._stop_metadata_slow_warning_watch_if_idle()

    def _running_worker_threads(self) -> list[QThread]:
        candidates: list[QThread] = []
        if self._download_thread is not None:
            candidates.append(self._download_thread)
        if hasattr(self, "_update_flow"):
            update_thread = self._update_flow.running_thread()
            if update_thread is not None:
                candidates.append(update_thread)
        if self._probe_thread is not None:
            candidates.append(self._probe_thread)
        if self._single_analysis_thread is not None:
            candidates.append(self._single_analysis_thread)
        if hasattr(self, "_thumbnail_flow"):
            candidates.extend(self._thumbnail_flow.running_threads())
        if self._stale_cleanup_thread is not None:
            candidates.append(self._stale_cleanup_thread)
        candidates.extend(list(self._dependency_threads.values()))
        candidates.extend(list(self._batch_analysis_threads.values()))
        running: list[QThread] = []
        seen_ids: set[int] = set()
        for thread in candidates:
            if thread is None:
                continue
            ident = id(thread)
            if ident in seen_ids:
                continue
            seen_ids.add(ident)
            try:
                if thread.isRunning():
                    running.append(thread)
            except RuntimeError:
                continue
        return running

    def _confirm_close_with_active_workers(self) -> bool:
        running = self._running_worker_threads()
        if not running:
            return True
        answer = self._ask_yes_no(
            "Background tasks running",
            "MediaCrate still has background tasks running.\n\nStop them and exit now?",
            default_button=QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _has_active_metadata_validation(self) -> bool:
        single_running = bool(self._single_analysis_thread and self._single_analysis_thread.isRunning())
        batch_running = bool(self._batch_analysis_threads)
        batch_queued = bool(self._batch_analysis_queued_entry_ids)
        return bool(single_running or batch_running or batch_queued)

    def _start_metadata_slow_warning_watch(self) -> None:
        if self._is_metadata_fetch_disabled():
            return
        if self._metadata_slow_warning_shown:
            return
        if self._metadata_slow_warning_timer.isActive():
            return
        if not self._has_active_metadata_validation():
            return
        self._metadata_slow_warning_timer.start(int(METADATA_SLOW_WARNING_THRESHOLD_MS))

    def _stop_metadata_slow_warning_watch_if_idle(self) -> None:
        if self._has_active_metadata_validation():
            return
        self._metadata_slow_warning_timer.stop()
        self._metadata_slow_warning_shown = False

    def _on_metadata_validation_slow_timeout(self) -> None:
        if self._metadata_slow_warning_shown:
            return
        if not self._has_active_metadata_validation():
            return
        self._metadata_slow_warning_shown = True
        self._show_info(
            "Metadata is taking a while",
            "Validation is taking longer than expected.\n\nYou can still try downloading while metadata continues loading.",
        )

    @staticmethod
    def _wait_for_thread_shutdown(thread: QThread | None, *, timeout_ms: int) -> bool:
        if thread is None:
            return True
        try:
            if not thread.isRunning():
                return True
        except RuntimeError:
            return True
        try:
            thread.quit()
        except RuntimeError:
            return False
        try:
            return bool(thread.wait(max(0, int(timeout_ms))))
        except RuntimeError:
            return True

    def _wait_for_metadata_threads_to_finish(self, *, timeout_ms: int = 1200) -> bool:
        threads: list[QThread] = []
        if self._single_analysis_thread is not None:
            threads.append(self._single_analysis_thread)
        if self._probe_thread is not None:
            threads.append(self._probe_thread)
        threads.extend(list(self._batch_analysis_threads.values()))
        for thread in threads:
            if not self._wait_for_thread_shutdown(thread, timeout_ms=timeout_ms):
                return False
        return True

    def _wait_for_all_worker_threads_to_finish(self, *, timeout_ms: int) -> tuple[bool, list[QThread]]:
        remaining: list[QThread] = []
        for thread in self._running_worker_threads():
            if not self._wait_for_thread_shutdown(thread, timeout_ms=timeout_ms):
                remaining.append(thread)
        return (len(remaining) == 0), remaining

    @staticmethod
    def _force_terminate_threads(threads: list[QThread]) -> None:
        for thread in threads:
            try:
                if not thread.isRunning():
                    continue
            except RuntimeError:
                continue
            try:
                thread.terminate()
            except RuntimeError:
                continue
            try:
                thread.wait(300)
            except RuntimeError:
                continue

    def _begin_shutdown_sequence(self) -> None:
        if bool(getattr(self, "_tutorial_active", False)):
            self._end_tutorial(completed=False)
        self._batch_queue_save_timer.stop()
        self._config_save_timer.stop()
        self._single_analysis_timer.stop()
        self._startup_stage_timer.stop()
        self._metadata_slow_warning_timer.stop()
        if hasattr(self, "_thumbnail_cache_maintenance_timer"):
            self._thumbnail_cache_maintenance_timer.stop()
        self._save_batch_queue_snapshot(force=True)
        self.stop_downloads()
        self._stop_dependency_workers()
        self._stop_batch_analysis_workers()
        self._stop_thumbnail_workers()
        self._stop_optional_workers()

    def _finalize_shutdown_sequence(self) -> None:
        self._close_wait_timer.stop()
        self._close_shutdown_pending = False
        self._allow_close_after_shutdown = True
        self._close_wait_notice_shown = False
        self._flush_config_save()
        self._save_download_history()
        if hasattr(self, "_thumbnail_flow"):
            self._thumbnail_flow.clear_cache()

    def _on_close_wait_tick(self) -> None:
        if not self._close_shutdown_pending:
            self._close_wait_timer.stop()
            return
        if self._running_worker_threads():
            return
        self._finalize_shutdown_sequence()
        QTimer.singleShot(0, self.window.close)

    def _on_close_request(self) -> bool:
        if bool(getattr(self, "_allow_close_after_shutdown", False)):
            return True
        if bool(getattr(self, "_close_shutdown_pending", False)):
            running_checker = getattr(self, "_running_worker_threads", None)
            if callable(running_checker):
                running_threads = running_checker()
            else:
                metadata_waiter = getattr(self, "_wait_for_metadata_threads_to_finish", None)
                running_threads = [object()] if callable(metadata_waiter) and (not metadata_waiter()) else []
            if running_threads:
                if not bool(getattr(self, "_close_wait_notice_shown", False)):
                    self._close_wait_notice_shown = True
                    self._show_info(
                        "Still shutting down",
                        "Background tasks are still stopping.\n\nPlease wait a moment.",
                    )
                return False
            if callable(getattr(self, "_finalize_shutdown_sequence", None)):
                self._finalize_shutdown_sequence()
            else:
                self._flush_config_save()
                self._save_download_history()
            return True
        if not self._confirm_close_during_ffmpeg_install():
            return False
        if hasattr(self, "_confirm_close_with_active_workers"):
            if not self._confirm_close_with_active_workers():
                return False
        if callable(getattr(self, "_begin_shutdown_sequence", None)):
            self._begin_shutdown_sequence()
        else:
            self._batch_queue_save_timer.stop()
            self._config_save_timer.stop()
            self._single_analysis_timer.stop()
            self._startup_stage_timer.stop()
            self._metadata_slow_warning_timer.stop()
            self._save_batch_queue_snapshot(force=True)
            self.stop_downloads()
            self._stop_dependency_workers()
            self._stop_batch_analysis_workers()
            self._stop_thumbnail_workers()
            self._stop_optional_workers()

        running_checker = getattr(self, "_running_worker_threads", None)
        if callable(running_checker):
            running_threads = running_checker()
        else:
            metadata_waiter = getattr(self, "_wait_for_metadata_threads_to_finish", None)
            running_threads = [object()] if callable(metadata_waiter) and (not metadata_waiter()) else []
        if running_threads:
            self._close_shutdown_pending = True
            self._close_wait_notice_shown = False
            close_wait_timer = getattr(self, "_close_wait_timer", None)
            if close_wait_timer is not None and hasattr(close_wait_timer, "isActive") and hasattr(close_wait_timer, "start"):
                if not close_wait_timer.isActive():
                    close_wait_timer.start()
            self._show_info(
                "Still shutting down",
                "Background tasks are still stopping.\n\nThe app will close when shutdown completes.",
            )
            return False
        if callable(getattr(self, "_finalize_shutdown_sequence", None)):
            self._finalize_shutdown_sequence()
        else:
            self._flush_config_save()
            self._save_download_history()
        return True

    def _on_tutorial_requested(self) -> None:
        TutorialFlow.on_tutorial_requested(self)

    def _build_tutorial_steps(self) -> list[TutorialStep]:
        return TutorialFlow.build_tutorial_steps()

    def start_tutorial(self) -> None:
        TutorialFlow.start_tutorial(self)

    def _show_tutorial_step(self) -> None:
        TutorialFlow.show_tutorial_step(self)

    def _advance_tutorial(self) -> None:
        TutorialFlow.advance_tutorial(self)

    def _rewind_tutorial(self) -> None:
        TutorialFlow.rewind_tutorial(self)

    def _end_tutorial(self, completed: bool) -> None:
        TutorialFlow.end_tutorial(self, completed)

    def _confirm_close_during_ffmpeg_install(self) -> bool:
        if not self._is_dependency_install_running("ffmpeg"):
            return True
        answer = self._ask_yes_no(
            "FFmpeg install in progress",
            "FFmpeg is currently downloading/installing.\n\nClosing now will cancel installation. Exit anyway?",
            default_button=QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _build_message_box(
        self,
        *,
        icon: QMessageBox.Icon,
        title: str,
        text: str,
        buttons: QMessageBox.StandardButtons = QMessageBox.Ok,
        default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    ) -> QMessageBox:
        return self.window._build_message_box(
            icon=icon,
            title=title,
            text=text,
            buttons=buttons,
            default_button=default_button,
        )

    def _restore_cursor_state(self) -> None:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        self.window.refresh_cursor_state()

    def _exec_dialog(self, dialog: QWidget) -> int:
        return exec_dialog(dialog, on_after=self._restore_cursor_state)

    def _show_info(self, title: str, text: str) -> int:
        return self._exec_dialog(self._build_message_box(icon=QMessageBox.Information, title=title, text=text))

    def _show_warning(self, title: str, text: str) -> int:
        return self._exec_dialog(self._build_message_box(icon=QMessageBox.Warning, title=title, text=text))

    def _ask_yes_no(
        self,
        title: str,
        text: str,
        *,
        default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    ) -> int:
        return self._exec_dialog(
            self._build_message_box(
                icon=QMessageBox.Question,
                title=title,
                text=text,
                buttons=QMessageBox.Yes | QMessageBox.No,
                default_button=default_button,
            )
        )

    def _on_theme_mode_changed(self, mode: str) -> None:
        normalized = "light" if str(mode).strip().lower() == "light" else "dark"
        self.config.theme_mode = normalized
        self.window.set_theme(get_theme(normalized), normalized)
        self._save_config()

    def _on_ui_scale_changed(self, value: int) -> None:
        self.config.ui_scale_percent = int(value)
        self._save_config()

    def _on_download_location_changed(self, path: str) -> None:
        candidate = str(path or "").strip()
        if not candidate:
            return
        self.config.download_location = candidate
        self._save_config()

    def _on_batch_concurrency_changed(self, value: int) -> None:
        self.config.batch_concurrency = max(1, min(BATCH_CONCURRENCY_MAX, int(value)))
        self._save_config(deferred=True)

    def _on_batch_mode_changed(self, value: bool) -> None:
        self.config.batch_enabled = bool(value)
        self._save_config()
        if bool(value):
            self._single_analysis_timer.stop()
            self._single_analysis_pending_url = ""
            self.window.set_single_url_validation_busy(False)
            self.window.set_single_url_analysis_state("idle")
            self.window.set_single_url_thumbnail(None, "")
            self._refresh_batch_entries_view()
        elif not self._is_download_running():
            self.window.reset_download_progress()
            self._apply_metadata_fetch_policy()

    def _on_skip_existing_files_changed(self, value: bool) -> None:
        self.config.skip_existing_files = bool(value)
        self._save_config()

    def _on_auto_start_ready_links_changed(self, value: bool) -> None:
        self.config.auto_start_ready_links = bool(value)
        self._save_config()
        if (not self.config.auto_start_ready_links) or (not self._is_download_running()) or (not self._active_download_is_multi):
            return
        deduped_candidates, _skipped = self._collect_ready_deduped_entries(
            self._ordered_batch_entries(),
            require_unassigned=True,
        )
        self._enqueue_entries_into_active_download(deduped_candidates, source_label="auto-start")

    def _on_batch_retry_count_changed(self, value: int) -> None:
        self.config.batch_retry_count = max(0, min(3, int(value)))
        self._save_config(deferred=True)

    def _on_retry_profile_changed(self, value: str) -> None:
        requested = str(value or "").strip().lower()
        valid_profiles = {item.value for item in RetryProfile}
        if requested not in valid_profiles:
            requested = RetryProfile.BASIC.value
        self.config.retry_profile = requested
        self._save_config(deferred=True)

    def _on_fallback_metadata_changed(self, value: bool) -> None:
        self.config.fallback_download_on_metadata_error = bool(value)
        self._save_config()
        if self._is_metadata_fallback_enabled():
            self._apply_metadata_fallback_policy_to_batch_entries()
            return
        self._reconcile_disabled_metadata_fallback_entries()

    def _on_filename_template_changed(self, value: str) -> None:
        template = str(value or "").strip()
        self.config.filename_template = template or DEFAULT_FILENAME_TEMPLATE
        self._save_config()

    def _on_conflict_policy_changed(self, value: str) -> None:
        policy = str(value or "skip").strip().lower()
        if policy not in {"skip", "rename", "overwrite"}:
            policy = "skip"
        self.config.conflict_policy = policy
        self._save_config()

    def _on_speed_limit_changed(self, value: int) -> None:
        self.config.download_speed_limit_kbps = max(0, min(SPEED_LIMIT_KBPS_MAX, int(value)))
        self._save_config(deferred=True)

    def _on_adaptive_concurrency_changed(self, value: bool) -> None:
        self.config.adaptive_batch_concurrency = bool(value)
        self._save_config()

    def _on_auto_updates_changed(self, value: bool) -> None:
        self.config.auto_check_updates = bool(value)
        self._save_config()

    def _on_metadata_fetch_disabled_changed(self, value: bool) -> None:
        self.config.disable_metadata_fetch = bool(value)
        self._save_config()
        self._apply_metadata_fetch_policy()

    def _on_disable_history_changed(self, value: bool) -> None:
        self.config.disable_history = bool(value)
        self._save_config()
        self._apply_history_policy(show_feedback=True)

    def _on_stale_part_cleanup_hours_changed(self, value: int) -> None:
        self.config.stale_part_cleanup_hours = max(0, min(24 * 30, int(value)))
        self._save_config(deferred=True)
        if not self._is_download_running():
            self._request_stale_part_cleanup(reason="settings")

    def _on_reset_settings_requested(self) -> None:
        if self._is_download_running():
            self._show_info("Download in progress", "Stop downloads before resetting settings.")
            return
        answer = self._ask_yes_no(
            "Reset settings",
            "Reset all settings to their defaults?\n\nYour current window size will be kept.",
            default_button=QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        defaults = default_config()
        defaults.window_geometry = self.config.window_geometry
        self.config = defaults
        self.window.set_theme(get_theme(self.config.theme_mode), self.config.theme_mode)
        self.window.set_config(self.config)
        self._apply_metadata_fetch_policy()
        self._apply_history_policy(show_feedback=False)
        self._request_stale_part_cleanup(reason="reset-settings")
        self._save_config()
        self.window.append_log("Settings reset to defaults.")

    def _metadata_fetch_timeout_seconds(self) -> float:
        return float(METADATA_FETCH_TIMEOUT_SECONDS)

    def _is_metadata_fetch_disabled(self) -> bool:
        return bool(self.config.disable_metadata_fetch)

    def _is_metadata_fallback_enabled(self) -> bool:
        return bool(self.config.fallback_download_on_metadata_error)

    @staticmethod
    def _is_pre_download_metadata_failure_entry(entry: BatchEntry) -> bool:
        return (
            bool(entry.syntax_valid)
            and str(entry.status or "").strip().lower() == BatchEntryStatus.FAILED.value
            and int(entry.attempts or 0) <= 0
            and float(entry.progress_percent or 0.0) <= 0.0
        )

    def _apply_metadata_failure_fallback_to_entry(self, entry: BatchEntry, *, reason: str = "") -> None:
        detail = str(reason or "").strip()
        message = _METADATA_FALLBACK_MESSAGE if not detail else f"{_METADATA_FALLBACK_MESSAGE} ({detail})"
        entry.status = BatchEntryStatus.VALID.value
        entry.error = message
        if not entry.available_formats:
            entry.available_formats = ["VIDEO", "AUDIO", "MP4", "MP3"]
        if not entry.available_qualities:
            entry.available_qualities = ["BEST QUALITY"]
        self._sync_entry_choices_after_analysis(entry)

    @staticmethod
    def _is_metadata_fallback_marked_entry(entry: BatchEntry) -> bool:
        return (
            str(entry.status or "").strip().lower() == BatchEntryStatus.VALID.value
            and str(entry.error or "").strip().startswith(_METADATA_FALLBACK_MESSAGE)
        )

    def _apply_metadata_fallback_policy_to_batch_entries(self) -> None:
        if not self._is_metadata_fallback_enabled():
            return
        changed = False
        for entry in self._ordered_batch_entries():
            if not self._is_pre_download_metadata_failure_entry(entry):
                continue
            self._apply_metadata_failure_fallback_to_entry(entry)
            changed = True
            self._maybe_auto_start_ready_entry(entry)
        if not changed:
            return
        self._refresh_batch_entries_view()
        self._mark_batch_queue_dirty()

    def _reconcile_disabled_metadata_fallback_entries(self) -> None:
        changed = False
        should_analyze = (not self._is_metadata_fetch_disabled())
        for entry in self._ordered_batch_entries():
            if not self._is_metadata_fallback_marked_entry(entry):
                continue
            if self._is_metadata_status_protected(entry):
                continue
            if should_analyze:
                entry.status = BatchEntryStatus.VALIDATING.value
                entry.error = ""
                self._start_batch_entry_analysis(entry.entry_id, entry.url_raw)
            else:
                entry.status = BatchEntryStatus.VALID.value
                entry.error = ""
            changed = True
        if not changed:
            return
        self._refresh_batch_entries_view()
        self._mark_batch_queue_dirty()

    @staticmethod
    def _is_metadata_status_protected(entry: BatchEntry) -> bool:
        return str(entry.status or "").strip().lower() in {
            BatchEntryStatus.DOWNLOAD_QUEUED.value,
            BatchEntryStatus.DOWNLOADING.value,
            BatchEntryStatus.PAUSED.value,
            BatchEntryStatus.DONE.value,
            BatchEntryStatus.SKIPPED.value,
            BatchEntryStatus.CANCELLED.value,
        }

    def _entry_has_enriched_metadata(self, entry: BatchEntry | None) -> bool:
        if entry is None:
            return False
        if str(entry.title or "").strip():
            return True
        if entry.expected_size_bytes is not None:
            return True
        if str(entry.thumbnail_url or "").strip():
            return True
        known_formats = {"VIDEO", "AUDIO", "MP4", "MP3"}
        formats = self._dedupe_preserve(entry.available_formats or [])
        if any(item not in known_formats for item in formats):
            return True
        qualities = self._dedupe_preserve(entry.available_qualities or [])
        if len(qualities) > 1:
            return True
        return False

    def _set_single_metadata_disabled_state(self) -> None:
        if self._is_download_running():
            self.window.set_single_url_validation_busy(False)
            return
        if bool(self.window.download_payload().get("batch_enabled")):
            self.window.set_single_url_validation_busy(False)
            return
        current_url = self._first_url_from_input()
        if not current_url:
            self._set_single_analysis_idle_state()
            return
        if not validate_url(current_url):
            self._set_single_analysis_invalid_state("Enter a valid media URL (http/https).")
            return
        self.window.set_single_url_analysis_state(
            "disabled",
            title="Metadata preview disabled",
            size_text="Unknown",
            message="Enable metadata fetching in Settings to load metadata.",
        )

    def _apply_metadata_fetch_policy_to_batch_entries(self) -> None:
        entries = self._ordered_batch_entries()
        if not entries:
            return

        changed = False
        pending_analysis: list[tuple[str, str]] = []
        metadata_disabled = self._is_metadata_fetch_disabled()

        for entry in entries:
            if (not entry.syntax_valid) or self._is_metadata_status_protected(entry):
                continue
            if metadata_disabled:
                if entry.status != BatchEntryStatus.VALID.value or entry.error:
                    entry.status = BatchEntryStatus.VALID.value
                    entry.error = ""
                    changed = True
                if not entry.available_formats:
                    entry.available_formats = ["VIDEO", "AUDIO", "MP4", "MP3"]
                if not entry.available_qualities:
                    entry.available_qualities = ["BEST QUALITY"]
                self._sync_entry_choices_after_analysis(entry)
                continue

            if self._is_metadata_fallback_enabled() and self._is_pre_download_metadata_failure_entry(entry):
                self._apply_metadata_failure_fallback_to_entry(entry)
                changed = True
                continue
            if self._is_metadata_fallback_enabled() and self._is_metadata_fallback_marked_entry(entry):
                continue

            if self._entry_has_enriched_metadata(entry):
                continue
            if entry.status != BatchEntryStatus.VALIDATING.value or entry.error:
                entry.status = BatchEntryStatus.VALIDATING.value
                entry.error = ""
                changed = True
            pending_analysis.append((str(entry.entry_id), str(entry.url_raw)))

        for entry_id, url in pending_analysis:
            self._start_batch_entry_analysis(entry_id, url)

        if changed:
            self._refresh_batch_entries_view()
            self._update_batch_stats_header(entries)
            self._mark_batch_queue_dirty()

    def _apply_metadata_fetch_policy(self) -> None:
        if self._is_metadata_fetch_disabled():
            self._metadata_slow_warning_timer.stop()
            self._metadata_slow_warning_shown = False
            if self._probe_worker:
                self._probe_worker.stop()
            if self._single_analysis_worker:
                self._single_analysis_worker.stop()
            self._single_analysis_timer.stop()
            self._single_analysis_pending_url = ""
            self.window.set_formats_loading(False)
            self.window.set_single_url_validation_busy(False)
            self._stop_batch_analysis_workers()
            self._set_single_metadata_disabled_state()
            self._apply_metadata_fetch_policy_to_batch_entries()
            return

        self._apply_metadata_fallback_policy_to_batch_entries()
        self._apply_metadata_fetch_policy_to_batch_entries()
        if self._is_download_running():
            return
        if bool(self.window.download_payload().get("batch_enabled")):
            self.window.set_single_url_validation_busy(False)
            return
        self._reset_single_url_analysis_for_input_change()
        self._stop_mismatched_active_single_analysis()
        self._schedule_or_reset_pending_single_analysis()

    def _ordered_batch_entries(self) -> list[BatchEntry]:
        entries: list[BatchEntry] = []
        for entry_id in self._batch_entry_order:
            entry = self._batch_entries_by_id.get(entry_id)
            if entry is not None:
                entries.append(entry)
        return entries

    def _recompute_duplicate_promotions(self) -> bool:
        seen_normalized: set[str] = set()
        promote_for_analysis: list[tuple[str, str]] = []
        changed = False
        for entry in self._ordered_batch_entries():
            normalized = str(entry.url_normalized or "").strip()
            if (not entry.syntax_valid) or (not normalized):
                continue
            if normalized in seen_normalized:
                continue
            seen_normalized.add(normalized)
            if not entry.is_duplicate:
                continue
            entry.is_duplicate = False
            changed = True
            if self._entry_has_analysis_metadata(entry):
                entry.status = BatchEntryStatus.VALID.value
                entry.error = ""
            elif entry.status == BatchEntryStatus.INVALID.value:
                entry.status = (
                    BatchEntryStatus.VALID.value
                    if self._is_metadata_fetch_disabled()
                    else BatchEntryStatus.VALIDATING.value
                )
                entry.error = ""
                if not self._is_metadata_fetch_disabled():
                    promote_for_analysis.append((str(entry.entry_id), str(entry.url_raw)))
        for entry_id, url in promote_for_analysis:
            self._start_batch_entry_analysis(entry_id, url)
        if changed:
            self._refresh_batch_entries_view()
            self._mark_batch_queue_dirty()
        return changed

    def _update_batch_stats_header(self, entries: list[BatchEntry] | None = None) -> None:
        source_entries = entries if entries is not None else self._ordered_batch_entries()
        stats = compute_batch_stats(source_entries)
        self.window.set_batch_stats(
            queued=stats.queued,
            downloading=stats.downloading,
            in_progress=stats.in_progress,
            downloaded=stats.downloaded,
            valid=stats.valid,
            invalid=stats.invalid,
            pending=stats.pending,
            duplicates=stats.duplicates,
        )

    def _refresh_batch_entries_view(self) -> None:
        entries = self._ordered_batch_entries()
        self.window.set_batch_entries(entries)
        self._update_batch_stats_header(entries)
        self._schedule_thumbnails_for_entries(entries)

    def _run_thumbnail_cache_maintenance(self) -> None:
        self._thumbnail_flow.maintenance_tick(ttl_seconds=THUMBNAIL_CACHE_ENTRY_TTL_SECONDS)

    def _get_batch_entry_for_thumbnail(self, entry_id: str) -> BatchEntry | None:
        key = str(entry_id or "").strip()
        if not key:
            return None
        return self._batch_entries_by_id.get(key)

    def _schedule_thumbnails_for_entries(self, entries: list[BatchEntry]) -> None:
        self._thumbnail_flow.schedule_entries(entries)

    def _schedule_thumbnail_for_entry(self, entry_id: str) -> None:
        self._thumbnail_flow.schedule_entry_thumbnail(entry_id)

    @staticmethod
    def _is_terminal_batch_state(state: str) -> bool:
        return is_terminal_batch_state(state)

    @staticmethod
    def _dedupe_preserve(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            item = str(value or "").strip().upper()
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    def _current_default_choices(self) -> tuple[str, str]:
        payload = self.window.download_payload()
        fmt = str(payload.get("format_choice") or "VIDEO").strip().upper() or "VIDEO"
        quality = str(payload.get("quality_choice") or "BEST QUALITY").strip().upper() or "BEST QUALITY"
        return fmt, quality

    def _batch_analysis_results(self) -> dict[str, UrlAnalysisResult]:
        mapping = getattr(self, "_batch_analysis_result_by_entry_id", None)
        if isinstance(mapping, dict):
            return mapping
        created: dict[str, UrlAnalysisResult] = {}
        self._batch_analysis_result_by_entry_id = created
        return created

    def _apply_entry_size_for_selection(self, entry: BatchEntry) -> None:
        key = str(entry.entry_id or "").strip()
        if not key:
            return
        analysis = self._batch_analysis_results().get(key)
        if analysis is None:
            return
        selected_size = estimate_selection_size_bytes(
            analysis,
            str(entry.format_choice or "VIDEO"),
            str(entry.quality_choice or "BEST QUALITY"),
        )
        entry.expected_size_bytes = selected_size

    def _entry_has_analysis_metadata(self, entry: BatchEntry | None) -> bool:
        return entry_has_analysis_metadata(entry)

    def _clone_entry_analysis_metadata(self, source: BatchEntry, target: BatchEntry) -> None:
        target.title = str(source.title or "")
        target.expected_size_bytes = source.expected_size_bytes
        target.thumbnail_url = str(source.thumbnail_url or "")
        target.available_formats = self._dedupe_preserve(
            source.available_formats or ["VIDEO", "AUDIO", "MP4", "MP3"]
        )
        target.available_qualities = self._dedupe_preserve(source.available_qualities or ["BEST QUALITY"])
        if "BEST QUALITY" not in target.available_qualities:
            target.available_qualities.insert(0, "BEST QUALITY")
        if str(target.format_choice or "").strip().upper() not in target.available_formats:
            target.format_choice = "VIDEO" if "VIDEO" in target.available_formats else target.available_formats[0]
        if is_audio_format_choice(str(target.format_choice or "").strip().upper()):
            target.quality_choice = "BEST QUALITY"
        elif str(target.quality_choice or "").strip().upper() not in target.available_qualities:
            target.quality_choice = "BEST QUALITY"
        source_key = str(source.entry_id or "").strip()
        target_key = str(target.entry_id or "").strip()
        results_by_entry = self._batch_analysis_results()
        if source_key and target_key and source_key in results_by_entry:
            results_by_entry[target_key] = results_by_entry[source_key]
            self._apply_entry_size_for_selection(target)
        target.status = BatchEntryStatus.VALID.value
        target.error = ""

    @staticmethod
    def _entry_download_signature(entry: BatchEntry) -> tuple[str, str, str]:
        return build_download_signature(
            url_normalized=str(entry.url_normalized or "").strip(),
            url_raw=str(entry.url_raw or "").strip(),
            format_choice=str(entry.format_choice or "VIDEO").strip().upper() or "VIDEO",
            quality_choice=str(entry.quality_choice or "BEST QUALITY").strip().upper() or "BEST QUALITY",
        )

    def _collect_ready_deduped_entries(
        self,
        entries: list[BatchEntry],
        *,
        require_unassigned: bool = False,
    ) -> tuple[list[BatchEntry], int]:
        active_entry_ids = set(self._batch_entry_to_active_job_id) if require_unassigned else set()
        return collect_ready_deduped_entries(
            entries,
            active_entry_ids=active_entry_ids,
            require_unassigned=require_unassigned,
            signature_builder=self._entry_download_signature,
        )

    @staticmethod
    def _is_entry_eligible_for_active_enqueue(entry: BatchEntry) -> bool:
        return is_entry_eligible_for_active_enqueue(entry)

    def _on_multi_add_url(self, value: str) -> None:
        self._add_batch_urls([value])

    def _on_multi_bulk_add(self, text: str) -> None:
        lines = list(iter_non_empty_lines(text))
        if not lines:
            return
        self._add_batch_urls(lines)

    def _existing_batch_entries_by_normalized(self) -> dict[str, BatchEntry]:
        existing_by_normalized: dict[str, BatchEntry] = {}
        for entry in self._ordered_batch_entries():
            normalized = str(entry.url_normalized or "").strip()
            if normalized and normalized not in existing_by_normalized:
                existing_by_normalized[normalized] = entry
        return existing_by_normalized

    def _build_batch_entry_from_url(
        self,
        *,
        url_raw: str,
        default_format: str,
        default_quality: str,
        parent_entry: BatchEntry | None,
    ) -> BatchEntry:
        entry_id = uuid.uuid4().hex[:10]
        syntax_valid = validate_url(url_raw)
        normalized = normalize_batch_url(url_raw) if syntax_valid else ""
        is_duplicate = bool(parent_entry)
        status = (
            BatchEntryStatus.INVALID.value
            if not syntax_valid
            else BatchEntryStatus.VALID.value if self._is_metadata_fetch_disabled() else BatchEntryStatus.VALIDATING.value
        )
        error = "" if syntax_valid else "Invalid URL"
        entry = BatchEntry(
            entry_id=entry_id,
            url_raw=url_raw,
            url_normalized=normalized,
            syntax_valid=syntax_valid,
            status=status,
            format_choice=default_format,
            quality_choice=default_quality if not is_audio_format_choice(default_format) else "BEST QUALITY",
            is_duplicate=is_duplicate,
            error=error,
            thumbnail_url="",
            available_formats=["VIDEO", "AUDIO", "MP4", "MP3"],
            available_qualities=["BEST QUALITY"],
        )
        if syntax_valid and is_duplicate and self._entry_has_analysis_metadata(parent_entry):
            self._clone_entry_analysis_metadata(parent_entry, entry)
        return entry

    def _add_entry_to_batch_state(self, entry: BatchEntry) -> None:
        key = str(entry.entry_id or "").strip()
        if not key:
            return
        self._batch_entries_by_id[key] = entry
        self._batch_entry_order.append(key)

    def _maybe_start_entry_analysis(self, entry: BatchEntry) -> None:
        if self._is_metadata_fetch_disabled():
            return
        if entry.status == BatchEntryStatus.VALIDATING.value:
            self._start_batch_entry_analysis(entry.entry_id, entry.url_raw)

    def _add_batch_urls(self, urls: list[str]) -> None:
        default_format, default_quality = self._current_default_choices()
        existing_by_normalized = self._existing_batch_entries_by_normalized()
        added = 0
        for raw_url in urls:
            url_raw = str(raw_url or "").strip()
            if not url_raw:
                continue
            syntax_valid = validate_url(url_raw)
            normalized = normalize_batch_url(url_raw) if syntax_valid else ""
            parent_entry = existing_by_normalized.get(normalized) if normalized else None
            entry = self._build_batch_entry_from_url(
                url_raw=url_raw,
                default_format=default_format,
                default_quality=default_quality,
                parent_entry=parent_entry,
            )
            self._add_entry_to_batch_state(entry)
            added += 1
            if normalized and normalized not in existing_by_normalized:
                existing_by_normalized[normalized] = entry
            self._maybe_start_entry_analysis(entry)
        if added:
            self._refresh_batch_entries_view()
            self._mark_batch_queue_dirty()

    def _start_batch_entry_analysis(self, entry_id: str, url: str) -> None:
        key = str(entry_id or "").strip()
        target_url = str(url or "").strip()
        if (not key) or (not target_url):
            return
        if self._is_metadata_fetch_disabled():
            entry = self._batch_entries_by_id.get(key)
            if entry is not None and entry.syntax_valid:
                entry.status = BatchEntryStatus.VALID.value
                entry.error = ""
                self._sync_entry_choices_after_analysis(entry)
            return
        if key in self._batch_analysis_threads or key in self._batch_analysis_queued_entry_ids:
            return
        self._batch_analysis_queue.append((key, target_url))
        self._batch_analysis_queued_entry_ids.add(key)
        self._pump_batch_analysis_queue()

    def _pump_batch_analysis_queue(self) -> None:
        if self._is_metadata_fetch_disabled():
            self._batch_analysis_queue.clear()
            self._batch_analysis_queued_entry_ids.clear()
            self._stop_metadata_slow_warning_watch_if_idle()
            return
        self._start_metadata_slow_warning_watch()
        while len(self._batch_analysis_threads) < self._batch_analysis_max_workers and self._batch_analysis_queue:
            entry_id, url = self._batch_analysis_queue.popleft()
            self._batch_analysis_queued_entry_ids.discard(entry_id)
            if entry_id in self._batch_analysis_threads:
                continue
            if entry_id not in self._batch_entries_by_id:
                continue
            thread = QThread(self)
            worker = BatchAnalyzeWorker(
                self.download_service,
                entry_id,
                url,
                timeout_seconds=self._metadata_fetch_timeout_seconds(),
            )
            worker.moveToThread(thread)
            thread.setProperty("entry_id", entry_id)
            thread.started.connect(worker.run)
            worker.finishedSummary.connect(self._on_batch_analysis_summary, Qt.ConnectionType.QueuedConnection)
            worker.errorRaised.connect(self._on_batch_analysis_error, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._on_batch_analysis_thread_finished, Qt.ConnectionType.QueuedConnection)
            self._batch_analysis_threads[entry_id] = thread
            self._batch_analysis_workers[entry_id] = worker
            thread.start()
            self._start_metadata_slow_warning_watch()

    def _apply_analysis_result_to_entry(self, entry: BatchEntry, result: UrlAnalysisResult) -> None:
        entry_key = str(entry.entry_id or "").strip()
        if entry_key:
            self._batch_analysis_results()[entry_key] = result
        entry.url_normalized = str(result.url_normalized or entry.url_normalized)
        entry.title = str(result.title or "")
        entry.thumbnail_url = str(result.thumbnail_url or "")
        entry.expected_size_bytes = result.expected_size_bytes
        entry.available_formats = self._dedupe_preserve(result.formats or entry.available_formats or ["VIDEO", "AUDIO", "MP4", "MP3"])
        entry.available_qualities = self._dedupe_preserve(result.qualities or ["BEST QUALITY"])
        if "BEST QUALITY" not in entry.available_qualities:
            entry.available_qualities.insert(0, "BEST QUALITY")

        if result.is_valid and not result.error:
            entry.status = BatchEntryStatus.VALID.value
            entry.error = ""
        else:
            reason = str(result.error or "Unable to analyze URL").strip()
            if entry.syntax_valid and self._is_metadata_fallback_enabled():
                self._apply_metadata_failure_fallback_to_entry(entry, reason=reason)
            else:
                entry.status = BatchEntryStatus.FAILED.value
                entry.error = reason

    def _sync_entry_choices_after_analysis(self, entry: BatchEntry) -> None:
        if entry.format_choice not in entry.available_formats:
            entry.format_choice = "VIDEO" if "VIDEO" in entry.available_formats else entry.available_formats[0]
        if is_audio_format_choice(entry.format_choice):
            entry.quality_choice = "BEST QUALITY"
        elif entry.quality_choice not in entry.available_qualities:
            entry.quality_choice = "BEST QUALITY"
        self._apply_entry_size_for_selection(entry)

    def _maybe_auto_start_ready_entry(self, entry: BatchEntry) -> None:
        if entry.status != BatchEntryStatus.VALID.value or (not self.config.auto_start_ready_links):
            return
        if self._is_download_running() and self._active_download_is_multi:
            self._enqueue_entries_into_active_download([entry], source_label="auto-queue")
        elif (not self._is_download_running()) and bool(self.window.download_payload().get("batch_enabled")):
            self._start_download_for_entries([entry], source_label="auto-start")

    def _on_batch_analysis_summary(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        entry_id, result = payload
        key = str(entry_id or "").strip()
        if not key:
            return
        entry = self._batch_entries_by_id.get(key)
        if entry is None:
            return
        if not isinstance(result, UrlAnalysisResult):
            entry.status = BatchEntryStatus.FAILED.value
            entry.error = "Metadata analysis failed"
            self._refresh_batch_entries_view()
            return

        self._apply_analysis_result_to_entry(entry, result)
        self._sync_entry_choices_after_analysis(entry)
        self._maybe_auto_start_ready_entry(entry)
        self.window.update_batch_entry(entry)
        self._update_batch_stats_header()
        self._schedule_thumbnail_for_entry(key)
        self._mark_batch_queue_dirty()

    def _on_batch_analysis_error(self, entry_id: str, error: str) -> None:
        key = str(entry_id or "").strip()
        entry = self._batch_entries_by_id.get(key)
        if entry is None:
            return
        reason = str(error or "Analysis failed").strip()
        if entry.syntax_valid and self._is_metadata_fallback_enabled():
            self._apply_metadata_failure_fallback_to_entry(entry, reason=reason)
            self._maybe_auto_start_ready_entry(entry)
        else:
            entry.status = BatchEntryStatus.FAILED.value
            entry.error = reason
        self.window.update_batch_entry(entry)
        self._update_batch_stats_header()
        self._mark_batch_queue_dirty()

    def _on_batch_analysis_finished(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        self._batch_analysis_threads.pop(key, None)
        self._batch_analysis_workers.pop(key, None)
        self._stop_metadata_slow_warning_watch_if_idle()
        self._pump_batch_analysis_queue()

    def _on_batch_analysis_thread_finished(self) -> None:
        sender = self.sender()
        entry_id = ""
        if isinstance(sender, QThread):
            entry_id = str(sender.property("entry_id") or "").strip()
        if entry_id:
            self._on_batch_analysis_finished(entry_id)

    def _on_multi_entry_format_changed(self, entry_id: str, value: str) -> None:
        entry = self._batch_entries_by_id.get(str(entry_id or "").strip())
        if entry is None:
            return
        selected = str(value or "").strip().upper() or "VIDEO"
        entry.format_choice = selected
        if is_audio_format_choice(selected):
            entry.quality_choice = "BEST QUALITY"
        self._apply_entry_size_for_selection(entry)
        self.window.update_batch_entry(entry)
        self._mark_batch_queue_dirty()

    def _on_multi_entry_quality_changed(self, entry_id: str, value: str) -> None:
        entry = self._batch_entries_by_id.get(str(entry_id or "").strip())
        if entry is None:
            return
        entry.quality_choice = str(value or "").strip().upper() or "BEST QUALITY"
        self._apply_entry_size_for_selection(entry)
        self.window.update_batch_entry(entry)
        self._mark_batch_queue_dirty()

    def _on_multi_remove_entry(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        entry = self._batch_entries_by_id.get(key)
        if entry is None:
            return
        active_job_id = self._active_job_id_for_entry(key)
        if active_job_id and self._download_worker is not None:
            self._download_worker.stop_job(active_job_id)
        self._batch_entry_to_active_job_id.pop(key, None)
        if active_job_id:
            self._post_processing_notice_job_ids.discard(active_job_id)
            self._job_to_batch_entry_id.pop(active_job_id, None)
            self._download_progress_by_job.pop(active_job_id, None)
            self._download_attempts_by_job.pop(active_job_id, None)
            existing_state = normalize_download_state(self._download_state_by_job.get(active_job_id, ""))
            self._download_state_by_job.pop(active_job_id, None)
            self._download_url_by_job.pop(active_job_id, None)
            if self._active_download_is_multi:
                if existing_state in {DownloadState.DONE.value, DownloadState.SKIPPED.value}:
                    self._download_completed_jobs = max(0, int(self._download_completed_jobs) - 1)
                self._download_total_jobs = max(0, int(self._download_total_jobs) - 1)
                self._refresh_overall_download_progress()
        self._batch_entries_by_id.pop(key, None)
        self._batch_analysis_results().pop(key, None)
        self._batch_entry_order = [item for item in self._batch_entry_order if item != key]
        self._batch_analysis_queued_entry_ids.discard(key)
        self._batch_analysis_queue = deque(
            (entry_id, url) for entry_id, url in self._batch_analysis_queue if entry_id != key
        )
        worker = self._batch_analysis_workers.get(key)
        if worker:
            worker.stop()
        self._thumbnail_flow.clear_entry_waiter(key)
        if not self._recompute_duplicate_promotions():
            self._refresh_batch_entries_view()
        self._mark_batch_queue_dirty()

    def _on_multi_export_urls(self, output_path: str) -> None:
        target = Path(str(output_path or "").strip()).expanduser()
        if not target.name:
            self._show_warning("Export URLs", "Please choose a valid output file path.")
            return

        urls: list[str] = []
        for entry in self._ordered_batch_entries():
            value = str(entry.url_raw or "").strip()
            if not value:
                continue
            urls.append(value)

        if not urls:
            self._show_info("Export URLs", "There are no URLs to export.")
            return

        if target.suffix == "":
            target = target.with_suffix(".txt")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(urls) + "\n", encoding="utf-8")
        except OSError as exc:
            self._show_warning("Export URLs", f"Could not write file:\n{exc}")
            return
        self.window.append_log(f"Exported {len(urls)} URL(s) to {target}")

    def _on_multi_start_entry(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        entry = self._batch_entries_by_id.get(key)
        if entry is None:
            return
        if entry.status == BatchEntryStatus.VALIDATING.value:
            self._show_info("Entry still validating", "Please wait until URL analysis finishes.")
            return
        if not entry.syntax_valid:
            self._show_warning("Entry not ready", "Only valid rows can be downloaded.")
            return
        if entry.status in {BatchEntryStatus.INVALID.value, BatchEntryStatus.DOWNLOADING.value, BatchEntryStatus.DOWNLOAD_QUEUED.value}:
            self._show_warning("Entry not ready", "This row is not currently eligible for download.")
            return
        if self._is_download_running():
            if not self._active_download_is_multi:
                self._show_info("Download in progress", "Wait for the current download to finish or stop it first.")
                return
            added = self._enqueue_entries_into_active_download([entry], source_label="row")
            if added <= 0:
                self._show_info("Entry already queued", "This row is already active or could not be queued.")
            return
        self._start_download_for_entries([entry], source_label="row")

    def _active_job_id_for_entry(self, entry_id: str) -> str:
        return resolve_active_job_id_for_entry(self._batch_entry_to_active_job_id, entry_id)

    def _active_single_job_id(self) -> str:
        return resolve_active_single_job_id(
            self._download_state_by_job,
            active_download_is_multi=self._active_download_is_multi,
            normalize_download_state=normalize_download_state,
            terminal_states=TERMINAL_DOWNLOAD_STATES,
        )

    def _active_multi_job_ids(self) -> list[str]:
        return resolve_active_multi_job_ids(
            self._download_state_by_job,
            active_download_is_multi=self._active_download_is_multi,
            normalize_download_state=normalize_download_state,
            terminal_states=TERMINAL_DOWNLOAD_STATES,
        )

    def _refresh_multi_pause_resume_ui(self) -> None:
        if (not self._is_download_running()) or (not self._active_download_is_multi):
            self.window.set_multi_pause_resume_state(paused=False, enabled=False)
            return
        active_job_ids = self._active_multi_job_ids()
        if not active_job_ids:
            self.window.set_multi_pause_resume_state(paused=False, enabled=False)
            return
        paused_state = DownloadState.PAUSED.value
        all_paused = all_jobs_paused(
            state_by_job=self._download_state_by_job,
            active_job_ids=active_job_ids,
            paused_state=paused_state,
        )
        self.window.set_multi_pause_resume_state(paused=all_paused, enabled=True)

    def _refresh_single_pause_resume_ui(self) -> None:
        if (not self._is_download_running()):
            if self._active_download_is_multi or bool(self.window.download_payload().get("batch_enabled")):
                self.window.set_multi_pause_resume_state(paused=False, enabled=False)
            else:
                self.window.set_single_pause_resume_state(paused=False, enabled=False)
            return
        if self._active_download_is_multi:
            self._refresh_multi_pause_resume_ui()
            return
        job_id = self._active_single_job_id()
        if not job_id:
            self.window.set_single_pause_resume_state(paused=False, enabled=False)
            return
        current_state = normalize_download_state(self._download_state_by_job.get(job_id, ""))
        self.window.set_single_pause_resume_state(paused=(current_state == DownloadState.PAUSED.value), enabled=True)

    def _on_single_pause_resume_requested(self) -> None:
        if (not self._is_download_running()) or (self._download_worker is None):
            if self._active_download_is_multi or bool(self.window.download_payload().get("batch_enabled")):
                self.window.set_multi_pause_resume_state(paused=False, enabled=False)
            else:
                self.window.set_single_pause_resume_state(paused=False, enabled=False)
            return
        if self._active_download_is_multi:
            self._on_multi_pause_resume_all_requested()
            return
        job_id = self._active_single_job_id()
        if not job_id:
            self.window.set_single_pause_resume_state(paused=False, enabled=False)
            return
        state = normalize_download_state(self._download_state_by_job.get(job_id, ""))
        if state in TERMINAL_DOWNLOAD_STATES:
            self.window.set_single_pause_resume_state(paused=False, enabled=False)
            return
        if state == DownloadState.PAUSED.value:
            self._download_worker.resume_job(job_id)
            self._download_state_by_job[job_id] = DownloadState.QUEUED.value
            self.window.set_single_pause_resume_state(paused=False, enabled=True)
            return
        self._download_worker.pause_job(job_id)
        self._download_state_by_job[job_id] = DownloadState.PAUSED.value
        self.window.set_single_pause_resume_state(paused=True, enabled=True)

    def _on_multi_pause_resume_all_requested(self) -> None:
        if (not self._is_download_running()) or (not self._active_download_is_multi) or (self._download_worker is None):
            self.window.set_multi_pause_resume_state(paused=False, enabled=False)
            return
        active_job_ids = self._active_multi_job_ids()
        if not active_job_ids:
            self.window.set_multi_pause_resume_state(paused=False, enabled=False)
            return
        paused_value = DownloadState.PAUSED.value
        all_paused, to_resume, to_pause = partition_multi_pause_actions(
            state_by_job=self._download_state_by_job,
            active_job_ids=active_job_ids,
            paused_state=paused_value,
        )
        for job_id in to_resume:
            self._download_worker.resume_job(job_id)
            self._download_state_by_job[job_id] = DownloadState.QUEUED.value
        for job_id in to_pause:
            self._download_worker.pause_job(job_id)
            self._download_state_by_job[job_id] = paused_value
        for entry_id, active_job_id in self._batch_entry_to_active_job_id.items():
            if active_job_id not in active_job_ids:
                continue
            entry = self._batch_entries_by_id.get(entry_id)
            if entry is None:
                continue
            entry.status = (
                BatchEntryStatus.DOWNLOAD_QUEUED.value
                if all_paused
                else BatchEntryStatus.PAUSED.value
            )
            self.window.update_batch_entry(entry)
        self._update_batch_stats_header()
        self._mark_batch_queue_dirty()
        self._refresh_multi_pause_resume_ui()

    def _on_multi_pause_entry(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        if (not key) or (not self._is_download_running()) or (self._download_worker is None):
            return
        job_id = self._active_job_id_for_entry(key)
        if not job_id:
            return
        self._download_worker.pause_job(job_id)
        self._download_state_by_job[job_id] = DownloadState.PAUSED.value
        entry = self._batch_entries_by_id.get(key)
        if entry is not None:
            entry.status = BatchEntryStatus.PAUSED.value
            self.window.update_batch_entry(entry)
            self._mark_batch_queue_dirty()
        self._refresh_multi_pause_resume_ui()

    def _on_multi_resume_entry(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        if (not key) or (not self._is_download_running()) or (self._download_worker is None):
            return
        job_id = self._active_job_id_for_entry(key)
        if not job_id:
            return
        self._download_worker.resume_job(job_id)
        self._download_state_by_job[job_id] = DownloadState.QUEUED.value
        entry = self._batch_entries_by_id.get(key)
        if entry is not None:
            entry.status = BatchEntryStatus.DOWNLOAD_QUEUED.value
            self.window.update_batch_entry(entry)
            self._mark_batch_queue_dirty()
        self._refresh_multi_pause_resume_ui()

    def _collect_start_all_candidates(self) -> tuple[list[BatchEntry], int, int, int]:
        entries = self._ordered_batch_entries()
        valid_rows, skipped_same_signature = self._collect_ready_deduped_entries(entries)
        skipped_count, pending_count = collect_start_all_counts(entries, selected_count=len(valid_rows))
        return valid_rows, skipped_count, pending_count, skipped_same_signature

    def _log_start_all_skip_context(
        self,
        *,
        started_count: int,
        skipped_count: int,
        pending_count: int,
        skipped_same_signature: int,
        queued_into_active: bool,
    ) -> None:
        message = build_start_all_skip_log_message(
            started_count=started_count,
            skipped_count=skipped_count,
            pending_count=pending_count,
            skipped_same_signature=skipped_same_signature,
            queued_into_active=queued_into_active,
        )
        if not message:
            return
        self.window.append_log(message)

    def _on_multi_start_all(self) -> None:
        entries = self._ordered_batch_entries()
        if not entries:
            self.window.append_log("No links yet. Add one above or use Bulk paste.")
            return
        valid_rows, skipped_count, pending_count, skipped_same_signature = self._collect_start_all_candidates()
        if not valid_rows:
            self.window.append_log("No validated rows are ready to download yet.")
            return
        if self._is_download_running():
            if not self._active_download_is_multi:
                self._show_info("Download in progress", "Wait for the current download to finish or stop it first.")
                return
            added = self._enqueue_entries_into_active_download(valid_rows, source_label="start-all")
            if added <= 0:
                self._show_info("Nothing queued", "All valid rows are already queued/downloading.")
                return
            self._log_start_all_skip_context(
                started_count=added,
                skipped_count=skipped_count,
                pending_count=pending_count,
                skipped_same_signature=skipped_same_signature,
                queued_into_active=True,
            )
            return
        self._log_start_all_skip_context(
            started_count=len(valid_rows),
            skipped_count=skipped_count,
            pending_count=pending_count,
            skipped_same_signature=skipped_same_signature,
            queued_into_active=False,
        )
        self._start_download_for_entries(valid_rows, source_label="start-all")

    def _build_download_job_for_entry(self, entry: BatchEntry, *, output_dir: str) -> DownloadJob:
        format_choice = str(entry.format_choice or "VIDEO").strip().upper() or "VIDEO"
        quality_choice = str(entry.quality_choice or "BEST QUALITY").strip().upper() or "BEST QUALITY"
        if is_audio_format_choice(format_choice):
            quality_choice = "BEST QUALITY"
        return DownloadJob(
            job_id=uuid.uuid4().hex[:10],
            url=coerce_http_url(str(entry.url_raw)),
            format_choice=format_choice,
            quality_choice=quality_choice,
            output_dir=output_dir,
        )

    @staticmethod
    def _mark_entry_as_download_queued(entry: BatchEntry) -> None:
        entry.status = BatchEntryStatus.DOWNLOAD_QUEUED.value
        entry.progress_percent = 0.0
        entry.attempts = 0
        entry.error = ""

    def _resolve_download_output_dir(self, payload: dict[str, object]) -> str:
        return str(payload.get("download_location") or self.config.download_location)

    def _register_enqueued_job_state(self, *, job: DownloadJob, entry_id: str) -> None:
        self._job_to_batch_entry_id[job.job_id] = entry_id
        self._batch_entry_to_active_job_id[entry_id] = job.job_id
        self._download_progress_by_job[job.job_id] = 0.0
        self._download_attempts_by_job[job.job_id] = 0
        self._download_state_by_job[job.job_id] = DownloadState.QUEUED.value
        self._download_url_by_job[job.job_id] = job.url
        self._download_total_jobs += 1

    def _try_enqueue_entry_into_active_download(self, *, entry: BatchEntry, output_dir: str) -> bool:
        if self._download_worker is None:
            return False
        entry_id = str(entry.entry_id or "").strip()
        if (not entry_id) or (entry_id in self._batch_entry_to_active_job_id):
            return False
        if not self._is_entry_eligible_for_active_enqueue(entry):
            return False
        job = self._build_download_job_for_entry(entry, output_dir=output_dir)
        if not self._download_worker.enqueue_job(job):
            return False
        self._register_enqueued_job_state(job=job, entry_id=entry_id)
        self._mark_entry_as_download_queued(entry)
        self.window.update_batch_entry(entry)
        return True

    def _enqueue_entries_into_active_download(self, entries: list[BatchEntry], *, source_label: str) -> int:
        if (not entries) or (not self._is_download_running()) or (not self._active_download_is_multi):
            return 0
        if self._download_worker is None:
            return 0

        payload = self.window.download_payload()
        output_dir = self._resolve_download_output_dir(payload)
        added = 0
        for entry in entries:
            if self._try_enqueue_entry_into_active_download(entry=entry, output_dir=output_dir):
                added += 1

        if added > 0:
            if source_label != "auto-queue":
                self.window.append_log(f"Queued {added} additional row(s).")
            self._update_batch_stats_header()
            self._refresh_overall_download_progress()
            self._mark_batch_queue_dirty()
        return added

    def _start_download_for_entries(self, entries: list[BatchEntry], *, source_label: str) -> None:
        if not entries:
            return
        if self._is_download_running() and self._active_download_is_multi:
            self._enqueue_entries_into_active_download(entries, source_label=source_label)
            return
        payload = self.window.download_payload()
        output_dir = self._resolve_download_output_dir(payload)
        jobs: list[DownloadJob] = []
        job_to_entry: dict[str, str] = {}
        for entry in entries:
            job = self._build_download_job_for_entry(entry, output_dir=output_dir)
            jobs.append(job)
            job_id = job.job_id
            job_to_entry[job_id] = str(entry.entry_id)
            self._mark_entry_as_download_queued(entry)

        self._refresh_batch_entries_view()
        self._mark_batch_queue_dirty()
        if source_label == "start-all":
            self.window.append_log(f"Queued {len(jobs)} row(s) for batch download.")
        self._start_download_worker(
            jobs,
            payload=payload,
            job_to_entry=job_to_entry,
        )

    def _initialize_download_runtime(
        self,
        *,
        jobs: list[DownloadJob],
        payload: dict[str, object],
        job_to_entry: dict[str, str] | None,
    ) -> None:
        active_multi = bool(payload.get("batch_enabled", False) or bool(job_to_entry))
        self._download_runtime.initialize_jobs(
            [job.job_id for job in jobs],
            {job.job_id: job.url for job in jobs},
            active_multi=active_multi,
            job_to_entry=job_to_entry,
        )
        self._post_processing_notice_job_ids.clear()
        self.window.reset_download_progress()
        self._refresh_overall_download_progress()
        self.window.set_controls_busy(True)
        self._refresh_single_pause_resume_ui()

    def _resolve_download_worker_runtime_settings(
        self,
        *,
        payload: dict[str, object],
        job_count: int,
    ) -> tuple[int, int]:
        requested_concurrency = int(payload.get("batch_concurrency", self.config.batch_concurrency))
        speed_limit_kbps = max(
            0,
            min(
                SPEED_LIMIT_KBPS_MAX,
                int(payload.get("speed_limit_kbps", self.config.download_speed_limit_kbps)),
            ),
        )
        adaptive_enabled = bool(
            payload.get("adaptive_batch_concurrency", self.config.adaptive_batch_concurrency)
        )
        effective_concurrency = self._effective_batch_concurrency(
            requested_concurrency,
            job_count,
            speed_limit_kbps=speed_limit_kbps,
            adaptive_enabled=adaptive_enabled,
        )
        if effective_concurrency != requested_concurrency and job_count > 1:
            self.window.append_log(
                f"Adaptive concurrency: {requested_concurrency} -> {effective_concurrency}"
            )
        return effective_concurrency, speed_limit_kbps

    def _create_download_worker(
        self,
        *,
        jobs: list[DownloadJob],
        payload: dict[str, object],
        effective_concurrency: int,
        speed_limit_kbps: int,
    ) -> DownloadWorker:
        return DownloadWorker(
            self.download_service,
            jobs,
            effective_concurrency,
            retry_count=int(payload.get("batch_retry_count", self.config.batch_retry_count)),
            retry_profile=str(payload.get("retry_profile", self.config.retry_profile or RetryProfile.BASIC.value)),
            skip_existing_files=bool(payload.get("skip_existing_files", self.config.skip_existing_files)),
            filename_template=str(payload.get("filename_template", self.config.filename_template)),
            conflict_policy=str(payload.get("conflict_policy", self.config.conflict_policy)),
            speed_limit_kbps=speed_limit_kbps,
        )

    def _connect_download_worker(self, *, thread: QThread, worker: DownloadWorker) -> None:
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressChanged.connect(self._on_download_progress, Qt.ConnectionType.QueuedConnection)
        worker.statusChanged.connect(self._on_download_status, Qt.ConnectionType.QueuedConnection)
        worker.logChanged.connect(self._on_download_log, Qt.ConnectionType.QueuedConnection)
        worker.errorRaised.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.finishedSummary.connect(self._on_download_summary, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_download_finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)

    def _start_download_worker(
        self,
        jobs: list[DownloadJob],
        *,
        payload: dict[str, object],
        job_to_entry: dict[str, str] | None = None,
    ) -> None:
        if not jobs:
            return
        self._initialize_download_runtime(jobs=jobs, payload=payload, job_to_entry=job_to_entry)
        effective_concurrency, speed_limit_kbps = self._resolve_download_worker_runtime_settings(
            payload=payload,
            job_count=len(jobs),
        )
        thread = QThread(self)
        worker = self._create_download_worker(
            jobs=jobs,
            payload=payload,
            effective_concurrency=effective_concurrency,
            speed_limit_kbps=speed_limit_kbps,
        )
        self._connect_download_worker(thread=thread, worker=worker)
        thread.start()

        self._download_thread = thread
        self._download_worker = worker

    def _open_official_page(self) -> None:
        webbrowser.open(OFFICIAL_PAGE_URL)

    def _on_manual_update_check(self) -> None:
        self.start_update_check(manual=True)

    @staticmethod
    def _split_analysis_formats(formats: list[str]) -> tuple[list[str], list[str]]:
        base_order = ["VIDEO", "AUDIO", "MP4", "MP3"]
        normalized: list[str] = []
        seen: set[str] = set()
        for value in formats:
            item = str(value or "").strip().upper()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        if not normalized:
            normalized = list(base_order)
        base_formats = [item for item in base_order if item in normalized]
        if not base_formats:
            base_formats = list(base_order)
        extra_formats = [item for item in normalized if item not in base_formats]
        return base_formats, extra_formats

    @staticmethod
    def _normalize_quality_items(qualities: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in qualities:
            item = str(value or "").strip().upper()
            if not item or item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        if "BEST QUALITY" not in normalized:
            normalized.insert(0, "BEST QUALITY")
        return normalized

    def _cached_single_result_for_url(self, url: str) -> UrlAnalysisResult | None:
        normalized = normalize_batch_url(str(url or "").strip())
        if not normalized:
            return None
        return self._single_analysis_cache.get(normalized)

    @staticmethod
    def _format_duration_label(duration_seconds: int | None) -> str:
        if duration_seconds is None:
            return "Unknown"
        total = max(0, int(duration_seconds))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @staticmethod
    def _format_source_label(source_label: str, fallback_url: str) -> str:
        source = str(source_label or "").strip()
        if source:
            return source
        candidate = str(fallback_url or "").strip()
        if not candidate:
            return "Unknown"
        try:
            parsed = urlparse(candidate)
        except Exception:
            return "Unknown"
        host = str(parsed.netloc or "").strip().lower()
        if not host:
            return "Unknown"
        if host.startswith("www."):
            host = host[4:]
        return host or "Unknown"

    def _selected_size_text_for_result(self, result: UrlAnalysisResult, *, format_choice: str, quality_choice: str) -> str:
        estimated_size = estimate_selection_size_bytes(result, format_choice, quality_choice)
        return format_size_human(estimated_size)

    def _apply_single_ready_state_from_result(self, url: str, result: UrlAnalysisResult) -> None:
        title = str(result.title or "").strip() or "Untitled media"
        selected_format, selected_quality = self._current_default_choices()
        size_text = self._selected_size_text_for_result(
            result,
            format_choice=selected_format,
            quality_choice=selected_quality,
        )
        duration_text = self._format_duration_label(result.duration_seconds)
        source_text = self._format_source_label(result.source_label, str(result.url_normalized or url))
        self.window.set_single_url_analysis_state(
            "ready",
            title=title,
            size_text=size_text,
            message=f"Duration: {duration_text}\nSource: {source_text}",
        )
        self.window.set_single_url_validation_busy(False)

    def _refresh_single_selection_size_from_cache(self) -> None:
        if self._is_metadata_fetch_disabled():
            return
        if bool(self.window.download_payload().get("batch_enabled")):
            return
        first_url = self._first_url_from_input()
        cached = self._cached_single_result_for_url(first_url)
        if cached is None or (not cached.is_valid) or bool(cached.error):
            return
        self._apply_single_ready_state_from_result(first_url, cached)

    def _apply_single_analysis_result(self, url: str, result: UrlAnalysisResult) -> None:
        current_url = self._first_url_from_input()
        if current_url != url:
            return

        base_formats, other_formats = self._split_analysis_formats(result.formats)
        qualities = self._normalize_quality_items(result.qualities or ["BEST QUALITY"])
        self.window.set_formats_and_qualities(
            base_formats,
            qualities,
            other_formats=other_formats,
            reveal_all_formats=False,
        )

        if result.is_valid and (not result.error):
            self._apply_single_ready_state_from_result(url, result)
            self._last_probe_url = url
            self._schedule_single_thumbnail(str(result.thumbnail_url or "").strip())
            return

        error_text = str(result.error or "").strip() or "Could not analyze this URL."
        self.window.set_single_url_analysis_state(
            "invalid",
            title="Invalid URL",
            size_text="Unknown",
            message=error_text,
        )
        self.window.set_single_url_thumbnail(None, "")
        self.window.set_single_url_validation_busy(False)

    def _schedule_single_thumbnail(self, thumbnail_url: str) -> None:
        self._thumbnail_flow.schedule_single_thumbnail(thumbnail_url)

    def _expected_single_thumbnail_url(self) -> str:
        current_url = self._first_url_from_input()
        if not current_url:
            return ""
        cached = self._cached_single_result_for_url(current_url)
        if cached is None:
            return ""
        return str(cached.thumbnail_url or "").strip()

    def _set_single_analysis_idle_state(self) -> None:
        self.window.set_single_url_validation_busy(False)
        self.window.set_single_url_analysis_state("idle")
        self.window.set_single_url_thumbnail(None, "")

    def _set_single_analysis_invalid_state(self, message: str) -> None:
        self.window.set_single_url_analysis_state(
            "invalid",
            title="Invalid URL",
            size_text="Unknown",
            message=message,
        )
        self.window.set_single_url_thumbnail(None, "")
        self.window.set_single_url_validation_busy(False)

    def _set_single_analysis_validating_state(self, url: str) -> None:
        self.window.set_single_url_validation_busy(True)
        self.window.set_single_url_analysis_state(
            "validating",
            title=url,
            size_text="Unknown",
            message="Validating link, loading formats and quality...",
        )

    def _start_single_analysis_worker(self, url: str) -> None:
        thread = QThread(self)
        worker = SingleAnalyzeWorker(
            self.download_service,
            url,
            timeout_seconds=self._metadata_fetch_timeout_seconds(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finishedSummary.connect(self._on_single_analysis_summary, Qt.ConnectionType.QueuedConnection)
        worker.errorRaised.connect(self._on_single_analysis_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_single_analysis_finished, Qt.ConnectionType.QueuedConnection)
        self._single_analysis_active_url = url
        self._single_analysis_thread = thread
        self._single_analysis_worker = worker
        thread.start()
        self._start_metadata_slow_warning_watch()

    @staticmethod
    def _coerce_single_analysis_result(url: str, result: object) -> UrlAnalysisResult:
        normalized_url = str(url or "").strip()
        if isinstance(result, UrlAnalysisResult):
            return result
        return UrlAnalysisResult(
            url_raw=normalized_url,
            url_normalized=normalize_batch_url(normalized_url),
            is_valid=False,
            formats=["VIDEO", "AUDIO", "MP4", "MP3"],
            qualities=["BEST QUALITY"],
            error="Metadata analysis failed",
        )

    def _cache_single_analysis_result(self, *, url: str, result: UrlAnalysisResult) -> None:
        cache_key = str(result.url_normalized or normalize_batch_url(url)).strip()
        if not cache_key:
            return
        self._single_analysis_cache[cache_key] = result
        if len(self._single_analysis_cache) <= 256:
            return
        oldest_key = next(iter(self._single_analysis_cache))
        if oldest_key != cache_key:
            self._single_analysis_cache.pop(oldest_key, None)

    def _kick_single_url_analysis(self) -> None:
        if self._is_download_running():
            return
        if bool(self.window.download_payload().get("batch_enabled")):
            self.window.set_single_url_validation_busy(False)
            return
        if self._is_metadata_fetch_disabled():
            self._set_single_metadata_disabled_state()
            return
        if self._single_analysis_thread and self._single_analysis_thread.isRunning():
            return

        url = str(self._single_analysis_pending_url or "").strip()
        if not url:
            self._set_single_analysis_idle_state()
            return
        if not validate_url(url):
            self._set_single_analysis_invalid_state("Enter a valid media URL (http/https).")
            return

        cached = self._cached_single_result_for_url(url)
        if cached is not None:
            self._apply_single_analysis_result(url, cached)
            return

        self._set_single_analysis_validating_state(url)
        self._start_single_analysis_worker(url)

    def _on_single_analysis_summary(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        url, result = payload
        normalized_url = str(url or "").strip()
        if not normalized_url:
            return
        analyzed = self._coerce_single_analysis_result(normalized_url, result)
        self._cache_single_analysis_result(url=normalized_url, result=analyzed)
        self._apply_single_analysis_result(normalized_url, analyzed)

    def _on_single_analysis_error(self, _job_id: str, error: str) -> None:
        current_url = self._first_url_from_input()
        if not current_url:
            return
        active_url = str(self._single_analysis_active_url or "").strip()
        if active_url and normalize_batch_url(current_url) != normalize_batch_url(active_url):
            return
        self._set_single_analysis_invalid_state(str(error or "Unable to analyze URL."))

    def _on_single_analysis_finished(self) -> None:
        finished_url = str(self._single_analysis_active_url or "").strip()
        self._single_analysis_active_url = ""
        self._single_analysis_thread = None
        self._single_analysis_worker = None
        self._stop_metadata_slow_warning_watch_if_idle()
        pending = str(self._single_analysis_pending_url or "").strip()
        if pending and pending != finished_url:
            self._single_analysis_timer.stop()
            self._single_analysis_timer.start()
            return
        self.window.set_single_url_validation_busy(False)

    def _reset_single_url_analysis_for_input_change(self) -> None:
        self._last_probe_url = ""
        self.window.reset_format_quality_for_url_change()
        self._single_analysis_pending_url = self._first_url_from_input()
        self._single_analysis_timer.stop()

    def _record_single_completion_state(self, summary: DownloadSummary) -> None:
        self._last_completed_single_url_normalized = ""
        if self._active_download_is_multi:
            return
        if not summary.results:
            return
        first_result = summary.results[0]
        state = normalize_download_state(str(first_result.state or ""))
        if state not in {DownloadState.DONE.value, DownloadState.SKIPPED.value}:
            return
        completed_url = coerce_http_url(str(first_result.url or "").strip())
        if not completed_url:
            completed_url = coerce_http_url(self._first_url_from_input())
        self._last_completed_single_url_normalized = normalize_batch_url(completed_url)

    def _reset_single_visuals_for_new_completed_url(self) -> None:
        completed_url = str(self._last_completed_single_url_normalized or "").strip()
        if not completed_url:
            return
        current_url = coerce_http_url(self._first_url_from_input())
        current_normalized = normalize_batch_url(current_url)
        if current_normalized == completed_url:
            return
        self.window.reset_download_progress()
        self.window.set_single_url_thumbnail(None, "")
        self._last_completed_single_url_normalized = ""

    def _stop_mismatched_active_single_analysis(self) -> None:
        active_url = str(self._single_analysis_active_url or "").strip()
        pending_url = str(self._single_analysis_pending_url or "").strip()
        if (
            active_url
            and normalize_batch_url(pending_url) != normalize_batch_url(active_url)
            and self._single_analysis_worker is not None
        ):
            self._single_analysis_worker.stop()
            self.window.set_single_url_validation_busy(False)

    def _schedule_or_reset_pending_single_analysis(self) -> None:
        if self._single_analysis_pending_url:
            self._single_analysis_timer.start()
            return
        self._set_single_analysis_idle_state()

    def _on_url_text_changed(self, _text: str) -> None:
        if bool(self.window.download_payload().get("batch_enabled")):
            return
        self._reset_single_visuals_for_new_completed_url()
        if self._is_download_running():
            return
        if self._is_metadata_fetch_disabled():
            self._single_analysis_timer.stop()
            self._single_analysis_pending_url = ""
            if self._single_analysis_worker is not None:
                self._single_analysis_worker.stop()
            self._set_single_metadata_disabled_state()
            return
        self._reset_single_url_analysis_for_input_change()
        self._stop_mismatched_active_single_analysis()
        self._schedule_or_reset_pending_single_analysis()

    def _on_single_format_changed(self, _value: str) -> None:
        self._refresh_single_selection_size_from_cache()

    def _on_single_quality_changed(self, _value: str) -> None:
        self._refresh_single_selection_size_from_cache()

    def _on_quality_dropdown_opened(self) -> None:
        if self._is_metadata_fetch_disabled():
            return
        payload = self.window.download_payload()
        if is_audio_format_choice(str(payload.get("format_choice", "VIDEO")).strip().upper()):
            return
        first_url = self._first_url_from_input()
        cached = self._cached_single_result_for_url(first_url)
        if cached is not None and cached.is_valid and not self.window.is_quality_stale():
            return
        self.probe_url_formats(show_errors=False, force=True, reveal_all_formats=False)

    def _on_load_others_requested(self) -> None:
        if self._is_metadata_fetch_disabled():
            self.window.set_formats_loading(False)
            self.window.append_log("Metadata fetching is disabled in settings.")
            return
        first_url = self._first_url_from_input()
        cached = self._cached_single_result_for_url(first_url)
        if cached is not None and cached.is_valid:
            base_formats, other_formats = self._split_analysis_formats(cached.formats)
            qualities = self._normalize_quality_items(cached.qualities or ["BEST QUALITY"])
            self.window.set_formats_and_qualities(
                base_formats,
                qualities,
                other_formats=other_formats,
                reveal_all_formats=True,
            )
            self._refresh_single_selection_size_from_cache()
            return
        self.window.set_formats_loading(True)
        self.probe_url_formats(show_errors=False, force=True, reveal_all_formats=True)

    def _first_url_from_input(self) -> str:
        payload = self.window.download_payload()
        return first_non_empty_line(str(payload["url_text"] or ""))

    def _build_jobs(self) -> tuple[list[DownloadJob], dict[str, object]]:
        payload = self.window.download_payload()
        url_text = str(payload["url_text"] or "")
        output_dir = str(payload["download_location"] or self.config.download_location)
        format_choice = str(payload["format_choice"] or "VIDEO")
        quality_choice = str(payload["quality_choice"] or "BEST QUALITY")

        first_url = first_non_empty_line(url_text)
        normalized_first_url = coerce_http_url(first_url)
        urls = [normalized_first_url] if validate_url(normalized_first_url) else []

        jobs = [
            DownloadJob(
                job_id=uuid.uuid4().hex[:10],
                url=url,
                format_choice=format_choice,
                quality_choice=quality_choice,
                output_dir=output_dir,
            )
            for url in urls
        ]
        return jobs, payload

    @staticmethod
    def _path_matches_requested_format(path: Path, format_choice: str) -> bool:
        normalized_format = str(format_choice or "VIDEO").strip().upper() or "VIDEO"
        suffix = str(path.suffix or "").strip().lower()
        if normalized_format == "VIDEO":
            return bool(suffix)
        if normalized_format == "AUDIO":
            return suffix in _AUDIO_OUTPUT_EXTENSIONS
        if is_audio_format_choice(normalized_format):
            return suffix == f".{normalized_format.lower()}"
        return suffix == f".{normalized_format.lower()}"

    def _find_existing_single_download_path(self, *, url: str, format_choice: str) -> Path | None:
        if self._is_history_disabled():
            return None
        normalized_url = normalize_batch_url(str(url or "").strip())
        if not normalized_url:
            return None
        for entry in self._download_history:
            if normalize_download_state(str(entry.state or "")) not in {
                DownloadState.DONE.value,
                DownloadState.SKIPPED.value,
            }:
                continue
            if normalize_batch_url(str(entry.url or "").strip()) != normalized_url:
                continue
            raw_output = str(entry.output_path or "").strip()
            if not raw_output:
                continue
            try:
                output_path = Path(raw_output).expanduser()
            except Exception:
                continue
            if (not output_path.exists()) or (not output_path.is_file()):
                continue
            if not self._path_matches_requested_format(output_path, format_choice):
                continue
            return output_path
        return None

    def _is_download_running(self) -> bool:
        return bool(self._download_thread and self._download_thread.isRunning())

    def _is_probe_running(self) -> bool:
        return bool(self._probe_thread and self._probe_thread.isRunning())

    def _effective_batch_concurrency(self, requested: int, job_count: int, *, speed_limit_kbps: int, adaptive_enabled: bool) -> int:
        clamped = max(1, int(requested))
        if (not adaptive_enabled) or job_count <= 1:
            return min(clamped, max(1, int(job_count)))
        if speed_limit_kbps > 0:
            return min(clamped, 2, max(1, int(job_count)))
        if job_count >= 24:
            return min(clamped, 6)
        if job_count >= 12:
            return min(clamped, 5)
        return min(clamped, max(1, int(job_count)))

    def start_downloads(self) -> None:
        if self._is_download_running():
            return
        payload = self.window.download_payload()
        if bool(payload.get("batch_enabled")):
            self._on_multi_start_all()
            return

        jobs, payload = self._build_jobs()
        if not jobs:
            self._show_warning("No valid URLs", "Please enter at least one valid URL.")
            return
        format_choice = str(payload.get("format_choice") or "VIDEO").strip().upper() or "VIDEO"
        skip_existing_files = bool(payload.get("skip_existing_files", self.config.skip_existing_files))
        conflict_policy = str(payload.get("conflict_policy", self.config.conflict_policy)).strip().lower()
        if skip_existing_files and conflict_policy != "overwrite":
            existing_path = self._find_existing_single_download_path(
                url=str(jobs[0].url or ""),
                format_choice=format_choice,
            )
            if existing_path is not None:
                self.window.clear_log()
                self.window.append_log("Starting download...")
                self.window.append_log(f"Already downloaded. File exists: {existing_path}")
                self.window.set_download_progress(100.0)
                self._record_download_history(
                    DownloadState.SKIPPED.value,
                    url=str(jobs[0].url or ""),
                    job_id=str(jobs[0].job_id or ""),
                    output_path=str(existing_path),
                    details="File already exists.",
                )
                return

        self.window.clear_log()
        self.window.append_log("Starting download...")
        self._start_download_worker(jobs, payload=payload, job_to_entry={})

    def stop_downloads(self) -> None:
        if self._download_worker:
            self._download_worker.stop()
        self.download_service.cancel_all()
        if self._is_download_running():
            self.window.append_log("Stopping downloads...")

    def _on_worker_error(self, job_id: str, message: str) -> None:
        DownloadFlow.on_worker_error(self, job_id, message)

    def _on_download_log(self, message: str) -> None:
        DownloadFlow.on_download_log(self, message)

    def _on_download_progress(self, job_id: str, percent: float, message: str) -> None:
        DownloadFlow.on_download_progress(self, job_id, percent, message)

    def _on_download_status(self, job_id: str, state: str) -> None:
        DownloadFlow.on_download_status(self, job_id, state)

    def _refresh_multi_overall_download_progress(self) -> None:
        DownloadFlow.refresh_multi_overall_download_progress(self)

    def _refresh_single_overall_download_progress(self) -> None:
        DownloadFlow.refresh_single_overall_download_progress(self)

    def _refresh_overall_download_progress(self) -> None:
        DownloadFlow.refresh_overall_download_progress(self)

    def _process_download_summary_result(
        self,
        *,
        result: DownloadResult,
        failed_categories: dict[str, int],
        retryable_failed_entry_ids: list[str],
    ) -> bool:
        return DownloadFlow.process_download_summary_result(
            self,
            result=result,
            failed_categories=failed_categories,
            retryable_failed_entry_ids=retryable_failed_entry_ids,
        )

    def _apply_download_summary_error_result(
        self,
        *,
        result: DownloadResult,
        entry_id: str,
        failed_categories: dict[str, int],
        retryable_failed_entry_ids: list[str],
    ) -> bool:
        category, retryable = self._classify_download_error(result.error)
        failed_categories[category] = failed_categories.get(category, 0) + 1
        if entry_id and retryable:
            retryable_failed_entry_ids.append(entry_id)
        if not entry_id:
            return False
        entry = self._batch_entries_by_id.get(entry_id)
        if entry is None:
            return False
        entry.status = BatchEntryStatus.FAILED.value
        hint = self._failure_hint(category)
        entry.error = f"{self._format_classified_error(result.error)} | {hint}"
        self.window.update_batch_entry(entry)
        return True

    @staticmethod
    def _download_summary_totals_line(summary: DownloadSummary) -> str:
        return (
            "Done: "
            f"{summary.completed} | Skipped: {summary.skipped} | Failed: {summary.failed} "
            f"| Cancelled: {summary.cancelled} | Retries used: {summary.retried}"
        )

    def _append_download_summary_logs(
        self,
        *,
        summary: DownloadSummary,
        failed_categories: dict[str, int],
    ) -> None:
        self.window.append_log(self._download_summary_totals_line(summary))
        if not failed_categories:
            return
        summary_text = ", ".join(
            f"{name.upper()}={count}" for name, count in sorted(failed_categories.items(), key=lambda item: item[0])
        )
        self.window.append_log(f"Failure categories: {summary_text}")

    def _maybe_prompt_retryable_failures(self, retryable_failed_entry_ids: list[str]) -> None:
        if not retryable_failed_entry_ids:
            return
        if not bool(self.window.download_payload().get("batch_enabled")):
            return
        answer = self._ask_yes_no(
            "Retry failed items",
            f"{len(retryable_failed_entry_ids)} failed item(s) look retryable.\nRetry them now?",
            default_button=QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            self._pending_retry_entry_ids = retryable_failed_entry_ids

    def _on_download_summary(self, summary: DownloadSummary) -> None:
        self._record_single_completion_state(summary)
        updated_entries = False
        retryable_failed_entry_ids: list[str] = []
        failed_categories: dict[str, int] = {}
        for result in summary.results:
            if self._process_download_summary_result(
                result=result,
                failed_categories=failed_categories,
                retryable_failed_entry_ids=retryable_failed_entry_ids,
            ):
                updated_entries = True

        self._append_download_summary_logs(summary=summary, failed_categories=failed_categories)
        if updated_entries:
            self._mark_batch_queue_dirty()
        self._maybe_prompt_retryable_failures(retryable_failed_entry_ids)

    def _collect_retry_entries(self) -> list[BatchEntry]:
        retry_entries: list[BatchEntry] = []
        if not self._pending_retry_entry_ids:
            return retry_entries
        for entry_id in self._pending_retry_entry_ids:
            entry = self._batch_entries_by_id.get(entry_id)
            if entry is not None and entry.status == BatchEntryStatus.FAILED.value:
                retry_entries.append(entry)
        return retry_entries

    def _on_download_finished(self) -> None:
        DownloadFlow.on_download_finished(self)
        self._refresh_overall_download_progress()
        self.window.set_controls_busy(False)
        self._refresh_single_pause_resume_ui()
        if self._job_to_batch_entry_id:
            self._refresh_batch_entries_view()
        retry_entries = self._collect_retry_entries()
        self._pending_retry_entry_ids = []
        self._download_worker = None
        self._download_thread = None
        self._download_runtime.reset()
        self._request_stale_part_cleanup(reason="post-download")
        self._mark_batch_queue_dirty()
        if retry_entries and (not self._is_download_running()):
            self._start_download_for_entries(retry_entries, source_label="retry-failed")

    def probe_url_formats(
        self,
        *,
        show_errors: bool = False,
        force: bool = False,
        reveal_all_formats: bool = False,
    ) -> None:
        if self._is_metadata_fetch_disabled():
            self._finish_probe_loading_if_needed(reveal_all_formats=reveal_all_formats)
            if show_errors:
                self._show_info("Metadata disabled", "Enable metadata fetching in Settings to load format metadata.")
            return
        first_url = self._first_url_from_input()
        if not self._can_start_probe(
            first_url=first_url,
            force=force,
            show_errors=show_errors,
            reveal_all_formats=reveal_all_formats,
        ):
            return
        self._start_probe_worker(
            url=first_url,
            show_errors=show_errors,
            reveal_all_formats=reveal_all_formats,
        )

    def _finish_probe_loading_if_needed(self, *, reveal_all_formats: bool) -> None:
        if reveal_all_formats:
            self.window.set_formats_loading(False)

    def _can_start_probe(
        self,
        *,
        first_url: str,
        force: bool,
        show_errors: bool,
        reveal_all_formats: bool,
    ) -> bool:
        if self._is_probe_running() or self._is_download_running():
            self._finish_probe_loading_if_needed(reveal_all_formats=reveal_all_formats)
            return False
        if not validate_url(first_url):
            if show_errors:
                self._show_warning("Invalid URL", "Enter a valid URL first.")
            self._finish_probe_loading_if_needed(reveal_all_formats=reveal_all_formats)
            return False
        if (not force) and first_url == self._last_probe_url:
            self._finish_probe_loading_if_needed(reveal_all_formats=reveal_all_formats)
            return False
        return True

    def _start_probe_worker(
        self,
        *,
        url: str,
        show_errors: bool,
        reveal_all_formats: bool,
    ) -> None:
        thread = QThread(self)
        worker = ProbeWorker(
            self.download_service,
            url,
            timeout_seconds=self._metadata_fetch_timeout_seconds(),
        )
        self._probe_pending_url = url
        self._probe_pending_show_errors = bool(show_errors)
        self._probe_pending_reveal_all_formats = bool(reveal_all_formats)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finishedSummary.connect(self._on_probe_summary, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_probe_finished, Qt.ConnectionType.QueuedConnection)
        thread.finished.connect(thread.deleteLater)
        thread.start()

        self._probe_thread = thread
        self._probe_worker = worker

    def _on_probe_summary(self, result: FormatProbeResult) -> None:
        self._on_probe_result(
            self._probe_pending_url,
            result,
            show_errors=self._probe_pending_show_errors,
            reveal_all_formats=self._probe_pending_reveal_all_formats,
        )

    def _on_probe_result(
        self,
        probed_url: str,
        result: FormatProbeResult,
        *,
        show_errors: bool = False,
        reveal_all_formats: bool = False,
    ) -> None:
        self.window.set_formats_and_qualities(
            result.formats,
            result.qualities,
            other_formats=result.other_formats,
            reveal_all_formats=reveal_all_formats,
        )
        if result.error:
            self.window.append_log(f"Probe error: {result.error}")
            if show_errors:
                self._show_warning("Format probe failed", str(result.error))
            return
        self._last_probe_url = probed_url

    def _on_probe_finished(self) -> None:
        self.window.set_formats_loading(False)
        self._probe_worker = None
        self._probe_thread = None
        self._probe_pending_url = ""
        self._probe_pending_show_errors = False
        self._probe_pending_reveal_all_formats = False

    def _refresh_dependency_status(self) -> None:
        status = dependency_status()
        self.window.set_dependency_state("ffmpeg", status["ffmpeg"].installed, status["ffmpeg"].path)
        self.window.set_dependency_state("node", status["node"].installed, status["node"].path)

    def _is_dependency_install_running(self, dependency_name: str) -> bool:
        existing = self._dependency_threads.get(dependency_name)
        return bool(existing and existing.isRunning())

    def _set_dependency_install_start_state(self, dependency_name: str) -> None:
        self.window.set_dependency_install_busy(dependency_name, True)
        self._dependency_progress_bucket[dependency_name] = -1
        self.window.set_download_progress(0.0)
        self.window.append_log(f"Installing {dependency_name}...")

    def _start_dependency_install_worker(self, dependency_name: str) -> tuple[QThread, DependencyWorker]:
        thread = QThread(self)
        worker = DependencyWorker(self.dependency_service, dependency_name)
        worker.setProperty("dependency_name", dependency_name)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progressChanged.connect(self._on_dependency_progress, Qt.ConnectionType.QueuedConnection)
        worker.statusChanged.connect(self._on_dependency_status, Qt.ConnectionType.QueuedConnection)
        worker.logChanged.connect(self._on_dependency_worker_log, Qt.ConnectionType.QueuedConnection)
        worker.errorRaised.connect(self._on_dependency_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(self._on_dependency_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        return thread, worker

    def install_dependency(self, name: str) -> None:
        normalized = str(name or "").strip().lower()
        if normalized not in {"ffmpeg", "node"}:
            return
        status = dependency_status().get(normalized)
        if status and status.installed:
            self._refresh_dependency_status()
            return
        if self._is_dependency_install_running(normalized):
            return

        self._set_dependency_install_start_state(normalized)
        thread, worker = self._start_dependency_install_worker(normalized)
        self._dependency_threads[normalized] = thread
        self._dependency_workers[normalized] = worker

    def _on_dependency_status(self, dependency_name: str, state: str) -> None:
        normalized_state = str(state or "").strip().lower()
        if normalized_state in {"installing", "ready"}:
            return
        self.window.append_log(f"[{dependency_name}] {normalized_state}")

    def _on_dependency_error(self, dependency_name: str, error: str) -> None:
        self._show_warning(
            "Dependency install failed",
            f"Failed to install {dependency_name}: {error}",
        )

    def _on_dependency_log(self, dependency_name: str, message: str) -> None:
        normalized = str(dependency_name or "").strip().lower()
        detail = str(message or "").strip()
        if not detail:
            return
        if normalized:
            self.window.append_log(f"[{normalized}] {detail}")
            return
        self.window.append_log(detail)

    def _on_dependency_worker_log(self, message: str) -> None:
        sender = self.sender()
        dependency_name = ""
        if isinstance(sender, QObject):
            dependency_name = str(sender.property("dependency_name") or "").strip().lower()
        self._on_dependency_log(dependency_name, message)

    def _on_dependency_finished(self, dependency_name: str) -> None:
        normalized = str(dependency_name or "").strip().lower()
        if not normalized:
            return
        self._refresh_dependency_status()
        self.window.set_dependency_install_busy(normalized, False)
        self._dependency_progress_bucket.pop(normalized, None)
        self._dependency_threads.pop(normalized, None)
        self._dependency_workers.pop(normalized, None)

    def _on_dependency_worker_finished(self) -> None:
        sender = self.sender()
        dependency_name = ""
        if isinstance(sender, QObject):
            dependency_name = str(sender.property("dependency_name") or "").strip().lower()
        if dependency_name:
            self._on_dependency_finished(dependency_name)

    def _on_dependency_progress(self, dependency_name: str, percent: int, _message: str) -> None:
        normalized = str(dependency_name or "").strip().lower()
        clamped_percent = max(0, min(100, int(percent)))
        previous = self._dependency_progress_bucket.get(normalized, -1)
        if clamped_percent <= previous:
            return
        self._dependency_progress_bucket[normalized] = clamped_percent
        self.window.set_download_progress(float(clamped_percent))

    def start_update_check(self, *, manual: bool) -> None:
        self._update_flow.start_check(manual=manual)

    def open_downloads_folder(self) -> None:
        HistoryFlow.open_downloads_folder(self)

    def _on_history_open_file(self, path_value: str) -> None:
        HistoryFlow.on_history_open_file(self, path_value)

    def _on_history_open_folder(self, path_value: str) -> None:
        HistoryFlow.on_history_open_folder(self, path_value)

    def _on_history_retry_url(self, url_value: str) -> None:
        HistoryFlow.on_history_retry_url(self, url_value)

    def _on_history_clear(self) -> None:
        HistoryFlow.on_history_clear(self)
