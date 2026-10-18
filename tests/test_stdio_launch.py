"""Integration test: launch the documented stdio command and talk to it.

This exercises the real launch path a client such as OpenCode uses, over the
default ``legacy`` MCP protocol, to confirm the server starts and its tool is
discoverable. It does not require FreeCAD.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp import Client, StdioServerParameters


async def _talk_to_stdio_server() -> tuple[Any, Any]:
    params = StdioServerParameters(command=sys.executable, args=["-m", "freecad_mcp"])
    async with Client(params, mode="legacy") as client:
        tools = await client.list_tools()
        result = await client.call_tool("server_info", {})
        return tools, result


def test_server_launches_over_stdio_and_serves_server_info() -> None:
    tools, result = asyncio.run(_talk_to_stdio_server())

    assert "server_info" in {tool.name for tool in tools.tools}
    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["name"] == "freecad-mcp"
