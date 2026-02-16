from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

from .models import DependencyStatus
from .paths import resolve_binary, runtime_storage_dir

FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_FALLBACK_URL = "https://nodejs.org/dist/v20.19.0/node-v20.19.0-win-x64.zip"


class DependencyInstallCancelled(RuntimeError):
    pass


def _ensure_not_cancelled(cancel_token, dependency_name: str) -> None:                
    if cancel_token.is_set():
        raise DependencyInstallCancelled(f"{dependency_name} installation cancelled")


def _dependency_package_config(name: str) -> tuple[tuple[str, ...], str]:
    if name == "ffmpeg":
        return ("ffmpeg.exe", "ffprobe.exe"), FFMPEG_DOWNLOAD_URL
    return ("node.exe",), _choose_node_download_url()


def dependency_status() -> dict[str, DependencyStatus]:
    ffmpeg = resolve_binary("ffmpeg")
    node = resolve_binary("node")
    return {
        "ffmpeg": DependencyStatus(name="ffmpeg", installed=bool(ffmpeg), path=ffmpeg or ""),
        "node": DependencyStatus(name="node", installed=bool(node), path=node or ""),
    }


def _choose_node_download_url(timeout: float = 8.0) -> str:
    try:
        response = requests.get(NODE_INDEX_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                files = item.get("files")
                if not isinstance(files, list) or "win-x64-zip" not in files:
                    continue
                if not item.get("lts"):
                    continue
                version = str(item.get("version") or "").strip()
                if not version.startswith("v"):
                    continue
                return f"https://nodejs.org/dist/{version}/node-{version}-win-x64.zip"
    except Exception:
        pass
    return NODE_FALLBACK_URL


def _find_binary_under(path: Path, binary_name: str) -> Path | None:
    for file in path.rglob(binary_name):
        if file.is_file():
            return file
    return None


class DependencyService:
    def install_dependency(
        self,
        dependency_name: str,
        cancel_token,
        *,
        progress_cb: Callable[[int, str], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
    ) -> str:
        name = str(dependency_name or "").strip().lower()
        if name not in {"ffmpeg", "node"}:
            raise ValueError(f"Unsupported dependency: {dependency_name}")
        binary_names, download_url = _dependency_package_config(name)

        target_dir = runtime_storage_dir()
        if progress_cb:
            progress_cb(0, f"Preparing {name} installer")
        if log_cb:
            log_cb(f"Downloading {name} from {download_url}")

        with tempfile.TemporaryDirectory(prefix=f"mc_{name}_") as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            archive_path = temp_dir / f"{name}.zip"
            extract_dir = temp_dir / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)

            with requests.get(download_url, stream=True, timeout=20) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", "0") or 0)
                done = 0
                with archive_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        _ensure_not_cancelled(cancel_token, name)
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        if progress_cb and total > 0:
                            percent = min(70, int((done / total) * 70))
                            progress_cb(percent, f"Downloading {name} ({percent}%)")
            if log_cb:
                log_cb(f"Downloaded archive to {archive_path}")

            if log_cb:
                log_cb(f"Extracting {archive_path.name}")
            with zipfile.ZipFile(archive_path, "r") as zipped:
                members = zipped.infolist()
                total_members = len(members)
                total_extract_bytes = sum(
                    max(0, int(getattr(member, "file_size", 0) or 0))
                    for member in members
                    if not member.is_dir()
                )
                extracted_bytes = 0
                extracted_members = 0
                if progress_cb:
                    progress_cb(75, f"Extracting {name} (75%)")
                for member in members:
                    _ensure_not_cancelled(cancel_token, name)
                    zipped.extract(member, extract_dir)
                    extracted_members += 1
                    if member.is_dir():
                        if progress_cb and total_extract_bytes <= 0 and total_members > 0:
                            percent = 75 + int((extracted_members / total_members) * 24)
                            progress_cb(min(99, percent), f"Extracting {name} ({min(99, percent)}%)")
                        continue
                    extracted_bytes += max(0, int(getattr(member, "file_size", 0) or 0))
                    if progress_cb:
                        if total_extract_bytes > 0:
                            percent = 75 + int((extracted_bytes / total_extract_bytes) * 24)
                        elif total_members > 0:
                            percent = 75 + int((extracted_members / total_members) * 24)
                        else:
                            percent = 99
                        progress_cb(min(99, percent), f"Extracting {name} ({min(99, percent)}%)")

            _ensure_not_cancelled(cancel_token, name)

            located_binaries: dict[str, Path] = {}
            for binary_name in binary_names:
                found = _find_binary_under(extract_dir, binary_name)
                if not found:
                    raise FileNotFoundError(f"{binary_name} was not found in downloaded archive")
                located_binaries[binary_name] = found

            installed_paths: list[Path] = []
            for binary_name in binary_names:
                _ensure_not_cancelled(cancel_token, name)
                found = located_binaries[binary_name]
                target_binary = target_dir / binary_name
                shutil.copy2(found, target_binary)
                installed_paths.append(target_binary)
                if log_cb:
                    log_cb(f"Installed {binary_name} to {target_binary}")
            if progress_cb:
                progress_cb(99, f"Finalizing {name} (99%)")

            if progress_cb:
                progress_cb(100, f"{name} installed")
            return str(installed_paths[0])
