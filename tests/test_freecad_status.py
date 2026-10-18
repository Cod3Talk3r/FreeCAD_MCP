"""Tests for the ``freecad_status`` tool's unavailable/failure paths (no FreeCAD)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp import Client

from freecad_mcp.config import Settings
from freecad_mcp.server import create_server
from freecad_mcp.tools.freecad import collect_freecad_status


def test_unavailable_when_configured_path_is_missing(tmp_path: Path) -> None:
    status = collect_freecad_status(Settings(freecad_executable=str(tmp_path / "missing.exe")))

    assert status.available is False
    assert status.executable is None
    assert status.hint


def test_unavailable_when_executable_is_not_freecad() -> None:
    """A real executable that is not FreeCAD produces a reported failure, not a crash."""
    status = collect_freecad_status(Settings(freecad_executable=sys.executable, freecad_timeout_seconds=30))

    assert status.available is False
    assert status.executable == sys.executable
    assert "FreeCAD" in (status.error or "")


async def _call_tool(settings: Settings) -> Any:
    async with Client(create_server(settings)) as client:
        return await client.call_tool("freecad_status", {})


def test_freecad_status_tool_over_mcp(tmp_path: Path) -> None:
    settings = Settings(freecad_executable=str(tmp_path / "missing.exe"))
    result = asyncio.run(_call_tool(settings))

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["available"] is False
    assert payload["hint"]
