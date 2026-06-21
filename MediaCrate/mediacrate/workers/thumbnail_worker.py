from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

from .base_worker import BaseWorker

THUMBNAIL_TIMEOUT_SECONDS = 8.0
THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024
THUMBNAIL_MAX_REDIRECTS = 3


def _is_public_address(host: str) -> bool:
    value = str(host or "").strip()
    if not value:
        return False
    try:
        addresses = socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not addresses:
        return False
    for item in addresses:
        try:
            raw_address = item[4][0]
            parsed = ipaddress.ip_address(raw_address)
        except (IndexError, TypeError, ValueError):
            return False
        if (
            parsed.is_loopback
            or parsed.is_private
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        ):
            return False
    return True


def _thumbnail_url_is_safe(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    host = str(parsed.hostname or "").strip()
    return _is_public_address(host)


def _redirect_target(response: requests.Response, current_url: str) -> str:
    location = str(response.headers.get("location") or "").strip()
    if not location:
        return ""
    return urljoin(str(current_url or ""), location)


def _safe_content_length(value: object) -> int:
    try:
        parsed = int(str(value or "").strip() or "0")
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


class ThumbnailWorker(BaseWorker):
    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = str(url or "").strip()

    def _open_response(self) -> requests.Response | None:
        current_url = self._url
        for _attempt in range(THUMBNAIL_MAX_REDIRECTS + 1):
            if self._stop_event.is_set() or (not current_url) or (not _thumbnail_url_is_safe(current_url)):
                return None
            response = requests.get(
                current_url,
                stream=True,
                timeout=THUMBNAIL_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if not (300 <= int(response.status_code) < 400):
                if not _thumbnail_url_is_safe(str(response.url or current_url)):
                    response.close()
                    return None
                return response
            next_url = _redirect_target(response, current_url)
            response.close()
            if not next_url:
                return None
            current_url = next_url
        return None

    def run(self) -> None:
        def execute() -> tuple[str, bytes]:
            data = b""
            if self._stop_event.is_set() or (not self._url) or (not _thumbnail_url_is_safe(self._url)):
                return self._url, data

            response = self._open_response()
            if response is None:
                return self._url, data
            with response:
                response.raise_for_status()
                content_type = str(response.headers.get("content-type") or "").lower()
                if content_type and ("image" not in content_type):
                    return self._url, data
                content_length = _safe_content_length(response.headers.get("content-length"))
                if content_length > THUMBNAIL_MAX_BYTES:
                    return self._url, data
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=65536):
                    if self._stop_event.is_set():
                        return self._url, b""
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > THUMBNAIL_MAX_BYTES:
                        chunks = []
                        break
                    chunks.append(chunk)
                if chunks:
                    data = b"".join(chunks)
            return self._url, data

        def on_result(payload: tuple[str, bytes]) -> None:
            self.statusChanged.emit("thumbnail", "done")
            self.finishedSummary.emit(payload)

        def on_error(exc: Exception) -> None:
            self.statusChanged.emit("thumbnail", "error")
            self.errorRaised.emit("thumbnail", str(exc))
            self.finishedSummary.emit((self._url, b""))

        self.statusChanged.emit("thumbnail", "downloading")
        self.run_guarded(execute=execute, on_result=on_result, on_error=on_error)
