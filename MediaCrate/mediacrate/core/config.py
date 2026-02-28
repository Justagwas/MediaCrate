from __future__ import annotations

import json
import os
from pathlib import Path

from .models import AppConfig, RetryProfile

APP_NAME = "MediaCrate"
APP_VERSION = "2.1.1"
INNO_SETUP_APP_ID = "MediaCrateJustagwas"
PROJECT_BASE_URL = "https://www.justagwas.com/projects/mediacrate"
OFFICIAL_PAGE_URL = PROJECT_BASE_URL
UPDATE_MANIFEST_URL = f"{PROJECT_BASE_URL}/latest.json"
DEFAULT_DOWNLOAD_URL = f"{PROJECT_BASE_URL}/download"
DEFAULT_SETUP_UPDATE_URL = "https://downloads.justagwas.com/mediacrate/MediaCrateSetup.exe"
UPDATE_GITHUB_LATEST_URL = "https://github.com/Justagwas/mediacrate/releases/latest"
UPDATE_GITHUB_DOWNLOAD_URL = "https://github.com/Justagwas/mediacrate/releases/latest/download/MediaCrateSetup.exe"
UPDATE_SOURCEFORGE_RSS_URL = "https://sourceforge.net/projects/mediacrate/rss?path=/"

CONFIG_FILENAME = "MediaCrate_config.json"
LEGACY_CONFIG_FILENAMES = ("MediaCrate_config_v2.json",)
CONFIG_SCHEMA_VERSION = 10

THEME_VALUES = {"dark", "light"}
UI_SCALE_MIN = 75
UI_SCALE_MAX = 200
UI_SCALE_STEP = 5
BATCH_CONCURRENCY_MIN = 1
BATCH_CONCURRENCY_MAX = 16
BACKGROUND_WORKER_THREADS_MIN = 1
BACKGROUND_WORKER_THREADS_MAX = 32
BATCH_RETRY_COUNT_MIN = 0
BATCH_RETRY_COUNT_MAX = 3
SPEED_LIMIT_KBPS_MIN = 0
SPEED_LIMIT_KBPS_MAX = 100000
LEGACY_UNLIMITED_SPEED_SENTINEL_KBPS = 50000
STALE_PART_CLEANUP_HOURS_MIN = 0
STALE_PART_CLEANUP_HOURS_MAX = 24 * 30
CONFLICT_VALUES = {"skip", "rename", "overwrite"}
RETRY_PROFILE_VALUES = {item.value for item in RetryProfile}
DEFAULT_FILENAME_TEMPLATE = "%(title).130B [%(mc_quality)s] [%(id)s].%(ext)s"


def _paths():
    from . import paths as paths_module

    return paths_module


def _coerce_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_ui_scale(value: object, default: int) -> int:
    parsed = _coerce_int(value, default, UI_SCALE_MIN, UI_SCALE_MAX)
    snapped = int(round(parsed / UI_SCALE_STEP) * UI_SCALE_STEP)
    return max(UI_SCALE_MIN, min(UI_SCALE_MAX, snapped))


def _coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _sanitize_speed_limit_kbps(value: object, *, schema_version: int, default: int) -> int:
    parsed = _coerce_int(value, default, SPEED_LIMIT_KBPS_MIN, SPEED_LIMIT_KBPS_MAX)
    if schema_version < CONFIG_SCHEMA_VERSION and parsed == LEGACY_UNLIMITED_SPEED_SENTINEL_KBPS:
        return 0
    return parsed


def _coerce_non_empty_text(value: object, *, default: str) -> str:
    text = str(value or "").strip()
    return text if text else str(default)


def _default_background_worker_threads() -> int:
    cpu_count = os.cpu_count() or 4
    return max(BACKGROUND_WORKER_THREADS_MIN, min(BACKGROUND_WORKER_THREADS_MAX, int(cpu_count)))


def default_config() -> AppConfig:
    return AppConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        theme_mode="dark",
        ui_scale_percent=100,
        download_location=str(_paths().default_download_dir()),
        batch_enabled=False,
        batch_concurrency=4,
        skip_existing_files=True,
        auto_start_ready_links=False,
        batch_retry_count=0,
        filename_template=DEFAULT_FILENAME_TEMPLATE,
        conflict_policy="skip",
        download_speed_limit_kbps=0,
        adaptive_batch_concurrency=True,
        auto_check_updates=True,
        background_worker_threads=_default_background_worker_threads(),
        window_geometry="",
        disable_metadata_fetch=False,
        disable_history=False,
        retry_profile=RetryProfile.BASIC.value,
        fallback_download_on_metadata_error=True,
        accurate_size_enabled=False,
        save_metadata_to_file=False,
        retain_format_selection_enabled=True,
        saved_format_choice="VIDEO",
        saved_quality_choice="BEST QUALITY",
        stale_part_cleanup_hours=48,
    )


def _sanitize_payload(payload: dict[str, object]) -> AppConfig:
    defaults = default_config()
    payload_schema = _coerce_int(payload.get("schema_version", 0), 0, 0, CONFIG_SCHEMA_VERSION)

    theme_mode = str(payload.get("theme_mode", defaults.theme_mode)).strip().lower()
    if theme_mode not in THEME_VALUES:
        theme_mode = defaults.theme_mode
    download_location = _coerce_non_empty_text(
        payload.get("download_location", defaults.download_location),
        default=defaults.download_location,
    )
    filename_template = _coerce_non_empty_text(
        payload.get("filename_template", defaults.filename_template),
        default=defaults.filename_template,
    )
    conflict_policy = str(payload.get("conflict_policy", defaults.conflict_policy) or "").strip().lower()
    if conflict_policy not in CONFLICT_VALUES:
        conflict_policy = defaults.conflict_policy
    retry_profile = str(payload.get("retry_profile", defaults.retry_profile) or "").strip().lower()
    if retry_profile not in RETRY_PROFILE_VALUES:
        retry_profile = defaults.retry_profile
    saved_format_choice = _coerce_non_empty_text(
        payload.get("saved_format_choice", defaults.saved_format_choice),
        default=defaults.saved_format_choice,
    )
    saved_quality_choice = _coerce_non_empty_text(
        payload.get("saved_quality_choice", defaults.saved_quality_choice),
        default=defaults.saved_quality_choice,
    )

    return AppConfig(
        schema_version=CONFIG_SCHEMA_VERSION,
        theme_mode=theme_mode,
        ui_scale_percent=_coerce_ui_scale(
            payload.get("ui_scale_percent", defaults.ui_scale_percent),
            defaults.ui_scale_percent,
        ),
        download_location=download_location,
        batch_enabled=_coerce_bool(payload.get("batch_enabled"), default=defaults.batch_enabled),
        batch_concurrency=_coerce_int(
            payload.get("batch_concurrency", defaults.batch_concurrency),
            defaults.batch_concurrency,
            BATCH_CONCURRENCY_MIN,
            BATCH_CONCURRENCY_MAX,
        ),
        skip_existing_files=_coerce_bool(
            payload.get("skip_existing_files"), default=defaults.skip_existing_files
        ),
        auto_start_ready_links=_coerce_bool(
            payload.get("auto_start_ready_links"),
            default=defaults.auto_start_ready_links,
        ),
        batch_retry_count=_coerce_int(
            payload.get("batch_retry_count", defaults.batch_retry_count),
            defaults.batch_retry_count,
            BATCH_RETRY_COUNT_MIN,
            BATCH_RETRY_COUNT_MAX,
        ),
        filename_template=filename_template,
        conflict_policy=conflict_policy,
        download_speed_limit_kbps=_sanitize_speed_limit_kbps(
            payload.get("download_speed_limit_kbps", defaults.download_speed_limit_kbps),
            schema_version=payload_schema,
            default=defaults.download_speed_limit_kbps,
        ),
        adaptive_batch_concurrency=_coerce_bool(
            payload.get("adaptive_batch_concurrency"),
            default=defaults.adaptive_batch_concurrency,
        ),
        auto_check_updates=_coerce_bool(
            payload.get("auto_check_updates"), default=defaults.auto_check_updates
        ),
        background_worker_threads=_coerce_int(
            payload.get("background_worker_threads", defaults.background_worker_threads),
            defaults.background_worker_threads,
            BACKGROUND_WORKER_THREADS_MIN,
            BACKGROUND_WORKER_THREADS_MAX,
        ),
        window_geometry=str(payload.get("window_geometry", defaults.window_geometry) or ""),
        disable_metadata_fetch=_coerce_bool(
            payload.get("disable_metadata_fetch"),
            default=defaults.disable_metadata_fetch,
        ),
        disable_history=_coerce_bool(
            payload.get("disable_history"),
            default=defaults.disable_history,
        ),
        retry_profile=retry_profile,
        fallback_download_on_metadata_error=_coerce_bool(
            payload.get("fallback_download_on_metadata_error"),
            default=defaults.fallback_download_on_metadata_error,
        ),
        accurate_size_enabled=_coerce_bool(
            payload.get("accurate_size_enabled"),
            default=defaults.accurate_size_enabled,
        ),
        save_metadata_to_file=_coerce_bool(
            payload.get("save_metadata_to_file"),
            default=defaults.save_metadata_to_file,
        ),
        retain_format_selection_enabled=_coerce_bool(
            payload.get("retain_format_selection_enabled"),
            default=defaults.retain_format_selection_enabled,
        ),
        saved_format_choice=str(saved_format_choice).strip().upper() or "VIDEO",
        saved_quality_choice=str(saved_quality_choice).strip().upper() or "BEST QUALITY",
        stale_part_cleanup_hours=_coerce_int(
            payload.get("stale_part_cleanup_hours", defaults.stale_part_cleanup_hours),
            defaults.stale_part_cleanup_hours,
            STALE_PART_CLEANUP_HOURS_MIN,
            STALE_PART_CLEANUP_HOURS_MAX,
        ),
    )


def config_path() -> Path:
    return _paths().runtime_storage_dir() / CONFIG_FILENAME


def _legacy_config_candidates() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    paths_module = _paths()
    bases = [
        paths_module.runtime_storage_dir(),
        paths_module.app_dir(),
        paths_module.appdata_dir(),
    ]
    for base in bases:
        for filename in LEGACY_CONFIG_FILENAMES:
            path = base / filename
            if path in seen:
                continue
            seen.add(path)
            candidates.append(path)
    return candidates


def _load_config_from_path(path: Path) -> AppConfig | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return _sanitize_payload(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def load_config() -> AppConfig:
    primary = config_path()
    if primary.exists():
        loaded = _load_config_from_path(primary)
        if loaded is not None:
            return loaded

    for legacy in _legacy_config_candidates():
        if not legacy.exists():
            continue
        loaded = _load_config_from_path(legacy)
        if loaded is None:
            continue
        save_config(loaded)
        return loaded
    return default_config()


def config_to_dict(config: AppConfig) -> dict[str, object]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "theme_mode": config.theme_mode,
        "ui_scale_percent": int(config.ui_scale_percent),
        "download_location": str(config.download_location),
        "batch_enabled": bool(config.batch_enabled),
        "batch_concurrency": int(config.batch_concurrency),
        "skip_existing_files": bool(config.skip_existing_files),
        "auto_start_ready_links": bool(config.auto_start_ready_links),
        "batch_retry_count": int(config.batch_retry_count),
        "filename_template": str(config.filename_template or DEFAULT_FILENAME_TEMPLATE),
        "conflict_policy": str(config.conflict_policy or "skip"),
        "download_speed_limit_kbps": int(config.download_speed_limit_kbps),
        "adaptive_batch_concurrency": bool(config.adaptive_batch_concurrency),
        "auto_check_updates": bool(config.auto_check_updates),
        "background_worker_threads": int(config.background_worker_threads),
        "window_geometry": str(config.window_geometry or ""),
        "disable_metadata_fetch": bool(config.disable_metadata_fetch),
        "disable_history": bool(config.disable_history),
        "retry_profile": str(config.retry_profile or RetryProfile.BASIC.value),
        "fallback_download_on_metadata_error": bool(config.fallback_download_on_metadata_error),
        "accurate_size_enabled": bool(config.accurate_size_enabled),
        "save_metadata_to_file": bool(config.save_metadata_to_file),
        "retain_format_selection_enabled": bool(config.retain_format_selection_enabled),
        "saved_format_choice": str(config.saved_format_choice or "VIDEO"),
        "saved_quality_choice": str(config.saved_quality_choice or "BEST QUALITY"),
        "stale_part_cleanup_hours": int(config.stale_part_cleanup_hours),
    }


def save_config(config: AppConfig) -> str | None:
    payload = config_to_dict(config)
    path = config_path()
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(str(tmp_path), str(path))
        return str(path)
    except (OSError, TypeError, ValueError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
