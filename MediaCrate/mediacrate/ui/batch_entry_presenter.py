from __future__ import annotations

from dataclasses import dataclass

from ..core.formatting import format_size_human
from ..core.models import BatchEntry, BatchEntryStatus, DEFAULT_FORMAT_CHOICES, is_audio_format_choice


_RUNTIME_BATCH_STATUSES = frozenset(
    {
        BatchEntryStatus.DOWNLOAD_QUEUED.value,
        BatchEntryStatus.DOWNLOADING.value,
        BatchEntryStatus.PAUSED.value,
        BatchEntryStatus.DONE.value,
        BatchEntryStatus.SKIPPED.value,
        BatchEntryStatus.FAILED.value,
        BatchEntryStatus.CANCELLED.value,
    }
)


@dataclass(frozen=True, slots=True)
class BatchEntryViewState:
    entry_id: str
    full_url_text: str
    thumbnail_url: str
    status_state: str
    status_label: str
    url_state: str
    formats: tuple[str, ...]
    qualities: tuple[str, ...]
    selected_format: str
    selected_quality: str
    quality_allowed: bool
    detail_text: str
    can_download: bool
    can_remove: bool
    primary_action: str
    primary_button_text: str
    is_duplicate_visual: bool
    signature: tuple[object, ...]

def status_label_for_state(state: str) -> str:
    mapping = {
        "duplicate": "Duplicate",
        BatchEntryStatus.INVALID.value: "Invalid",
        BatchEntryStatus.VALIDATING.value: "Checking",
        BatchEntryStatus.VALID.value: "Ready",
        BatchEntryStatus.DOWNLOAD_QUEUED.value: "Queued",
        BatchEntryStatus.DOWNLOADING.value: "Downloading",
        BatchEntryStatus.PAUSED.value: "Paused",
        BatchEntryStatus.DONE.value: "Done",
        BatchEntryStatus.SKIPPED.value: "Skipped",
        BatchEntryStatus.FAILED.value: "Failed",
        BatchEntryStatus.CANCELLED.value: "Cancelled",
    }
    return mapping.get(str(state or "").strip().lower(), "Unknown")


def build_batch_entry_view_state(entry: BatchEntry) -> BatchEntryViewState:
    normalized_status = str(entry.status or BatchEntryStatus.INVALID.value).strip().lower()
    status_state = normalized_status
    if bool(entry.is_duplicate) and normalized_status not in _RUNTIME_BATCH_STATUSES:
        status_state = "duplicate"

    if status_state == "duplicate":
        url_state = "duplicate"
    elif normalized_status in {BatchEntryStatus.DONE.value, BatchEntryStatus.SKIPPED.value}:
        url_state = "done"
    elif normalized_status in {BatchEntryStatus.INVALID.value, BatchEntryStatus.FAILED.value}:
        url_state = "invalid"
    elif normalized_status == BatchEntryStatus.PAUSED.value:
        url_state = "paused"
    else:
        url_state = "default"

    formats = [
        str(item or "").strip().upper()
        for item in (entry.available_formats or [])
        if str(item or "").strip()
    ]
    if not formats:
        formats = list(DEFAULT_FORMAT_CHOICES)
    explicit_format = str(entry.format_choice or "").strip().upper()
    if explicit_format and explicit_format not in formats:
        formats.append(explicit_format)
    selected_format = explicit_format or "VIDEO"
    if selected_format not in formats:
        selected_format = "VIDEO" if "VIDEO" in formats else formats[0]

    qualities = [
        str(item or "").strip().upper()
        for item in (entry.available_qualities or [])
        if str(item or "").strip()
    ]
    if not qualities:
        qualities = ["BEST QUALITY"]
    if "BEST QUALITY" not in qualities:
        qualities.insert(0, "BEST QUALITY")
    selected_quality = str(entry.quality_choice or "BEST QUALITY").strip().upper() or "BEST QUALITY"
    if is_audio_format_choice(selected_format) or selected_quality not in qualities:
        selected_quality = "BEST QUALITY"
    quality_allowed = not is_audio_format_choice(selected_format)

    size_text = format_size_human(entry.expected_size_bytes)
    title_text = str(entry.title or "").strip() or "Unknown title"
    if entry.error:
        detail_text = (
            f"{title_text}  |  "
            f"Size: {size_text}  |  "
            f"{entry.error}"
        )
    else:
        detail_text = (
            f"{title_text}"
            f"  |  Size: {size_text}"
            f"  |  Progress: {max(0.0, min(100.0, float(entry.progress_percent))):.2f}%"
            f"  |  Attempts: {max(0, int(entry.attempts))}"
        )

    can_download = normalized_status in {
        BatchEntryStatus.VALID.value,
        BatchEntryStatus.FAILED.value,
        BatchEntryStatus.CANCELLED.value,
        BatchEntryStatus.SKIPPED.value,
        BatchEntryStatus.DONE.value,
        BatchEntryStatus.DOWNLOADING.value,
        BatchEntryStatus.DOWNLOAD_QUEUED.value,
        BatchEntryStatus.PAUSED.value,
    }
    if normalized_status in {BatchEntryStatus.DOWNLOADING.value, BatchEntryStatus.DOWNLOAD_QUEUED.value}:
        primary_action = "pause"
        primary_button_text = "Pause"
    elif normalized_status == BatchEntryStatus.PAUSED.value:
        primary_action = "resume"
        primary_button_text = "Resume"
    else:
        primary_action = "download"
        is_retry = normalized_status in {BatchEntryStatus.FAILED.value, BatchEntryStatus.CANCELLED.value}
        primary_button_text = "Retry" if is_retry else "Download"

    signature = (
        str(entry.entry_id or "").strip(),
        str(entry.url_raw or "").strip(),
        str(entry.thumbnail_url or "").strip(),
        normalized_status,
        bool(entry.is_duplicate),
        str(entry.title or "").strip(),
        int(entry.expected_size_bytes) if entry.expected_size_bytes is not None else None,
        selected_format,
        selected_quality,
        max(0, int(entry.attempts)),
        round(max(0.0, min(100.0, float(entry.progress_percent))), 3),
        str(entry.error or "").strip(),
        tuple(formats),
        tuple(qualities),
    )

    return BatchEntryViewState(
        entry_id=str(entry.entry_id or "").strip(),
        full_url_text=str(entry.url_raw or "").strip(),
        thumbnail_url=str(entry.thumbnail_url or "").strip(),
        status_state=status_state,
        status_label=status_label_for_state(status_state),
        url_state=url_state,
        formats=tuple(formats),
        qualities=tuple(qualities),
        selected_format=selected_format,
        selected_quality=selected_quality,
        quality_allowed=quality_allowed,
        detail_text=detail_text,
        can_download=can_download,
        can_remove=True,
        primary_action=primary_action,
        primary_button_text=primary_button_text,
        is_duplicate_visual=bool(entry.is_duplicate),
        signature=signature,
    )


def batch_entry_render_signature(
    entry: BatchEntry,
    *,
    controls_locked: bool,
    settings_visible: bool,
) -> tuple[object, ...]:
    del settings_visible
    view = build_batch_entry_view_state(entry)
    return (
        *view.signature,
        bool(controls_locked),
    )
