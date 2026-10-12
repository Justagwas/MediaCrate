from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

_LOCK = threading.RLock()
_HELD = threading.local()
_FILES = {"ffmpeg": ("ffmpeg.exe", "ffprobe.exe"), "node": ("node.exe",)}


@contextmanager
def dependency_resolution_lock(root: Path, name: str, *, blocking: bool = False):
    with _LOCK:
        directory = _directory(root, name)
        key = str(directory)
        held = getattr(_HELD, "roots", set())
        if key in held:
            yield
            return
        parent = directory.parent
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / f"{name}.lock"
        if path.exists() and (not _plain(path) or not path.is_file()):
            raise OSError("Dependency lock is not a regular file")
        with path.open("a+b") as stream:
            if not stream.tell():
                stream.write(b"\0"); stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            _HELD.roots = held | {key}
            try:
                yield
            finally:
                _HELD.roots = held
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plain(path: Path) -> bool:
    info = path.lstat()
    return not (path.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _directory(root: Path, name: str) -> Path:
    if name not in _FILES:
        raise ValueError("Unknown dependency transaction")
    root = root.resolve()
    parent = root / ".dependency-transactions"
    directory = parent / name
    for path in (parent, directory):
        if path.exists() and not _plain(path):
            raise RuntimeError("Dependency transaction directory is a reparse point")
    if directory.resolve().parent != parent.resolve() or parent.resolve().parent != root:
        raise RuntimeError("Dependency transaction is outside managed storage")
    return directory


def _write(directory: Path, journal: dict) -> None:
    tmp = directory / "journal.tmp"
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(journal, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, directory / "journal.json")


def _clean(directory: Path, name: str) -> None:
    allowed = {"journal.json", "journal.tmp"}
    allowed.update(f"{file}.{suffix}" for file in _FILES[name] for suffix in ("new", "old", "restore"))
    for path in directory.iterdir():
        if path.name not in allowed or not _plain(path) or not path.is_file():
            raise RuntimeError("Unexpected dependency transaction content")
    for path in list(directory.iterdir()):
        if path.name != "journal.json":
            path.unlink()
    (directory / "journal.json").unlink(missing_ok=True)
    directory.rmdir()


def _recover(root: Path, name: str) -> bool:
    try:
        directory = _directory(root, name)
        if not directory.exists():
            return True
        journal_path = directory / "journal.json"
        if not journal_path.exists():
            _clean(directory, name)
            return True
        if not _plain(journal_path):
            return False
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if journal.get("dependency") != name or journal.get("phase") not in {"preparing", "prepared", "committing", "committed"}:
            return False
        phase = journal["phase"]
        if phase in {"preparing", "prepared"}:
            _clean(directory, name)
            return True
        records = journal.get("files", {})
        if set(records) != set(_FILES[name]):
            return False
        for file, record in records.items():
            if not isinstance(record, dict) or not isinstance(record.get("existed"), bool):
                return False
            if len(str(record.get("new_hash", ""))) != 64:
                return False
        if phase == "committed":
            if not all((root / file).is_file() and _plain(root / file) and _digest(root / file) == record["new_hash"] for file, record in records.items()):
                return False
            _clean(directory, name)
            return True
        for file, record in records.items():
            target = root / file
            if target.exists() and not _plain(target):
                return False
            if record["existed"]:
                backup = directory / f"{file}.old"
                if not backup.is_file() or not _plain(backup) or _digest(backup) != record.get("old_hash"):
                    return False
                restored = directory / f"{file}.restore"
                shutil.copy2(backup, restored)
                os.replace(restored, target)
            elif target.exists():
                if _digest(target) != record["new_hash"]:
                    return False
                target.unlink()
        if not all((_digest(root / file) == record.get("old_hash")) if record["existed"] else not (root / file).exists() for file, record in records.items()):
            return False
        journal["phase"] = "prepared"
        _write(directory, journal)
        _clean(directory, name)
        return True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return False


def recover_dependency(root: Path, name: str) -> bool:
    root = Path(root).resolve()
    if not (root / ".dependency-transactions").exists():
        return True
    try:
        with dependency_resolution_lock(root, name):
            return _recover(root, name)
    except (OSError, RuntimeError):
        return False


def managed_binary_available(root: Path, binary_name: str) -> bool:
    filename = str(binary_name).lower()
    if not filename.endswith(".exe"):
        filename += ".exe"
    for name, files in _FILES.items():
        if filename in files:
            return recover_dependency(root, name)
    return True


def promote_dependency(root: Path, name: str, binaries: dict[str, Path], check_cancel) -> list[Path]:
    root = Path(root).resolve()
    with dependency_resolution_lock(root, name, blocking=True):
        if set(binaries) != set(_FILES[name]):
            raise RuntimeError("Dependency binary set is incomplete")
        if not _recover(root, name):
            raise RuntimeError("Dependency recovery is incomplete; retained backups require recovery")
        directory = _directory(root, name)
        directory.mkdir(parents=True)
        journal = {"dependency": name, "phase": "preparing", "files": {}}
        _write(directory, journal)
        try:
            for file in _FILES[name]:
                check_cancel()
                target = root / file
                if target.exists() and (not target.is_file() or not _plain(target)):
                    raise RuntimeError("Managed dependency target is not a regular file")
                staged = directory / f"{file}.new"
                shutil.copy2(binaries[file], staged)
                record = {"existed": target.exists(), "new_hash": _digest(staged), "old_hash": None}
                if record["existed"]:
                    backup = directory / f"{file}.old"
                    shutil.copy2(target, backup)
                    record["old_hash"] = _digest(backup)
                journal["files"][file] = record
            journal["phase"] = "prepared"; _write(directory, journal)
            check_cancel()
            journal["phase"] = "committing"; _write(directory, journal)
            for file in _FILES[name]:
                os.replace(directory / f"{file}.new", root / file)
            if not all(_digest(root / file) == record["new_hash"] for file, record in journal["files"].items()):
                raise RuntimeError("Dependency promotion verification failed")
            journal["phase"] = "committed"; _write(directory, journal)
        except Exception:
            if not _recover(root, name):
                raise RuntimeError("Dependency promotion failed and rollback remains incomplete")
            raise
        try:
            _clean(directory, name)
        except OSError:
            pass
        return [root / file for file in _FILES[name]]
