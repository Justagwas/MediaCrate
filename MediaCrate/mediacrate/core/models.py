from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FormatChoice(StrEnum):
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    MP4 = "MP4"
    MP3 = "MP3"


class BatchEntryStatus(StrEnum):
    INVALID = "invalid"
    VALIDATING = "validating"
    VALID = "valid"
    DOWNLOAD_QUEUED = "download_queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadState(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    RETRYING = "retrying"
    PAUSED = "paused"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL_DOWNLOAD_STATES = frozenset(
    {
        DownloadState.DONE.value,
        DownloadState.ERROR.value,
        DownloadState.CANCELLED.value,
        DownloadState.SKIPPED.value,
    }
)


class RetryProfile(StrEnum):
    OFF = "off"
    BASIC = "basic"
    AGGRESSIVE = "aggressive"


_AUDIO_ONLY_FORMAT_CHOICES = frozenset(
    {
        FormatChoice.AUDIO.value,
        FormatChoice.MP3.value,
        "AAC",
        "AIFF",
        "ALAC",
        "AMR",
        "AIF",
        "FLAC",
        "M4A",
        "MP2",
        "OGA",
        "OGG",
        "OPUS",
        "WAV",
        "WMA",
    }
)


def is_audio_format_choice(value: str) -> bool:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return False
    return normalized in _AUDIO_ONLY_FORMAT_CHOICES


@dataclass(slots=True)
class AppConfig:
    schema_version: int
    theme_mode: str
    ui_scale_percent: int
    download_location: str
    batch_enabled: bool
    batch_concurrency: int
    skip_existing_files: bool
    auto_start_ready_links: bool
    batch_retry_count: int
    filename_template: str
    conflict_policy: str
    download_speed_limit_kbps: int
    adaptive_batch_concurrency: bool
    auto_check_updates: bool
    window_geometry: str = ""
    disable_metadata_fetch: bool = False
    disable_history: bool = False
    retry_profile: str = RetryProfile.BASIC.value
    fallback_download_on_metadata_error: bool = True
    stale_part_cleanup_hours: int = 48


@dataclass(slots=True)
class FormatProbeResult:
    title: str = ""
    formats: list[str] = field(default_factory=list)
    other_formats: list[str] = field(default_factory=list)
    qualities: list[str] = field(default_factory=list)
    error: str = ""


@dataclass(slots=True)
class BatchEntry:
    entry_id: str
    url_raw: str
    url_normalized: str
    syntax_valid: bool
    status: str
    title: str = ""
    expected_size_bytes: int | None = None
    format_choice: str = FormatChoice.VIDEO.value
    quality_choice: str = "BEST QUALITY"
    attempts: int = 0
    progress_percent: float = 0.0
    error: str = ""
    is_duplicate: bool = False
    thumbnail_url: str = ""
    available_formats: list[str] = field(default_factory=list)
    available_qualities: list[str] = field(default_factory=list)


@dataclass(slots=True)
class UrlAnalysisResult:
    url_raw: str
    url_normalized: str
    is_valid: bool
    title: str = ""
    thumbnail_url: str = ""
    expected_size_bytes: int | None = None
    duration_seconds: int | None = None
    source_label: str = ""
    formats: list[str] = field(default_factory=list)
    qualities: list[str] = field(default_factory=list)
    selection_size_estimates: dict[str, int] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class DownloadJob:
    job_id: str
    url: str
    format_choice: str
    quality_choice: str
    output_dir: str


@dataclass(slots=True)
class DownloadResult:
    job_id: str
    url: str
    state: str
    output_path: str = ""
    error: str = ""


@dataclass(slots=True)
class DownloadSummary:
    total: int
    completed: int
    failed: int
    skipped: int
    cancelled: int
    retried: int
    results: list[DownloadResult] = field(default_factory=list)


@dataclass(slots=True)
class UpdateCheckResult:
    update_available: bool
    current_version: str
    latest_version: str = ""
    download_url: str = ""
    source: str = ""
    error: str = ""


@dataclass(slots=True)
class DependencyStatus:
    name: str
    installed: bool
    path: str = ""


@dataclass(slots=True)
class DownloadHistoryEntry:
    timestamp_utc: str
    url: str
    title: str
    state: str
    output_path: str
    details: str = ""
