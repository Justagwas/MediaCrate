from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

PREFIX = "mediacrate-update-"
_LOCK = threading.RLock()


def _plain(path: Path) -> bool:
    info = path.lstat()
    return not (path.is_symlink() or getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _acquire(path: Path):
    if path.exists() and (not _plain(path) or not path.is_file()):
        raise OSError("Unsafe update ownership file")
    stream = path.open("a+b")
    try:
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except BaseException:
        stream.close()
        raise


def _release(stream) -> None:
    try:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


class StagingLease:
    def __init__(self, coordinator, path: Path, stream) -> None:
        self.coordinator = coordinator
        self.path = path
        self._stream = stream
        self._mutex = threading.RLock()

    def state(self, phase: str) -> None:
        if self._stream is None:
            raise RuntimeError("Update staging ownership has been released")
        destination = self.coordinator.directory / f"{self.path.name}.json"
        if destination.exists() and not _plain(destination):
            raise OSError("Unsafe update staging state")
        temporary = destination.with_suffix(".tmp")
        if temporary.exists() and not _plain(temporary):
            raise OSError("Unsafe update staging state")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"phase": phase}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)

    @contextmanager
    def operation(self):
        with self._mutex:
            if self._stream is None:
                raise RuntimeError("Update staging ownership has been released")
            yield

    def close(self) -> None:
        with self._mutex:
            if self._stream is not None:
                stream, self._stream = self._stream, None
                _release(stream)


class StagingCoordinator:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.directory = self.root / ".update-ownership"

    @contextmanager
    def coordinate(self):
        with _LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.directory.exists() and not _plain(self.directory):
                raise OSError("Unsafe update ownership directory")
            self.directory.mkdir(exist_ok=True)
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    stream = _acquire(self.directory / "coordinator.lock")
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise OSError("Update staging is busy or inaccessible")
                    time.sleep(0.02)
            try:
                yield
            finally:
                _release(stream)

    def _lease(self, path: Path) -> StagingLease:
        if (not path.name.startswith(PREFIX) or path.parent.resolve() != self.root
                or not _plain(path) or not path.is_dir()):
            raise OSError("Update staging is outside owned storage")
        return StagingLease(self, path, _acquire(self.directory / f"{path.name}.lock"))

    def create(self) -> StagingLease:
        with self.coordinate():
            path = Path(tempfile.mkdtemp(prefix=PREFIX, dir=self.root))
            lease = None
            try:
                lease = self._lease(path)
                lease.state("preparing")
                return lease
            except BaseException:
                if lease is not None:
                    lease.close()
                path.rmdir()
                raise

    def adopt(self, path: Path) -> StagingLease:
        with self.coordinate():
            lease = self._lease(Path(path))
            try:
                state_path = self.directory / f"{path.name}.json"
                if state_path.exists():
                    phase = self._phase(state_path)
                    if phase in {"handoff_pending", "handed_off"} or phase is None:
                        raise RuntimeError("Update package has already been handed off or has unknown ownership")
                lease.state("prepared")
                return lease
            except BaseException:
                lease.close()
                raise

    @staticmethod
    def _phase(path: Path) -> str | None:
        try:
            if not _plain(path) or path.stat().st_size > 4096:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            phase = data.get("phase") if isinstance(data, dict) else None
            return phase if phase in {"preparing", "prepared", "discarded", "handoff_pending", "handed_off"} else None
        except (OSError, ValueError):
            return None

    def cleanup_claim(self, path: Path) -> StagingLease | None:
        try:
            with self.coordinate():
                lease = self._lease(path)
                eligible = False
                try:
                    phase = self._phase(self.directory / f"{path.name}.json")
                    eligible = phase in {"preparing", "prepared", "discarded"}
                    return lease if eligible else None
                finally:
                    if not eligible:
                        lease.close()
        except (OSError, RuntimeError):
            return None

    def forget(self, path: Path) -> None:
        with self.coordinate():
            if path.exists():
                return
            for suffix in (".json", ".tmp", ".lock"):
                candidate = self.directory / f"{path.name}{suffix}"
                if candidate.exists() and _plain(candidate):
                    candidate.unlink()
