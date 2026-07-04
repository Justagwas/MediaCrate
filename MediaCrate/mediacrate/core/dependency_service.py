from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import shutil
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zipfile import ZipFile, ZipInfo

import requests

from .node_runtime import NODE_MIN_MAJOR_VERSION, is_supported_node_runtime
from .models import DependencyStatus
from .paths import resolve_binary, runtime_storage_dir

DEPENDENCY_MANIFEST_FILENAME = "dependency_manifest.json"
COPY_CHUNK_SIZE = 1024 * 256
DEPENDENCY_DOWNLOAD_TIMEOUT_SECONDS = 10 * 60
DEPENDENCY_DOWNLOAD_EXTRA_BYTES = 1024 * 1024
DEPENDENCY_MAX_REDIRECTS = 3
DEPENDENCY_ALLOWED_HOSTS = {
    "downloads.justagwas.com",
    "nodejs.org",
    "www.nodejs.org",
}


class DependencyInstallCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DependencyPackage:
    name: str
    version: str
    url: str
    sha256: str
    binaries: tuple[str, ...]
    platform: str
    arch: str
    install_mode: str
    max_members: int
    max_extract_bytes: int


def _ensure_not_cancelled(cancel_token, dependency_name: str) -> None:                
    if cancel_token.is_set():
        raise DependencyInstallCancelled(f"{dependency_name} installation cancelled")


def _manifest_path() -> Path:
    return Path(__file__).with_name(DEPENDENCY_MANIFEST_FILENAME)


def _load_dependency_manifest(path: Path | None = None) -> dict[str, object]:
    manifest_path = path or _manifest_path()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Dependency manifest is missing or invalid: {manifest_path}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Dependency manifest must contain a JSON object.")
    return raw


def _dependency_url_allowed(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = str(parsed.hostname or "").strip().lower()
    return bool(host and host in DEPENDENCY_ALLOWED_HOSTS)


def _dependency_redirect_target(response: requests.Response, current_url: str) -> str:
    location = str(response.headers.get("location") or "").strip()
    if not location:
        return ""
    return urljoin(str(current_url or ""), location)


def _open_dependency_response(url: str) -> requests.Response:
    current_url = str(url or "").strip()
    if not _dependency_url_allowed(current_url):
        raise RuntimeError("Dependency manifest URL is missing or untrusted.")
    for _attempt in range(DEPENDENCY_MAX_REDIRECTS + 1):
        if not _dependency_url_allowed(current_url):
            raise RuntimeError("Dependency download redirected to an untrusted host.")
        response = requests.get(
            current_url,
            stream=True,
            timeout=20,
            allow_redirects=False,
        )
        if not (300 <= int(response.status_code) < 400):
            final_url = str(response.url or current_url)
            if not _dependency_url_allowed(final_url):
                response.close()
                raise RuntimeError("Dependency download redirected to an untrusted host.")
            return response
        next_url = _dependency_redirect_target(response, current_url)
        response.close()
        if not next_url:
            raise RuntimeError("Dependency download redirect did not include a target URL.")
        current_url = next_url
    raise RuntimeError("Dependency download redirected too many times.")


def _package_from_payload(name: str, payload: object) -> DependencyPackage:
    if not isinstance(payload, dict):
        raise RuntimeError(f"Dependency manifest entry is invalid for {name}.")
    package_name = str(payload.get("name") or name).strip().lower()
    url = str(payload.get("url") or "").strip()
    sha256 = str(payload.get("sha256") or "").strip().lower()
    target_platform = str(payload.get("platform") or "").strip().lower()
    arch = str(payload.get("arch") or "").strip().lower()
    install_mode = str(payload.get("install_mode") or "").strip().lower()
    binaries = tuple(
        str(item or "").strip()
        for item in (payload.get("binaries") or [])
        if str(item or "").strip()
    )
    try:
        max_members = max(1, int(payload.get("max_members", 4096)))
        max_extract_bytes = max(1, int(payload.get("max_extract_bytes", 512 * 1024 * 1024)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Dependency manifest limits are invalid for {name}.") from exc
    if (
        package_name != name
        or not _dependency_url_allowed(url)
        or len(sha256) != 64
        or not binaries
        or not target_platform
        or not arch
        or not install_mode
    ):
        raise RuntimeError(f"Dependency manifest entry is incomplete for {name}.")
    return DependencyPackage(
        name=name,
        version=str(payload.get("version") or "").strip(),
        url=url,
        sha256=sha256,
        binaries=binaries,
        platform=target_platform,
        arch=arch,
        install_mode=install_mode,
        max_members=max_members,
        max_extract_bytes=max_extract_bytes,
    )


def _dependency_package_config(name: str, manifest_path: Path | None = None) -> DependencyPackage:
    manifest = _load_dependency_manifest(manifest_path)
    payload = manifest.get(name)
    return _package_from_payload(name, payload)


def _current_platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform.lower()


def _current_arch_key() -> str:
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86", "i386", "i686"}:
        return "x86"
    return machine


def _verify_supported_package_runtime(package: DependencyPackage) -> None:
    current_platform = _current_platform_key()
    current_arch = _current_arch_key()
    if package.platform != current_platform or package.arch != current_arch:
        raise RuntimeError(
            f"{package.name} installer is for {package.platform}/{package.arch}; "
            f"this runtime is {current_platform}/{current_arch}."
        )
    if package.install_mode not in {"binary-copy", "single-binary"}:
        raise RuntimeError(f"{package.name} dependency manifest has unsupported install_mode.")


def _ensure_download_deadline(deadline: float, dependency_name: str) -> None:
    if time.monotonic() > deadline:
        raise TimeoutError(f"{dependency_name} download timed out.")


def _safe_content_length(value: object) -> int:
    try:
        parsed = int(str(value or "").strip() or "0")
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _archive_download_limit_bytes(package: DependencyPackage) -> int:
    return max(1, int(package.max_extract_bytes) + DEPENDENCY_DOWNLOAD_EXTRA_BYTES)


def _is_supported_node_binary(path: str) -> bool:
    return is_supported_node_runtime(path, minimum_major=NODE_MIN_MAJOR_VERSION)


def dependency_status() -> dict[str, DependencyStatus]:
    ffmpeg = resolve_binary("ffmpeg")
    node = resolve_binary("node")
    return {
        "ffmpeg": DependencyStatus(name="ffmpeg", installed=bool(ffmpeg), path=ffmpeg or ""),
        "node": DependencyStatus(
            name="node",
            installed=bool(node and _is_supported_node_binary(node)),
            path=node or "",
        ),
    }


def _find_binaries_under(path: Path, binary_names: tuple[str, ...]) -> dict[str, Path]:
    wanted = {str(name or "").strip().lower(): str(name or "").strip() for name in binary_names if str(name or "").strip()}
    found: dict[str, Path] = {}
    if not wanted:
        return found
    for file in path.rglob("*"):
        if not file.is_file():
            continue
        key = file.name.lower()
        original_name = wanted.get(key)
        if original_name and original_name not in found:
            found[original_name] = file
            if len(found) == len(wanted):
                break
    return found


def _sha256_file(path: Path, cancel_token, dependency_name: str) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            _ensure_not_cancelled(cancel_token, dependency_name)
            chunk = handle.read(COPY_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().lower()


def _verify_archive_hash(path: Path, *, expected_sha256: str, cancel_token, dependency_name: str) -> None:
    expected = str(expected_sha256 or "").strip().lower()
    if len(expected) != 64:
        raise RuntimeError(f"{dependency_name} dependency manifest has no valid SHA256.")
    actual = _sha256_file(path, cancel_token, dependency_name)
    if actual != expected:
        raise RuntimeError(
            f"{dependency_name} archive SHA256 mismatch. Expected {expected}, got {actual}."
        )


def _is_zip_symlink(member: ZipInfo) -> bool:
    mode = (int(member.external_attr) >> 16) & 0o170000
    return mode == stat.S_IFLNK


def _safe_member_target(extract_root: Path, member: ZipInfo) -> Path:
    raw_name = str(member.filename or "").replace("\\", "/")
    if not raw_name or raw_name.startswith("/"):
        raise RuntimeError(f"Unsafe archive member path: {member.filename}")
    member_path = Path(raw_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise RuntimeError(f"Unsafe archive member path: {member.filename}")
    target = (extract_root / member_path).resolve()
    root = extract_root.resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        raise RuntimeError(f"Archive member escapes extraction directory: {member.filename}")
    if _is_zip_symlink(member):
        raise RuntimeError(f"Archive symlinks are not supported: {member.filename}")
    return target


def _safe_extract_zip(
    zipped: ZipFile,
    extract_dir: Path,
    *,
    dependency_name: str,
    cancel_token,
    max_members: int,
    max_extract_bytes: int,
    progress_cb: Callable[[int, str], None] | None = None,
) -> None:
    members = zipped.infolist()
    if len(members) > max_members:
        raise RuntimeError(f"{dependency_name} archive contains too many files.")
    total_extract_bytes = 0
    for member in members:
        if not member.is_dir():
            total_extract_bytes += max(0, int(member.file_size or 0))
        if total_extract_bytes > max_extract_bytes:
            raise RuntimeError(f"{dependency_name} archive is larger than the allowed extraction limit.")

    extracted_bytes = 0
    for member in members:
        _ensure_not_cancelled(cancel_token, dependency_name)
        target = _safe_member_target(extract_dir, member)
        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zipped.open(member, "r") as source, target.open("wb") as destination:
            while True:
                _ensure_not_cancelled(cancel_token, dependency_name)
                chunk = source.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
                destination.write(chunk)
                extracted_bytes += len(chunk)
                if extracted_bytes > max_extract_bytes:
                    raise RuntimeError(f"{dependency_name} archive exceeded the extraction limit.")
                if progress_cb and total_extract_bytes > 0:
                    percent = 75 + int((extracted_bytes / total_extract_bytes) * 24)
                    progress_cb(min(99, percent), f"Extracting {dependency_name} ({min(99, percent)}%)")


class DependencyService:
    def __init__(self, *, manifest_path: Path | None = None) -> None:
        self._manifest_path = manifest_path

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
        package = _dependency_package_config(name, self._manifest_path)
        _verify_supported_package_runtime(package)

        target_dir = runtime_storage_dir()
        if progress_cb:
            progress_cb(0, f"Preparing {name} installer")
        if log_cb:
            version_suffix = f" {package.version}" if package.version else ""
            log_cb(f"Downloading {name}{version_suffix} from {package.url}")

        with tempfile.TemporaryDirectory(prefix=f"mc_{name}_") as temp_dir_raw:
            temp_dir = Path(temp_dir_raw)
            archive_path = temp_dir / f"{name}.zip"
            extract_dir = temp_dir / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)

            download_deadline = time.monotonic() + DEPENDENCY_DOWNLOAD_TIMEOUT_SECONDS
            with _open_dependency_response(package.url) as response:
                response.raise_for_status()
                total = _safe_content_length(response.headers.get("content-length"))
                max_download_bytes = _archive_download_limit_bytes(package)
                if total > max_download_bytes:
                    raise RuntimeError(f"{name} archive is larger than the allowed download limit.")
                done = 0
                with archive_path.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        _ensure_not_cancelled(cancel_token, name)
                        _ensure_download_deadline(download_deadline, name)
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        if done > max_download_bytes:
                            raise RuntimeError(f"{name} archive exceeded the allowed download limit.")
                        if progress_cb and total > 0:
                            percent = min(70, int((done / total) * 70))
                            progress_cb(percent, f"Downloading {name} ({percent}%)")
            if log_cb:
                log_cb(f"Downloaded archive to {archive_path}")

            if progress_cb:
                progress_cb(72, f"Verifying {name} archive")
            _verify_archive_hash(
                archive_path,
                expected_sha256=package.sha256,
                cancel_token=cancel_token,
                dependency_name=name,
            )
            if log_cb:
                log_cb(f"Verified SHA256 for {archive_path.name}")

            if log_cb:
                log_cb(f"Extracting {archive_path.name}")
            with zipfile.ZipFile(archive_path, "r") as zipped:
                if progress_cb:
                    progress_cb(75, f"Extracting {name} (75%)")
                _safe_extract_zip(
                    zipped,
                    extract_dir,
                    dependency_name=name,
                    cancel_token=cancel_token,
                    max_members=package.max_members,
                    max_extract_bytes=package.max_extract_bytes,
                    progress_cb=progress_cb,
                )

            _ensure_not_cancelled(cancel_token, name)

            located_binaries = _find_binaries_under(extract_dir, package.binaries)
            for binary_name in package.binaries:
                if binary_name not in located_binaries:
                    raise FileNotFoundError(f"{binary_name} was not found in downloaded archive")

            installed_paths: list[Path] = []
            for binary_name in package.binaries:
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
