"""Tests for the MCP server and its diagnostic tool.

These use the official SDK's in-process client (``mcp.Client``) to exercise the
real server object over a real MCP session, without spawning a process and
without requiring FreeCAD.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp import Client

import freecad_mcp
from freecad_mcp.config import Settings
from freecad_mcp.server import create_server


async def _list_tools() -> Any:
    async with Client(create_server(Settings())) as client:
        return await client.list_tools()


async def _call_server_info(include_environment: bool) -> Any:
    async with Client(create_server(Settings())) as client:
        return await client.call_tool("server_info", {"include_environment": include_environment})


def test_server_exposes_server_info_tool() -> None:
    result = asyncio.run(_list_tools())

    tools = {tool.name: tool for tool in result.tools}
    assert "server_info" in tools
    assert "freecad_status" in tools

    tool = tools["server_info"]
    assert tool.description
    assert tool.input_schema["type"] == "object"
    assert "include_environment" in tool.input_schema.get("properties", {})
    assert tool.output_schema is not None


def test_server_info_returns_core_payload() -> None:
    result = asyncio.run(_call_server_info(False))

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["name"] == "freecad-mcp"
    assert payload["version"] == freecad_mcp.__version__
    assert payload["mcp_sdk_version"]
    assert payload["freecad_bridge_status"] == "not_implemented"
    assert payload.get("environment") is None


def test_environment_details_are_opt_in() -> None:
    result = asyncio.run(_call_server_info(True))

    payload = result.structured_content
    assert payload is not None
    environment = payload.get("environment")
    assert environment is not None
    assert environment["python_version"]
    assert environment["executable"]
