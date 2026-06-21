from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .paths import runtime_storage_dir

PARTIAL_FILE_MANIFEST = "partial_files.json"
_MANIFEST_LOCK = threading.RLock()


def partial_manifest_path() -> Path:
    return runtime_storage_dir() / PARTIAL_FILE_MANIFEST


def _read_manifest() -> set[str]:
    path = partial_manifest_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return set()
    items = raw.get("paths") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return set()
    return {str(item or "").strip() for item in items if str(item or "").strip()}


def _write_manifest(paths: set[str]) -> None:
    path = partial_manifest_path()
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps({"paths": sorted(paths)}, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(path))
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _normalize_path_value(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except Exception:
        return raw


def part_candidates_for_output(output_path: str) -> set[Path]:
    raw = str(output_path or "").strip()
    if not raw:
        return set()
    try:
        path = Path(raw).expanduser()
    except Exception:
        return set()
    name = str(path.name or "").lower()
    if name.endswith(".part"):
        return {path}
    candidates = {Path(f"{path}.part")}
    if path.suffix:
        candidates.add(path.with_suffix(f"{path.suffix}.part"))
    try:
        candidates.add(path.with_suffix(".part"))
    except ValueError:
        pass
    return candidates


def record_partial_candidates(output_path: str) -> None:
    candidates = part_candidates_for_output(output_path)
    if not candidates:
        return
    with _MANIFEST_LOCK:
        paths = _read_manifest()
        for candidate in candidates:
            normalized = _normalize_path_value(candidate)
            if normalized:
                paths.add(normalized)
        _write_manifest(paths)


def discard_partial_candidates(output_path: str) -> None:
    candidates = part_candidates_for_output(output_path)
    if not candidates:
        return
    normalized: set[str] = set()
    for candidate in candidates:
        item = _normalize_path_value(candidate)
        if item:
            normalized.add(item)
    with _MANIFEST_LOCK:
        paths = _read_manifest()
        changed = bool(paths.intersection(normalized))
        paths.difference_update(normalized)
        if changed:
            _write_manifest(paths)


def list_tracked_partial_paths() -> list[str]:
    with _MANIFEST_LOCK:
        return sorted(_read_manifest())


def remove_tracked_partial_paths(paths_to_remove: list[str]) -> None:
    normalized = {_normalize_path_value(item) for item in paths_to_remove if str(item or "").strip()}
    normalized.discard("")
    if not normalized:
        return
    with _MANIFEST_LOCK:
        paths = _read_manifest()
        paths.difference_update(normalized)
        _write_manifest(paths)
