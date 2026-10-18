"""Tests that require a real FreeCAD installation.

Skipped automatically when FreeCAD cannot be discovered, so a machine without
FreeCAD never sees a false failure. Set FREECAD_MCP_FREECAD_EXECUTABLE to force a
specific console executable.
"""

from __future__ import annotations

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.tools.freecad import collect_freecad_status

_DISCOVERED = find_freecad_executable()

requires_freecad = pytest.mark.skipif(
    _DISCOVERED is None,
    reason="FreeCAD console executable not found (install FreeCAD or set FREECAD_MCP_FREECAD_EXECUTABLE).",
)


def _settings() -> Settings:
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)


@requires_freecad
def test_real_freecad_status_reports_version_and_part() -> None:
    status = collect_freecad_status(_settings())

    assert status.available is True, status.error
    assert status.version
    assert status.part_available is True
    assert status.python_version


@requires_freecad
def test_real_freecad_bridge_ping() -> None:
    bridge = FreeCadBridge.from_settings(_settings())
    assert bridge.ping("hello")["message"] == "hello"
