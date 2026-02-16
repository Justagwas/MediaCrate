from __future__ import annotations

from collections.abc import Callable


def active_job_id_for_entry(batch_entry_to_active_job_id: dict[str, str], entry_id: str) -> str:
    return batch_entry_to_active_job_id.get(str(entry_id or "").strip(), "")


def active_single_job_id(
    state_by_job: dict[str, str],
    *,
    active_download_is_multi: bool,
    normalize_download_state: Callable[[str], str],
    terminal_states: set[str],
) -> str:
    if active_download_is_multi:
        return ""
    for job_id, state in state_by_job.items():
        normalized = normalize_download_state(state)
        if normalized not in terminal_states:
            return str(job_id or "").strip()
    return next(iter(state_by_job), "")


def active_multi_job_ids(
    state_by_job: dict[str, str],
    *,
    active_download_is_multi: bool,
    normalize_download_state: Callable[[str], str],
    terminal_states: set[str],
) -> list[str]:
    if not active_download_is_multi:
        return []
    active_ids: list[str] = []
    for job_id, state in state_by_job.items():
        key = str(job_id or "").strip()
        if not key:
            continue
        normalized = normalize_download_state(state)
        if normalized in terminal_states:
            continue
        active_ids.append(key)
    return active_ids


def all_jobs_paused(
    *,
    state_by_job: dict[str, str],
    active_job_ids: list[str],
    paused_state: str,
) -> bool:
    return all(state_by_job.get(job_id) == paused_state for job_id in active_job_ids)


def partition_multi_pause_actions(
    *,
    state_by_job: dict[str, str],
    active_job_ids: list[str],
    paused_state: str,
) -> tuple[bool, list[str], list[str]]:
    all_paused = all_jobs_paused(
        state_by_job=state_by_job,
        active_job_ids=active_job_ids,
        paused_state=paused_state,
    )
    if all_paused:
        return True, list(active_job_ids), []
    to_pause = [job_id for job_id in active_job_ids if state_by_job.get(job_id) != paused_state]
    return False, [], to_pause
