from collections.abc import Callable

from PySide6.QtCore import QObject, Qt


class RequestDelivery(QObject):
    def __init__(self, owner: QObject, callback: Callable, current: Callable[[], bool]) -> None:
        super().__init__(owner)
        self._callback = callback
        self._current = current

    def deliver(self, *args) -> None:
        if self._current():
            self._callback(*args)


def connect_request(signal, owner: QObject, thread, callback: Callable, current: Callable[[], bool]) -> None:
    delivery = RequestDelivery(owner, callback, current)
    signal.connect(delivery.deliver, Qt.ConnectionType.QueuedConnection)
    thread.finished.connect(delivery.deleteLater, Qt.ConnectionType.QueuedConnection)
