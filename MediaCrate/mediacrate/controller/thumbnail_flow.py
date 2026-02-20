from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt

from ..core.models import BatchEntry
from ..workers.thumbnail_worker import ThumbnailWorker
from .thumbnail_cache import ThumbnailCache


class ThumbnailFlowCoordinator(QObject):
    def __init__(
        self,
        *,
        owner: QObject,
        cache: ThumbnailCache,
        validate_url: Callable[[str], bool],
        get_batch_entry: Callable[[str], BatchEntry | None],
        set_batch_thumbnail: Callable[[str, bytes | None, str], None],
        set_single_thumbnail: Callable[[bytes | None, str], None],
        expected_single_thumbnail_url: Callable[[], str],
        max_workers: int = 4,
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self._cache = cache
        self._validate_url = validate_url
        self._get_batch_entry = get_batch_entry
        self._set_batch_thumbnail = set_batch_thumbnail
        self._set_single_thumbnail = set_single_thumbnail
        self._expected_single_thumbnail_url = expected_single_thumbnail_url
        self._max_workers = max(1, int(max_workers))

        self._queue_urls: deque[str] = deque()
        self._queued_urls: set[str] = set()
        self._waiters_by_url: dict[str, set[str]] = {}
        self._threads_by_url: dict[str, QThread] = {}
        self._workers_by_url: dict[str, ThumbnailWorker] = {}

        self._single_thread: QThread | None = None
        self._single_worker: ThumbnailWorker | None = None
        self._single_pending_url = ""
        self._single_active_url = ""

    def set_max_workers(self, value: int) -> None:
        normalized = max(1, int(value))
        if normalized == self._max_workers:
            return
        self._max_workers = normalized
        self._pump_batch_queue()

    def running_threads(self) -> list[QThread]:
        threads: list[QThread] = []
        if self._single_thread is not None:
            threads.append(self._single_thread)
        threads.extend(list(self._threads_by_url.values()))
        return threads

    def stop_all(self, *, clear_pending: bool = True) -> None:
        for worker in list(self._workers_by_url.values()):
            worker.stop()
        if self._single_worker is not None:
            self._single_worker.stop()
        if clear_pending:
            self._queue_urls.clear()
            self._queued_urls.clear()
            self._waiters_by_url.clear()

    def clear_cache(self) -> None:
        self._cache.clear()

    def maintenance_tick(self, *, ttl_seconds: float) -> None:
        self._cache.purge_older_than(ttl_seconds)

    def cache_thumbnail_bytes(self, source_url: str, payload: bytes) -> None:
        normalized_url = str(source_url or "").strip()
        if not normalized_url:
            return
        self._cache.set(normalized_url, bytes(payload or b""))

    def clear_entry_waiter(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        if not key:
            return
        for waiters in self._waiters_by_url.values():
            waiters.discard(key)
        self._set_batch_thumbnail(key, None, "")

    def schedule_entries(self, entries: list[BatchEntry]) -> None:
        for entry in entries:
            self.schedule_entry_thumbnail(entry.entry_id)

    def schedule_entry_thumbnail(self, entry_id: str) -> None:
        key = str(entry_id or "").strip()
        if not key:
            return
        entry = self._get_batch_entry(key)
        if entry is None:
            return
        url = str(entry.thumbnail_url or "").strip()
        if (not url) or (not self._validate_url(url)):
            self._set_batch_thumbnail(key, None, "")
            return
        cached = self._cache.get(url)
        if cached is not None:
            self._set_batch_thumbnail(key, cached, url)
            return
        waiters = self._waiters_by_url.setdefault(url, set())
        waiters.add(key)
        if (url not in self._threads_by_url) and (url not in self._queued_urls):
            self._queue_urls.append(url)
            self._queued_urls.add(url)
        self._pump_batch_queue()

    def _pump_batch_queue(self) -> None:
        while len(self._threads_by_url) < self._max_workers and self._queue_urls:
            url = str(self._queue_urls.popleft() or "").strip()
            self._queued_urls.discard(url)
            if not url or url in self._threads_by_url:
                continue
            thread = QThread(self._owner)
            worker = ThumbnailWorker(url)
            worker.moveToThread(thread)
            thread.setProperty("source_url", url)
            thread.started.connect(worker.run)
            worker.finishedSummary.connect(self._on_batch_summary, Qt.ConnectionType.QueuedConnection)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._on_batch_thread_finished, Qt.ConnectionType.QueuedConnection)
            self._threads_by_url[url] = thread
            self._workers_by_url[url] = worker
            thread.start()

    def _on_batch_thread_finished(self) -> None:
        sender = self.sender()
        source_url = ""
        if isinstance(sender, QThread):
            source_url = str(sender.property("source_url") or "").strip()
        self._on_batch_finished(source_url)

    def _on_batch_summary(self, payload: object) -> None:
        if (not isinstance(payload, tuple)) or len(payload) != 2:
            return
        source_url = str(payload[0] or "").strip()
        raw_data = payload[1]
        data = raw_data if isinstance(raw_data, (bytes, bytearray)) else b""
        if not source_url:
            return
        if data:
            self.cache_thumbnail_bytes(source_url, bytes(data))
        waiters = self._waiters_by_url.pop(source_url, set())
        for entry_id in list(waiters):
            entry = self._get_batch_entry(entry_id)
            if entry is None:
                continue
            if str(entry.thumbnail_url or "").strip() != source_url:
                continue
            self._set_batch_thumbnail(entry_id, bytes(data) if data else None, source_url)

    def _on_batch_finished(self, source_url: str) -> None:
        key = str(source_url or "").strip()
        self._threads_by_url.pop(key, None)
        self._workers_by_url.pop(key, None)
        self._pump_batch_queue()

    def schedule_single_thumbnail(self, thumbnail_url: str) -> None:
        target_url = str(thumbnail_url or "").strip()
        self._single_pending_url = target_url
        if not target_url:
            self._set_single_thumbnail(None, "")
            return
        cached = self._cache.get(target_url)
        if cached is not None:
            self._set_single_thumbnail(cached, target_url)
            return
        self._pump_single_queue()

    def _pump_single_queue(self) -> None:
        if self._single_thread and self._single_thread.isRunning():
            return
        source_url = str(self._single_pending_url or "").strip()
        if not source_url:
            return
        thread = QThread(self._owner)
        worker = ThumbnailWorker(source_url)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finishedSummary.connect(self._on_single_summary, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_single_finished, Qt.ConnectionType.QueuedConnection)
        self._single_active_url = source_url
        self._single_thread = thread
        self._single_worker = worker
        thread.start()

    def _on_single_summary(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        source_url, data = payload
        normalized_url = str(source_url or "").strip()
        image_data = bytes(data) if data else b""
        if normalized_url and image_data:
            self.cache_thumbnail_bytes(normalized_url, image_data)
        expected_thumbnail = self._expected_single_thumbnail_url()
        if normalized_url and expected_thumbnail == normalized_url:
            self._set_single_thumbnail(image_data if image_data else None, normalized_url)

    def _on_single_finished(self) -> None:
        previous = str(self._single_active_url or "").strip()
        self._single_active_url = ""
        self._single_thread = None
        self._single_worker = None
        if self._single_pending_url and self._single_pending_url != previous:
            self._pump_single_queue()
