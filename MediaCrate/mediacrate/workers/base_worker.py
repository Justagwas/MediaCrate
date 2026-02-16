from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal


class BaseWorker(QObject):
    progressChanged = Signal(str, float, str)
    statusChanged = Signal(str, str)
    logChanged = Signal(str)
    errorRaised = Signal(str, str)
    finishedSummary = Signal(object)
    finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def is_cancelled(self) -> bool:
        return self._stop_event.is_set()

    def run_guarded(
        self,
        *,
        execute: Callable[[], Any],
        on_result: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_interrupted: Callable[[InterruptedError], None] | None = None,
    ) -> None:
        try:
            result = execute()
        except InterruptedError as exc:
            if on_interrupted is not None:
                on_interrupted(exc)
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
        else:
            if on_result is not None:
                on_result(result)
        finally:
            self.finished.emit()
