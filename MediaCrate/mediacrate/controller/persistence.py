from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Callable

from ..core.download_service import normalize_batch_url, validate_url
from ..core.models import (
    BatchEntry,
    BatchEntryStatus,
    DownloadHistoryEntry,
    DEFAULT_FORMAT_CHOICES,
    DEFAULT_QUALITY_CHOICES,
    is_audio_format_choice,
)
from ..core.paths import batch_queue_state_path, download_history_path


def queue_snapshot_path() -> Path:
    return batch_queue_state_path()


def history_path() -> Path:
    return download_history_path()


def clear_path(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def save_json_atomically(path: Path, payload: object) -> bool:
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp_path), str(path))
        return True
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def serialize_batch_entry(entry: BatchEntry) -> dict[str, object]:
    return {
        "entry_id": str(entry.entry_id or ""),
        "url_raw": str(entry.url_raw or ""),
        "url_normalized": str(entry.url_normalized or ""),
        "syntax_valid": bool(entry.syntax_valid),
        "status": str(entry.status or BatchEntryStatus.INVALID.value),
        "title": str(entry.title or ""),
        "expected_size_bytes": entry.expected_size_bytes if entry.expected_size_bytes is not None else None,
        "format_choice": str(entry.format_choice or "VIDEO"),
        "quality_choice": str(entry.quality_choice or "BEST QUALITY"),
        "attempts": int(max(0, int(entry.attempts))),
        "progress_percent": float(max(0.0, min(100.0, float(entry.progress_percent)))),
        "error": str(entry.error or ""),
        "is_duplicate": bool(entry.is_duplicate),
        "thumbnail_url": str(entry.thumbnail_url or ""),
        "available_formats": [str(item or "") for item in (entry.available_formats or [])],
        "available_qualities": [str(item or "") for item in (entry.available_qualities or [])],
    }


def deserialize_batch_entry(
    payload: object,
    *,
    dedupe_preserve: Callable[[list[str]], list[str]],
) -> BatchEntry | None:
    if not isinstance(payload, dict):
        return None
    url_raw = str(payload.get("url_raw") or "").strip()
    if not url_raw:
        return None
    entry_id = str(payload.get("entry_id") or "").strip() or uuid.uuid4().hex[:10]
    syntax_valid = bool(payload.get("syntax_valid")) and validate_url(url_raw)
    url_normalized = str(payload.get("url_normalized") or "").strip()
    if syntax_valid and not url_normalized:
        url_normalized = normalize_batch_url(url_raw)
    status = str(payload.get("status") or BatchEntryStatus.INVALID.value).strip().lower()
    known_statuses = {state.value for state in BatchEntryStatus}
    if status not in known_statuses:
        status = BatchEntryStatus.INVALID.value
    if status in {
        BatchEntryStatus.DOWNLOAD_QUEUED.value,
        BatchEntryStatus.DOWNLOADING.value,
        BatchEntryStatus.VALIDATING.value,
    }:
        status = BatchEntryStatus.VALID.value if syntax_valid else BatchEntryStatus.INVALID.value

    try:
        expected_size = payload.get("expected_size_bytes")
        expected_size_int = int(expected_size) if expected_size is not None else None
    except (TypeError, ValueError):
        expected_size_int = None

    try:
        attempts = max(0, int(payload.get("attempts", 0)))
    except (TypeError, ValueError):
        attempts = 0
    try:
        progress = max(0.0, min(100.0, float(payload.get("progress_percent", 0.0))))
    except (TypeError, ValueError):
        progress = 0.0

    available_formats = dedupe_preserve(
        [str(item or "").strip().upper() for item in payload.get("available_formats", []) if str(item or "").strip()]
    )
    if not available_formats:
        available_formats = list(DEFAULT_FORMAT_CHOICES)
    available_qualities = dedupe_preserve(
        [str(item or "").strip().upper() for item in payload.get("available_qualities", []) if str(item or "").strip()]
    )
    if not available_qualities:
        available_qualities = list(DEFAULT_QUALITY_CHOICES)
    if "BEST QUALITY" not in available_qualities:
        available_qualities.insert(0, "BEST QUALITY")

    format_choice = str(payload.get("format_choice") or "VIDEO").strip().upper() or "VIDEO"
    if format_choice not in available_formats:
        format_choice = "VIDEO" if "VIDEO" in available_formats else available_formats[0]
    quality_choice = str(payload.get("quality_choice") or "BEST QUALITY").strip().upper() or "BEST QUALITY"
    if is_audio_format_choice(format_choice) or quality_choice not in available_qualities:
        quality_choice = "BEST QUALITY"

    error = str(payload.get("error") or "").strip()
    if status == BatchEntryStatus.INVALID.value and not error:
        error = "Invalid URL"
    original_status = str(payload.get("status") or "").strip().lower()
    if status == BatchEntryStatus.VALID.value and not error and original_status in {
        BatchEntryStatus.DOWNLOADING.value,
        BatchEntryStatus.DOWNLOAD_QUEUED.value,
        BatchEntryStatus.VALIDATING.value,
    }:
        error = "Recovered from previous session."

    return BatchEntry(
        entry_id=entry_id,
        url_raw=url_raw,
        url_normalized=url_normalized,
        syntax_valid=syntax_valid,
        status=status,
        title=str(payload.get("title") or ""),
        expected_size_bytes=expected_size_int,
        format_choice=format_choice,
        quality_choice=quality_choice,
        attempts=attempts,
        progress_percent=progress,
        error=error,
        is_duplicate=bool(payload.get("is_duplicate")),
        thumbnail_url=str(payload.get("thumbnail_url") or ""),
        available_formats=available_formats,
        available_qualities=available_qualities,
    )


def serialize_history_entry(entry: DownloadHistoryEntry) -> dict[str, str]:
    return {
        "timestamp_utc": str(entry.timestamp_utc or ""),
        "url": str(entry.url or ""),
        "title": str(entry.title or ""),
        "state": str(entry.state or ""),
        "output_path": str(entry.output_path or ""),
        "details": str(entry.details or ""),
    }


def deserialize_history_entry(payload: object) -> DownloadHistoryEntry | None:
    if not isinstance(payload, dict):
        return None
    url = str(payload.get("url") or "").strip()
    if not url:
        return None
    return DownloadHistoryEntry(
        timestamp_utc=str(payload.get("timestamp_utc") or ""),
        url=url,
        title=str(payload.get("title") or ""),
        state=str(payload.get("state") or ""),
        output_path=str(payload.get("output_path") or ""),
        details=str(payload.get("details") or ""),
    )
