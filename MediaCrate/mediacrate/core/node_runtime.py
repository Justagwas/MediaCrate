from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import OrderedDict

NODE_MIN_MAJOR_VERSION = 22
_VERSION_CACHE: OrderedDict[tuple, tuple[float, int | None]] = OrderedDict()
_VERSION_LOCK = threading.RLock()
_VERSION_GENERATION = 0


def clear_node_version_cache() -> None:
    global _VERSION_GENERATION
    with _VERSION_LOCK:
        _VERSION_GENERATION += 1
        _VERSION_CACHE.clear()


def node_version_major(path: str) -> int | None:
    executable = str(path or "").strip()
    if not executable:
        return None
    try:
        info = os.stat(executable)
        identity = (os.path.normcase(os.path.abspath(executable)), info.st_dev, info.st_ino,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except OSError:
        return _probe_node_version(executable)
    with _VERSION_LOCK:
        cached = _VERSION_CACHE.get(identity)
        if cached is not None and time.monotonic() < cached[0]:
            _VERSION_CACHE.move_to_end(identity)
            return cached[1]
        generation = _VERSION_GENERATION
    major = _probe_node_version(executable)
    with _VERSION_LOCK:
        if generation == _VERSION_GENERATION:
            _VERSION_CACHE[identity] = (time.monotonic() + (30.0 if major is not None else 1.0), major)
            _VERSION_CACHE.move_to_end(identity)
            while len(_VERSION_CACHE) > 16:
                _VERSION_CACHE.popitem(last=False)
    return major


def _probe_node_version(executable: str) -> int | None:
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        completed = subprocess.run(
            [executable, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
            creationflags=creationflags,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    version = str(completed.stdout or "").strip().lstrip("vV")
    major = version.split(".", 1)[0].strip()
    try:
        return int(major)
    except ValueError:
        return None


def is_supported_node_runtime(path: str, *, minimum_major: int = NODE_MIN_MAJOR_VERSION) -> bool:
    major = node_version_major(path)
    return major is not None and major >= int(minimum_major)
