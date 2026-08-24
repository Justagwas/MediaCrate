from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

from .base_worker import BaseWorker

THUMBNAIL_TIMEOUT_SECONDS = 8.0
THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024
THUMBNAIL_MAX_REDIRECTS = 3


def _is_public_ip_address(value: object) -> bool:
    raw = str(value or "").strip().split("%", 1)[0]
    if not raw:
        return False
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return bool(
        parsed.is_global
        and not parsed.is_loopback
        and not parsed.is_link_local
        and not parsed.is_multicast
        and not parsed.is_reserved
        and not parsed.is_unspecified
    )


def _public_addresses(host: str) -> frozenset[str]:
    value = str(host or "").strip()
    if not value:
        return frozenset()
    try:
        addresses = socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
    except OSError:
        return frozenset()
    public_addresses: set[str] = set()
    if not addresses:
        return frozenset()
    for item in addresses:
        try:
            raw_address = item[4][0]
        except (IndexError, TypeError):
            return frozenset()
        if not _is_public_ip_address(raw_address):
            return frozenset()
        public_addresses.add(str(ipaddress.ip_address(str(raw_address).split("%", 1)[0])))
    return frozenset(public_addresses)


def _is_public_address(host: str) -> bool:
    return bool(_public_addresses(host))


def _thumbnail_url_is_safe(url: str) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if parsed.scheme.lower() != "https":
        return False
    host = str(parsed.hostname or "").strip()
    return _is_public_address(host)


def _response_peer_address(response: requests.Response) -> str:
    raw = getattr(response, "raw", None)
    if raw is None:
        return ""
    candidate_paths = (
        ("_connection", "sock"),
        ("_connection", "_sock"),
        ("connection", "sock"),
        ("_fp", "fp", "raw", "_sock"),
        ("_original_response", "fp", "raw", "_sock"),
    )
    for path in candidate_paths:
        candidate = raw
        for attribute in path:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        if candidate is None:
            continue
        try:
            peer = candidate.getpeername()
        except (AttributeError, OSError):
            continue
        if isinstance(peer, tuple) and peer:
            return str(peer[0] or "").strip()
    return ""


def _response_uses_proxy(response: requests.Response) -> bool:
    raw = getattr(response, "raw", None)
    connection = getattr(raw, "_connection", None) or getattr(raw, "connection", None)
    if connection is None:
        return False
    return bool(
        getattr(connection, "proxy", None)
        or getattr(connection, "_has_connected_to_proxy", False)
    )


def _response_peer_is_public(response: requests.Response) -> bool:
    # With an HTTPS proxy the socket peer is the configured proxy, while TLS
    # still authenticates the requested origin through the CONNECT tunnel.
    return _response_uses_proxy(response) or _is_public_ip_address(_response_peer_address(response))


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
            try:
                requested_url = urlparse(current_url)
                requested_host = str(requested_url.hostname or "").strip()
            except Exception:
                return None
            addresses_before = _public_addresses(requested_host)
            if (
                self._stop_event.is_set()
                or (not current_url)
                or requested_url.scheme.lower() != "https"
                or (not addresses_before)
            ):
                return None
            response = requests.get(
                current_url,
                stream=True,
                timeout=THUMBNAIL_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if not _response_peer_is_public(response):
                response.close()
                return None
            if not (300 <= int(response.status_code) < 400):
                final_url = urlparse(str(response.url or current_url))
                final_host = str(final_url.hostname or "").strip()
                if final_url.scheme.lower() != "https" or final_host.lower() != requested_host.lower():
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
