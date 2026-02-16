from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from threading import Event
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .app_metadata import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_DOWNLOAD_URL,
    UPDATE_GITHUB_DOWNLOAD_URL,
    UPDATE_GITHUB_LATEST_URL,
    UPDATE_MANIFEST_URL,
    UPDATE_SOURCEFORGE_RSS_URL,
)
from .models import UpdateCheckResult

OFFICIAL_MANIFEST_URL = UPDATE_MANIFEST_URL
UPDATE_CHECK_TIMEOUT_SECONDS = 10.0
_STRICT_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_GITHUB_TAG_RE = re.compile(r"/tag/v?(\d+\.\d+\.\d+)")
_GITHUB_RELEASE_PAGE_TAG_RE = re.compile(r"/releases/tag/v?(\d+\.\d+\.\d+)")
_ALLOWED_UPDATE_HOSTS = {
    "github.com",
    "www.github.com",
    "sourceforge.net",
    "www.sourceforge.net",
    "justagwas.com",
    "www.justagwas.com",
}


def normalize_version(version_text: str) -> str:
    text = str(version_text or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    return text


def parse_semver(value: str) -> tuple[int, int, int] | None:
    match = _STRICT_SEMVER_RE.search(str(value or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def parse_version_tuple(version_text: str) -> tuple[int, ...] | None:
    return parse_semver(normalize_version(version_text))


def is_newer_version(latest_version: str, current_version: str) -> bool:
    latest_tuple = parse_version_tuple(latest_version)
    current_tuple = parse_version_tuple(current_version)
    if latest_tuple is None or current_tuple is None:
        return False
    return latest_tuple > current_tuple


def _normalize_semver(value: str) -> str:
    parsed = parse_semver(normalize_version(value))
    if parsed is None:
        raise RuntimeError("did not contain a valid semantic version")
    return f"{parsed[0]}.{parsed[1]}.{parsed[2]}"


def _ensure_not_stopped(stop_event: Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Update check stopped.")


def _request_text(url: str, *, stop_event: Event | None = None) -> tuple[str, str]:
    _ensure_not_stopped(stop_event)
    request = Request(
        url=url,
        headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
        method="GET",
    )
    with urlopen(request, timeout=UPDATE_CHECK_TIMEOUT_SECONDS) as response:
        final_url = str(response.geturl() or url)
        body = response.read().decode("utf-8-sig", errors="replace")
    _ensure_not_stopped(stop_event)
    return body, final_url


def _is_allowed_update_url(candidate: object) -> bool:
    parsed = urlparse(str(candidate or "").strip())
    if parsed.scheme.lower() != "https":
        return False
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return False
    return host in _ALLOWED_UPDATE_HOSTS


def _resolve_download_url(candidate: object, *, fallback: str = UPDATE_GITHUB_DOWNLOAD_URL) -> str:
    value = str(candidate or "").strip()
    if value and _is_allowed_update_url(value):
        return value
    fallback_url = str(fallback or "").strip()
    if fallback_url and _is_allowed_update_url(fallback_url):
        return fallback_url
    default_url = str(DEFAULT_DOWNLOAD_URL or "").strip()
    if default_url and _is_allowed_update_url(default_url):
        return default_url
    return ""


def fetch_update_manifest(*, stop_event: Event | None = None) -> tuple[str, str]:
    payload, _ = _request_text(OFFICIAL_MANIFEST_URL, stop_event=stop_event)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("latest.json did not return a JSON object")

    version_candidate = (
        data.get("version")
        or data.get("latest")
        or data.get("app_version")
        or ""
    )
    if not isinstance(version_candidate, str):
        raise RuntimeError("latest.json did not contain a semantic version string")
    latest_version = _normalize_semver(version_candidate)

    download_candidate = (
        data.get("download_url")
        or data.get("url")
        or data.get("download")
        or ""
    )
    download_url = _resolve_download_url(download_candidate, fallback=UPDATE_GITHUB_DOWNLOAD_URL)
    if not download_url:
        raise RuntimeError("download URL was missing/untrusted and fallback URL was invalid")

    return latest_version, download_url


def _fetch_github_latest(*, stop_event: Event | None = None) -> tuple[str, str]:
    body, final_url = _request_text(UPDATE_GITHUB_LATEST_URL, stop_event=stop_event)
    _ensure_not_stopped(stop_event)

    match = _GITHUB_TAG_RE.search(final_url)
    if match is None:
        match = _GITHUB_RELEASE_PAGE_TAG_RE.search(body)
    if match is None:
        raise RuntimeError("latest release page did not contain a valid version tag")

    download_url = _resolve_download_url(UPDATE_GITHUB_DOWNLOAD_URL, fallback=UPDATE_GITHUB_DOWNLOAD_URL)
    if not download_url:
        raise RuntimeError("GitHub fallback download URL is invalid")
    return _normalize_semver(match.group(1)), download_url


def _fetch_sourceforge_latest(*, stop_event: Event | None = None) -> tuple[str, str]:
    rss_text, _ = _request_text(UPDATE_SOURCEFORGE_RSS_URL, stop_event=stop_event)
    _ensure_not_stopped(stop_event)

    root = ET.fromstring(rss_text)
    for title in root.findall(".//item/title"):
        text = str(title.text or "")
        parsed = parse_semver(text)
        if parsed is None:
            continue
        version = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
        download_url = _resolve_download_url(
            "",
            fallback="https://sourceforge.net/projects/mediacrate/files/latest/download",
        )
        if not download_url:
            raise RuntimeError("SourceForge fallback download URL is invalid")
        return version, download_url
    raise RuntimeError("RSS did not contain a valid semantic version")


def detect_latest_version(*, stop_event: Event | None = None) -> tuple[str, str, str]:
    providers = (
        ("latest.json", fetch_update_manifest, "official_manifest"),
        ("GitHub", _fetch_github_latest, "github_latest"),
        ("SourceForge", _fetch_sourceforge_latest, "sourceforge_rss"),
    )
    errors: list[str] = []
    for provider_name, provider, source in providers:
        _ensure_not_stopped(stop_event)
        try:
            version, download_url = provider(stop_event=stop_event)
            return version, download_url, source
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(f"{provider_name} failed: {exc}")
    raise RuntimeError("Could not parse latest version from update sources. " + "; ".join(errors))


class UpdateService:
    def check_for_updates(
        self,
        current_version: str,
        *,
        stop_event: Event | None = None,
    ) -> UpdateCheckResult:
        current_normalized = normalize_version(current_version) or "0.0.0"
        if parse_semver(current_normalized) is None:
            current_normalized = "0.0.0"

        _ensure_not_stopped(stop_event)
        try:
            latest_version, download_url, source = detect_latest_version(stop_event=stop_event)
        except InterruptedError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Unable to fetch update metadata from update sources. {exc}") from exc

        latest_display = normalize_version(latest_version) or str(latest_version)
        resolved_download_url = _resolve_download_url(download_url)
        if not resolved_download_url:
            raise RuntimeError("No trusted download URL was available from update sources.")

        return UpdateCheckResult(
            update_available=is_newer_version(latest_version, current_normalized),
            current_version=current_normalized,
            latest_version=latest_display,
            download_url=resolved_download_url,
            source=source,
            error="",
        )
