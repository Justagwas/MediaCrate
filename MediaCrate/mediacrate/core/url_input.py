from __future__ import annotations

from collections.abc import Iterator


def iter_non_empty_lines(text: str) -> Iterator[str]:
    for raw_line in str(text or "").splitlines():
        value = str(raw_line or "").strip()
        if value:
            yield value


def first_non_empty_line(text: str) -> str:
    return next(iter_non_empty_lines(text), "")
