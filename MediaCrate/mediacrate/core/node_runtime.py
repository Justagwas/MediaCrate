from __future__ import annotations

import os
import subprocess

NODE_MIN_MAJOR_VERSION = 22


def node_version_major(path: str) -> int | None:
    executable = str(path or "").strip()
    if not executable:
        return None
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
    version = str(completed.stdout or "").strip().lstrip("vV")
    major = version.split(".", 1)[0].strip()
    try:
        return int(major)
    except ValueError:
        return None


def is_supported_node_runtime(path: str, *, minimum_major: int = NODE_MIN_MAJOR_VERSION) -> bool:
    major = node_version_major(path)
    return major is not None and major >= int(minimum_major)
