from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

from .config import APP_NAME

@lru_cache(maxsize=1)
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def bundle_dir() -> Path | None:
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    try:
        return Path(str(base)).resolve()
    except Exception:
        return None


@lru_cache(maxsize=1)
def appdata_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base).resolve() / APP_NAME
    return Path.home() / APP_NAME


def default_download_dir() -> Path:
    return Path.home() / "Downloads" / APP_NAME


@lru_cache(maxsize=1)
def runtime_storage_dir() -> Path:
    target = appdata_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Unable to create LOCALAPPDATA storage directory: {target}. "
            "Check folder permissions and available disk space."
        ) from exc
    return target


def batch_queue_state_path() -> Path:
    return runtime_storage_dir() / "batch_queue_state.json"


def download_history_path() -> Path:
    return runtime_storage_dir() / "download_history.json"


def resolve_app_asset(asset_name: str) -> Path | None:
    name = str(asset_name or "").strip()
    if not name:
        return None
    search_bases: list[Path] = []
    bundle_base = bundle_dir()
    if bundle_base is not None:
        search_bases.append(bundle_base)
    search_bases.append(app_dir())
    for base in _unique_paths(search_bases):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def _binary_name_candidates(binary_name: str) -> list[str]:
    if os.name == "nt" and not binary_name.lower().endswith(".exe"):
        return [f"{binary_name}.exe", binary_name]
    return [binary_name]


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def resolve_binary(binary_name: str) -> str | None:
    names = _binary_name_candidates(binary_name)
    search_dirs = _unique_paths([runtime_storage_dir(), app_dir(), appdata_dir()])
    for base in search_dirs:
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return str(candidate)

    for name in names:
        candidate = shutil.which(name)
        if candidate:
            return str(Path(candidate).resolve())
    return None
