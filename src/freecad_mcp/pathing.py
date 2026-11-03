"""Shared path translation helpers.

Handles native Windows/Linux paths and WSL2<->Windows translation. Translation
is performed with ``wslpath`` invoked as an argument list (no shell). Paths are
never silently converted for a target that is not Windows.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class PathTranslationError(RuntimeError):
    """Raised when a path cannot be translated for the requested target."""


def _is_windows_path(path: str | Path) -> bool:
    text = str(path)
    return len(text) >= 3 and text[1] == ":" and text[2] in ("\\", "/")


def is_windows_path(path: str | Path) -> bool:
    """Return True if *path* is a native Windows drive path (e.g. ``C:\\dir``)."""
    return _is_windows_path(path)


def _run_wslpath(arg: str, path: str) -> str:
    try:
        completed = subprocess.run(
            ["wslpath", arg, path],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PathTranslationError("wslpath is not available on PATH.") from exc
    if completed.returncode != 0:
        raise PathTranslationError(
            f"wslpath {arg} failed for {path!r}: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    result = completed.stdout.strip("\n")
    if not result:
        raise PathTranslationError(f"wslpath {arg} returned an empty result for {path!r}.")
    return result


def to_windows(path: str | Path) -> str:
    text = str(path)
    if _is_windows_path(text):
        return text
    return _run_wslpath("-w", text)


def to_linux(path: str | Path) -> str:
    text = str(path)
    if not _is_windows_path(text):
        return text
    return _run_wslpath("-u", text)


def for_target(path: str | Path, target: str) -> str:
    normalized = target.strip().lower()
    if normalized in ("windows", "win", "nt"):
        return to_windows(path)
    if normalized in ("linux", "wsl", "posix"):
        return to_linux(path)
    if normalized in ("native", "auto", ""):
        if os.name == "nt":
            return to_windows(path)
        return str(path)
    raise PathTranslationError(f"Unknown translation target: {target!r}")


__all__ = ["PathTranslationError", "is_windows_path", "to_windows", "to_linux", "for_target"]
