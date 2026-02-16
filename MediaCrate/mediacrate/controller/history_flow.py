from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Protocol

from PySide6.QtWidgets import QMessageBox

from ..core.download_service import validate_url
from ..core.models import DownloadHistoryEntry


class HistoryFlowConfig(Protocol):
    download_location: str
    batch_enabled: bool


class HistoryFlowWindow(Protocol):
    def append_log(self, text: str) -> None: ...

    def set_single_url_text(self, text: str) -> None: ...


class HistoryFlowController(Protocol):
    config: HistoryFlowConfig
    window: HistoryFlowWindow
    _download_history: list[DownloadHistoryEntry]

    def _show_warning(self, title: str, text: str) -> int: ...

    def _show_info(self, title: str, text: str) -> int: ...

    def _is_download_running(self) -> bool: ...

    def _add_batch_urls(self, values: list[str]) -> None: ...

    def start_downloads(self) -> None: ...

    def _ask_yes_no(
        self,
        title: str,
        text: str,
        *,
        default_button: QMessageBox.StandardButton = QMessageBox.NoButton,
    ) -> int: ...

    def _collect_unfinished_history_part_paths(self, entries: list[DownloadHistoryEntry]) -> list[Path]: ...

    def _cleanup_unfinished_part_files_from_history_entries(self, entries: list[DownloadHistoryEntry]) -> int: ...

    def _clear_history_file(self) -> None: ...

    def _refresh_history_view(self) -> None: ...


class HistoryFlow:
    @staticmethod
    def open_path_in_shell(controller: HistoryFlowController, path: Path, *, failure_title: str) -> bool:
        try:
            if os.name == "nt":
                os.startfile(str(path))
            else:
                webbrowser.open(path.as_uri())
            return True
        except Exception as exc:
            controller._show_warning(failure_title, str(exc))
            return False

    @staticmethod
    def resolve_history_target(
        controller: HistoryFlowController,
        path_value: str,
        *,
        action_title: str,
    ) -> Path | None:
        raw = str(path_value or "").strip()
        if not raw:
            controller._show_warning(action_title, "This history entry does not have a saved output path.")
            return None
        return Path(raw).expanduser()

    @staticmethod
    def open_downloads_folder(controller: HistoryFlowController) -> None:
        path = Path(controller.config.download_location).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            controller._show_warning("Open folder failed", str(exc))
            return
        HistoryFlow.open_path_in_shell(controller, path, failure_title="Open folder failed")

    @staticmethod
    def on_history_open_file(controller: HistoryFlowController, path_value: str) -> None:
        path = HistoryFlow.resolve_history_target(controller, path_value, action_title="Open file")
        if path is None:
            return
        if not path.exists() or not path.is_file():
            controller._show_warning("Open file", "The selected file no longer exists.")
            return
        HistoryFlow.open_path_in_shell(controller, path, failure_title="Open file failed")

    @staticmethod
    def on_history_open_folder(controller: HistoryFlowController, path_value: str) -> None:
        path = HistoryFlow.resolve_history_target(controller, path_value, action_title="Open folder")
        if path is None:
            return
        folder = path.parent if path.suffix else path
        if not folder.exists():
            controller._show_warning("Open folder", "The folder no longer exists.")
            return
        HistoryFlow.open_path_in_shell(controller, folder, failure_title="Open folder failed")

    @staticmethod
    def on_history_retry_url(controller: HistoryFlowController, url_value: str) -> None:
        url = str(url_value or "").strip()
        if not validate_url(url):
            controller._show_warning("Retry URL", "The selected history URL is invalid.")
            return
        if controller._is_download_running():
            controller._show_info("Download in progress", "Wait for the current download to finish.")
            return
        if controller.config.batch_enabled:
            controller._add_batch_urls([url])
            controller.window.append_log("History URL added to queue.")
            return
        controller.window.set_single_url_text(url)
        controller.start_downloads()

    @staticmethod
    def confirm_clear_history(controller: HistoryFlowController) -> bool:
        base_text = (
            "This will clear all history entries."
            "\n\nUnfinished history items may have matching .part files."
            "\nMatching .part files will be deleted if found."
        )
        first = controller._ask_yes_no(
            "Clear history",
            base_text,
            default_button=QMessageBox.No,
        )
        if first != QMessageBox.Yes:
            return False
        second = controller._ask_yes_no(
            "Confirm clear history",
            "This action cannot be undone.\n\nClear history now?",
            default_button=QMessageBox.No,
        )
        return second == QMessageBox.Yes

    @staticmethod
    def on_history_clear(controller: HistoryFlowController) -> None:
        if not HistoryFlow.confirm_clear_history(controller):
            return
        deleted_count = controller._cleanup_unfinished_part_files_from_history_entries(controller._download_history)
        controller._download_history = []
        controller._clear_history_file()
        controller._refresh_history_view()
        if deleted_count > 0:
            controller.window.append_log(f"Cleared history and deleted {deleted_count} unfinished .part file(s).")
        else:
            controller.window.append_log("Cleared history.")
