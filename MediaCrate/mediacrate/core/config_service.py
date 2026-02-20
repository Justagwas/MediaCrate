from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterator

from . import config as _config
from .models import AppConfig
from .paths import app_dir, appdata_dir, default_download_dir, runtime_storage_dir

# Backward-compatible re-exports for callers still importing config_service.
APP_NAME = _config.APP_NAME
APP_VERSION = _config.APP_VERSION
INNO_SETUP_APP_ID = _config.INNO_SETUP_APP_ID
PROJECT_BASE_URL = _config.PROJECT_BASE_URL
OFFICIAL_PAGE_URL = _config.OFFICIAL_PAGE_URL
UPDATE_MANIFEST_URL = _config.UPDATE_MANIFEST_URL
DEFAULT_DOWNLOAD_URL = _config.DEFAULT_DOWNLOAD_URL
DEFAULT_SETUP_UPDATE_URL = _config.DEFAULT_SETUP_UPDATE_URL
UPDATE_GITHUB_LATEST_URL = _config.UPDATE_GITHUB_LATEST_URL
UPDATE_GITHUB_DOWNLOAD_URL = _config.UPDATE_GITHUB_DOWNLOAD_URL
UPDATE_SOURCEFORGE_RSS_URL = _config.UPDATE_SOURCEFORGE_RSS_URL

CONFIG_FILENAME = _config.CONFIG_FILENAME
LEGACY_CONFIG_FILENAMES = _config.LEGACY_CONFIG_FILENAMES
CONFIG_SCHEMA_VERSION = _config.CONFIG_SCHEMA_VERSION

THEME_VALUES = _config.THEME_VALUES
UI_SCALE_MIN = _config.UI_SCALE_MIN
UI_SCALE_MAX = _config.UI_SCALE_MAX
UI_SCALE_STEP = _config.UI_SCALE_STEP
BATCH_CONCURRENCY_MIN = _config.BATCH_CONCURRENCY_MIN
BATCH_CONCURRENCY_MAX = _config.BATCH_CONCURRENCY_MAX
BATCH_RETRY_COUNT_MIN = _config.BATCH_RETRY_COUNT_MIN
BATCH_RETRY_COUNT_MAX = _config.BATCH_RETRY_COUNT_MAX
SPEED_LIMIT_KBPS_MIN = _config.SPEED_LIMIT_KBPS_MIN
SPEED_LIMIT_KBPS_MAX = _config.SPEED_LIMIT_KBPS_MAX
LEGACY_UNLIMITED_SPEED_SENTINEL_KBPS = _config.LEGACY_UNLIMITED_SPEED_SENTINEL_KBPS
STALE_PART_CLEANUP_HOURS_MIN = _config.STALE_PART_CLEANUP_HOURS_MIN
STALE_PART_CLEANUP_HOURS_MAX = _config.STALE_PART_CLEANUP_HOURS_MAX
CONFLICT_VALUES = _config.CONFLICT_VALUES
RETRY_PROFILE_VALUES = _config.RETRY_PROFILE_VALUES
DEFAULT_FILENAME_TEMPLATE = _config.DEFAULT_FILENAME_TEMPLATE

config_to_dict = _config.config_to_dict
_sanitize_payload = _config._sanitize_payload
_CONFIG_PATH_PATCH_LOCK = threading.RLock()


class _PathsProxy:
    @staticmethod
    def runtime_storage_dir():
        return runtime_storage_dir()

    @staticmethod
    def app_dir():
        return app_dir()

    @staticmethod
    def appdata_dir():
        return appdata_dir()

    @staticmethod
    def default_download_dir():
        return default_download_dir()


@contextmanager
def _using_config_paths() -> Iterator[None]:
    # Route config.py path lookups through this module so existing tests/mocks
    # that patch config_service.* path helpers keep working.
    with _CONFIG_PATH_PATCH_LOCK:
        original = _config._paths
        _config._paths = lambda: _PathsProxy  # type: ignore[assignment]
        try:
            yield
        finally:
            _config._paths = original  # type: ignore[assignment]


def default_config() -> AppConfig:
    with _using_config_paths():
        return _config.default_config()


def config_path():
    with _using_config_paths():
        return _config.config_path()


def _legacy_config_candidates():
    with _using_config_paths():
        return _config._legacy_config_candidates()


def _load_config_from_path(path):
    return _config._load_config_from_path(path)


def load_config() -> AppConfig:
    with _using_config_paths():
        return _config.load_config()


def save_config(config: AppConfig) -> str | None:
    with _using_config_paths():
        return _config.save_config(config)
