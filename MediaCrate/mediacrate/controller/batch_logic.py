from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.models import BatchEntry, BatchEntryStatus


@dataclass(frozen=True, slots=True)
class BatchStats:
    queued: int
    downloading: int
    in_progress: int
    downloaded: int
    valid: int
    invalid: int
    pending: int
    duplicates: int


def compute_batch_stats(entries: list[BatchEntry]) -> BatchStats:
    queued = sum(1 for item in entries if item.status == BatchEntryStatus.DOWNLOAD_QUEUED.value)
    downloading = sum(1 for item in entries if item.status == BatchEntryStatus.DOWNLOADING.value)
    in_progress = sum(
        1
        for item in entries
        if item.status in {
            BatchEntryStatus.DOWNLOAD_QUEUED.value,
            BatchEntryStatus.DOWNLOADING.value,
            BatchEntryStatus.PAUSED.value,
        }
    )
    downloaded = sum(
        1
        for item in entries
        if item.status in {BatchEntryStatus.DONE.value, BatchEntryStatus.SKIPPED.value}
    )
    valid = sum(1 for item in entries if item.status == BatchEntryStatus.VALID.value)
    invalid = sum(
        1
        for item in entries
        if (item.status in {BatchEntryStatus.INVALID.value, BatchEntryStatus.FAILED.value}) and (not item.is_duplicate)
    )
    pending = sum(1 for item in entries if item.status == BatchEntryStatus.VALIDATING.value)
    duplicates = sum(1 for item in entries if item.is_duplicate)
    return BatchStats(
        queued=queued,
        downloading=downloading,
        in_progress=in_progress,
        downloaded=downloaded,
        valid=valid,
        invalid=invalid,
        pending=pending,
        duplicates=duplicates,
    )


def is_terminal_batch_state(state: str) -> bool:
    normalized = str(state or "").strip().lower()
    return normalized in {
        BatchEntryStatus.DONE.value,
        BatchEntryStatus.SKIPPED.value,
        BatchEntryStatus.FAILED.value,
        BatchEntryStatus.CANCELLED.value,
    }


def entry_has_analysis_metadata(entry: BatchEntry | None) -> bool:
    if entry is None:
        return False
    status = str(entry.status or "").strip().lower()
    if status in {
        BatchEntryStatus.VALID.value,
        BatchEntryStatus.DOWNLOAD_QUEUED.value,
        BatchEntryStatus.DOWNLOADING.value,
        BatchEntryStatus.PAUSED.value,
        BatchEntryStatus.DONE.value,
        BatchEntryStatus.SKIPPED.value,
        BatchEntryStatus.FAILED.value,
        BatchEntryStatus.CANCELLED.value,
    }:
        return True
    if str(entry.title or "").strip():
        return True
    if entry.expected_size_bytes is not None:
        return True
    if str(entry.thumbnail_url or "").strip():
        return True
    return False


def is_entry_eligible_for_active_enqueue(entry: BatchEntry) -> bool:
    if not entry.syntax_valid:
        return False
    return entry.status not in {
        BatchEntryStatus.INVALID.value,
        BatchEntryStatus.VALIDATING.value,
        BatchEntryStatus.DOWNLOADING.value,
        BatchEntryStatus.DOWNLOAD_QUEUED.value,
        BatchEntryStatus.PAUSED.value,
    }


def collect_ready_deduped_entries(
    entries: list[BatchEntry],
    *,
    active_entry_ids: set[str] | None = None,
    require_unassigned: bool = False,
    signature_builder: Callable[[BatchEntry], tuple[str, str, str]],
) -> tuple[list[BatchEntry], int]:
    active_ids = set(active_entry_ids or ())
    seen_signatures: set[tuple[str, str, str]] = set()
    selected: list[BatchEntry] = []
    skipped_same_signature = 0
    for entry in entries:
        if (not entry.syntax_valid) or (entry.status != BatchEntryStatus.VALID.value):
            continue
        if require_unassigned and str(entry.entry_id or "").strip() in active_ids:
            continue
        signature = signature_builder(entry)
        if signature in seen_signatures:
            skipped_same_signature += 1
            continue
        seen_signatures.add(signature)
        selected.append(entry)
    return selected, skipped_same_signature


def collect_start_all_counts(entries: list[BatchEntry], *, selected_count: int) -> tuple[int, int]:
    pending_count = sum(1 for entry in entries if entry.status == BatchEntryStatus.VALIDATING.value)
    skipped_count = max(0, len(entries) - int(selected_count) - pending_count)
    return skipped_count, pending_count


def start_all_skip_suffix(skipped_same_signature: int) -> str:
    if skipped_same_signature <= 0:
        return ""
    return f" including {skipped_same_signature} same URL+format+quality duplicate row(s)"


def build_start_all_skip_log_message(
    *,
    started_count: int,
    skipped_count: int,
    pending_count: int,
    skipped_same_signature: int,
    queued_into_active: bool,
) -> str | None:
    if skipped_count <= 0 and pending_count <= 0:
        return None
    suffix = start_all_skip_suffix(skipped_same_signature)
    if queued_into_active:
        return (
            f"Queued {started_count} additional row(s), "
            f"skipping {skipped_count} invalid/ineligible and {pending_count} pending row(s){suffix}."
        )
    return (
        f"Start all: running {started_count} valid row(s), "
        f"skipping {skipped_count} invalid/ineligible and {pending_count} pending row(s){suffix}."
    )
