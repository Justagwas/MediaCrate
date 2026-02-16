from __future__ import annotations

import requests

from .base_worker import BaseWorker

THUMBNAIL_TIMEOUT_SECONDS = 8.0
THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024


class ThumbnailWorker(BaseWorker):
    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = str(url or "").strip()

    def run(self) -> None:
        def execute() -> tuple[str, bytes]:
            data = b""
            if self._stop_event.is_set() or (not self._url):
                return self._url, data

            with requests.get(self._url, stream=True, timeout=THUMBNAIL_TIMEOUT_SECONDS) as response:
                response.raise_for_status()
                content_type = str(response.headers.get("content-type") or "").lower()
                if content_type and ("image" not in content_type):
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

        def on_result(result: tuple[str, bytes]) -> None:
            self.finishedSummary.emit(result)

        def on_error(exc: Exception) -> None:
            self.errorRaised.emit("thumbnail", str(exc))
            self.finishedSummary.emit((self._url, b""))

        self.run_guarded(
            execute=execute,
            on_result=on_result,
            on_error=on_error,
        )
