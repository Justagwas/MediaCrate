from __future__ import annotations


def format_size_human(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "Unknown"
    try:
        value = float(int(size_bytes))
    except Exception:
        return "Unknown"
    if value <= 0:
        return "Unknown"
    units = ("B", "KB", "MB", "GB", "TB")
    unit_index = 0
    while value >= 1024.0 and unit_index < (len(units) - 1):
        value /= 1024.0
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.2f} {units[unit_index]}"


def format_batch_stats_line(
    *,
    queued: int,
    downloading: int,
    in_progress: int,
    downloaded: int,
    valid: int,
    invalid: int,
    pending: int,
    duplicates: int,
) -> str:
    return (
        f"Downloaded: {int(downloaded)}  |  Downloading: {int(downloading)}"
        f"  |  In progress: {int(in_progress)}  |  Queued: {int(queued)}"
        f"  |  Valid: {int(valid)}  |  Invalid: {int(invalid)}"
        f"  |  Duplicates: {int(duplicates)}  |  Pending: {int(pending)}"
    )
