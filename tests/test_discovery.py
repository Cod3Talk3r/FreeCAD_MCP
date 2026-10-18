"""Tests for FreeCAD executable discovery (FreeCAD-independent)."""

from __future__ import annotations

from pathlib import Path

from freecad_mcp.freecad_bridge.discovery import (
    EXECUTABLE_ENV,
    executable_names,
    find_freecad_executable,
)


def _fake_executable(directory: Path) -> Path:
    path = directory / executable_names()[0]
    path.write_text("", encoding="utf-8")
    return path


def test_explicit_override_wins(tmp_path: Path) -> None:
    exe = _fake_executable(tmp_path)
    assert find_freecad_executable(str(exe), environ={}) == exe


def test_missing_override_is_authoritative_not_fallback(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    assert find_freecad_executable(str(missing), environ={"PATH": ""}, use_registry=False) is None


def test_environment_variable_override(tmp_path: Path) -> None:
    exe = _fake_executable(tmp_path)
    assert find_freecad_executable(None, environ={EXECUTABLE_ENV: str(exe)}) == exe


def test_found_on_path(tmp_path: Path) -> None:
    exe = _fake_executable(tmp_path)
    assert find_freecad_executable(None, environ={"PATH": str(tmp_path)}) == exe


def test_not_found_when_nothing_matches(tmp_path: Path) -> None:
    result = find_freecad_executable(None, environ={"PATH": str(tmp_path), "ProgramFiles": str(tmp_path)}, use_registry=False)
    assert result is None
