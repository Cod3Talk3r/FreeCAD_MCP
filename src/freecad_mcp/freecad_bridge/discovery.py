"""Locate the FreeCAD console executable.

Discovery never hard-codes a machine-specific path. It checks, in order:

1. an explicit override (from configuration or an environment variable),
2. the ``PATH``,
3. a short list of conventional install locations per platform,
4. (Windows only) the Uninstall registry entries, from which the install root
   can be derived.

Returns ``None`` when nothing is found; callers decide how to report that.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

#: Environment variable / setting name for an explicit executable path.
EXECUTABLE_ENV = "FREECAD_MCP_FREECAD_EXECUTABLE"


def executable_names() -> tuple[str, ...]:
    """Candidate file names for the FreeCAD console executable on this platform."""
    if os.name == "nt":
        return ("freecadcmd.exe", "FreeCADCmd.exe")
    return ("freecadcmd", "FreeCADCmd")


def _from_override(override: str | Path | None) -> Path | None:
    if not override:
        return None
    path = Path(override).expanduser()
    return path if path.is_file() else None


def _from_path(environ: Mapping[str, str]) -> Path | None:
    for name in executable_names():
        found = shutil.which(name, path=environ.get("PATH"))
        if found:
            return Path(found)
    return None


def _common_dirs(environ: Mapping[str, str]) -> Iterable[Path]:
    """Conventional FreeCAD install directories for the current platform."""
    if os.name == "nt":
        program_files = [environ.get("ProgramFiles"), environ.get("ProgramFiles(x86)"), environ.get("LOCALAPPDATA")]
        bases = [Path(p) for p in program_files if p]
        for base in bases:
            yield from _glob_dirs(base, "FreeCAD*")
            yield from _glob_dirs(base / "Programs", "FreeCAD*")
    elif os.uname().sysname == "Darwin":  # pragma: no cover - platform specific
        yield Path("/Applications/FreeCAD.app/Contents/Resources/bin")
    else:  # pragma: no cover - platform specific
        yield from _glob_dirs(Path("/usr/lib"), "freecad*")
        yield from _glob_dirs(Path("/usr/local/lib"), "freecad*")
        yield Path("/usr/bin")


def _glob_dirs(base: Path, pattern: str) -> Iterable[Path]:
    bin_dir = base / "bin"
    if bin_dir.is_dir():
        yield bin_dir
    if base.is_dir():
        try:
            for child in sorted(base.glob(pattern)):
                if child.is_dir():
                    yield child / "bin"
        except OSError:  # pragma: no cover - unreadable directories
            return


def _from_registry_windows() -> Path | None:  # pragma: no cover - Windows only
    """Derive the install root from the Windows Uninstall registry entries."""
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None

    uninstall_roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, subkey in uninstall_roots:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                count = winreg.QueryInfoKey(key)[0]
                for index in range(count):
                    candidate = _console_from_registry_entry(key, index, winreg)
                    if candidate:
                        return candidate
        except OSError:
            continue
    return None


def _console_from_registry_entry(key: Any, index: int, winreg: Any) -> Path | None:  # pragma: no cover - Windows only
    """Inspect one Uninstall entry; return a console executable if it is FreeCAD."""
    try:
        name = winreg.EnumKey(key, index)
        with winreg.OpenKey(key, name) as entry:
            try:
                display, _ = winreg.QueryValueEx(entry, "DisplayName")
            except OSError:
                return None
            if not isinstance(display, str) or "freecad" not in display.lower():
                return None
            for value_name in ("InstallLocation", "UninstallString"):
                try:
                    value, _ = winreg.QueryValueEx(entry, value_name)
                except OSError:
                    continue
                candidate = _console_from_registry_value(str(value))
                if candidate:
                    return candidate
    except OSError:
        return None
    return None


def _console_from_registry_value(value: str) -> Path | None:  # pragma: no cover - Windows only
    cleaned = value.strip().strip('"')
    if not cleaned:
        return None
    path = Path(cleaned)
    root = path if path.is_dir() else path.parent
    for name in executable_names():
        for candidate in (root / name, root / "bin" / name):
            if candidate.is_file():
                return candidate
    return None


def find_freecad_executable(
    override: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    use_registry: bool = True,
) -> Path | None:
    """Return the best FreeCAD console executable, or ``None`` if not found.

    An explicit ``override`` (or the ``FREECAD_MCP_FREECAD_EXECUTABLE`` environment
    variable) is **authoritative**: if it is set but does not point to a file, the
    result is ``None`` rather than silently falling back to a different FreeCAD.
    This makes a misconfigured path visible instead of surprising.

    Args:
        override: Explicit path (from configuration).
        environ: Environment mapping to read. Defaults to :data:`os.environ`.
        use_registry: Whether to consult the Windows Uninstall registry. Tests
            disable this to stay deterministic.
    """
    env = os.environ if environ is None else environ

    if override:
        return _from_override(override)

    env_override = env.get(EXECUTABLE_ENV)
    if env_override:
        return _from_override(env_override)

    from_path = _from_path(env)
    if from_path:
        return from_path

    for directory in _common_dirs(env):
        for name in executable_names():
            candidate = directory / name
            if candidate.is_file():
                return candidate

    if use_registry:
        return _from_registry_windows()
    return None
