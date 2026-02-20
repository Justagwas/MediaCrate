from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from threading import Event

from .config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_DOWNLOAD_URL,
    DEFAULT_SETUP_UPDATE_URL,
    INNO_SETUP_APP_ID,
    UPDATE_MANIFEST_URL,
)
from .models import UpdateCheckResult
from .paths import app_dir
from .self_updater import (
    PreparedUpdateInstall,
    SelfUpdater,
    UpdateCheckData,
    normalize_version,
    parse_semver,
    is_newer_version,
)


def _update_storage_root() -> Path:
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        try:
            return (Path(local_app_data).resolve() / APP_NAME)
        except Exception:
            pass
    return app_dir()


class UpdateService:
    def __init__(self) -> None:
        executable_name = "MediaCrate.exe" if sys.platform == "win32" else "MediaCrate"
        self._updater = SelfUpdater(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            manifest_url=UPDATE_MANIFEST_URL,
            page_url=DEFAULT_DOWNLOAD_URL,
            setup_url=DEFAULT_SETUP_UPDATE_URL,
            installer_app_id=INNO_SETUP_APP_ID,
            executable_name=executable_name,
            install_dir=app_dir(),
            runtime_storage_dir=_update_storage_root(),
        )
        try:
            threading.Thread(
                target=self._updater.recover_pending_update,
                name="mediacrate-update-cleanup",
                daemon=True,
            ).start()
        except Exception:
            self._updater.recover_pending_update()

    def check_for_updates(
        self,
        current_version: str,
        *,
        stop_event: Event | None = None,
    ) -> UpdateCheckResult:
        try:
            check = self._updater.check_for_updates(current_version, stop_event=stop_event)
        except InterruptedError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Unable to fetch update metadata from latest.json. {exc}") from exc

        return UpdateCheckResult(
            update_available=bool(check.update_available),
            current_version=str(check.current_version or ""),
            latest_version=str(check.latest_version or ""),
            download_url=str(check.page_url or ""),
            setup_url=str(check.setup_url or ""),
            setup_sha256=str(check.setup_sha256 or ""),
            setup_size=int(check.setup_size or 0),
            released=str(check.released or ""),
            notes=list(check.notes or []),
            install_supported=bool(check.install_supported),
            channel=str(check.channel or "stable"),
            minimum_supported_version=str(check.minimum_supported_version or "1.0.0"),
            requires_manual_update=bool(check.requires_manual_update),
            source=str(check.source or "latest.json"),
            error="",
        )

    def install_update(
        self,
        check_result: UpdateCheckResult,
        *,
        stop_event: Event | None = None,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> str:
        if not check_result.update_available:
            raise RuntimeError("No update is available.")
        if not check_result.setup_url:
            raise RuntimeError("No setup installer URL was provided.")

        return self._updater.install_update(
            self._to_check_data(check_result),
            stop_event=stop_event,
            progress_cb=progress_cb,
        )

    def prepare_update(
        self,
        check_result: UpdateCheckResult,
        *,
        stop_event: Event | None = None,
        progress_cb: Callable[[int, str], None] | None = None,
    ) -> PreparedUpdateInstall:
        if not check_result.update_available:
            raise RuntimeError("No update is available.")
        if not check_result.setup_url:
            raise RuntimeError("No setup installer URL was provided.")
        return self._updater.prepare_update(
            self._to_check_data(check_result),
            stop_event=stop_event,
            progress_cb=progress_cb,
        )

    def launch_prepared_update(
        self,
        prepared: PreparedUpdateInstall,
        *,
        restart_after_update: bool,
    ) -> None:
        self._updater.launch_prepared_update(
            prepared,
            restart_after_update=bool(restart_after_update),
        )

    def discard_prepared_update(self, prepared: PreparedUpdateInstall) -> None:
        self._updater.discard_prepared_update(prepared)

    @staticmethod
    def _to_check_data(result: UpdateCheckResult) -> UpdateCheckData:
        return UpdateCheckData(
            update_available=bool(result.update_available),
            current_version=str(result.current_version or ""),
            latest_version=str(result.latest_version or ""),
            page_url=str(result.download_url or ""),
            setup_url=str(result.setup_url or ""),
            setup_sha256=str(result.setup_sha256 or ""),
            setup_size=int(result.setup_size or 0),
            released=str(result.released or ""),
            notes=list(result.notes or []),
            source=str(result.source or "latest.json"),
            channel=str(result.channel or "stable"),
            minimum_supported_version=str(result.minimum_supported_version or "1.0.0"),
            requires_manual_update=bool(result.requires_manual_update),
            setup_managed_install=bool(result.install_supported),
        )


__all__ = [
    "UpdateService",
    "is_newer_version",
    "normalize_version",
    "parse_semver",
]
