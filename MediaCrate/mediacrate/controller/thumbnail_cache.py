from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Callable


@dataclass(slots=True)
class _ThumbnailCacheEntry:
    data: bytes
    touched_at: float


class ThumbnailCache:
    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        time_fn: Callable[[], float] | None = None,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_bytes = max(1, int(max_bytes))
        self._time_fn = time_fn or monotonic
        self._items: OrderedDict[str, _ThumbnailCacheEntry] = OrderedDict()
        self._total_bytes = 0

    @property
    def total_bytes(self) -> int:
        return int(self._total_bytes)

    @property
    def size(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._total_bytes = 0

    def get(self, key: str) -> bytes | None:
        normalized = str(key or "").strip()
        if not normalized:
            return None
        entry = self._items.pop(normalized, None)
        if entry is None:
            return None
        entry.touched_at = float(self._time_fn())
        self._items[normalized] = entry
        return bytes(entry.data)

    def set(self, key: str, data: bytes) -> bool:
        normalized = str(key or "").strip()
        payload = bytes(data or b"")
        if not normalized or not payload:
            return False

        payload_size = len(payload)
        existing = self._items.pop(normalized, None)
        if existing is not None:
            self._total_bytes -= len(existing.data)

        # If a single thumbnail cannot fit, keep cache healthy and skip storing it.
        if payload_size > self._max_bytes:
            self._prune_limits()
            return False

        self._items[normalized] = _ThumbnailCacheEntry(
            data=payload,
            touched_at=float(self._time_fn()),
        )
        self._total_bytes += payload_size
        self._prune_limits()
        return True

    def purge_older_than(self, max_age_seconds: float) -> int:
        max_age = max(0.0, float(max_age_seconds))
        now = float(self._time_fn())
        removed = 0
        keys_to_remove: list[str] = []
        for key, entry in self._items.items():
            if (now - entry.touched_at) > max_age:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            removed_entry = self._items.pop(key, None)
            if removed_entry is None:
                continue
            self._total_bytes -= len(removed_entry.data)
            removed += 1
        if self._total_bytes < 0:
            self._total_bytes = 0
        return removed

    def _prune_limits(self) -> None:
        while self._items and (
            len(self._items) > self._max_entries or self._total_bytes > self._max_bytes
        ):
            _key, entry = self._items.popitem(last=False)
            self._total_bytes -= len(entry.data)
        if self._total_bytes < 0:
            self._total_bytes = 0
