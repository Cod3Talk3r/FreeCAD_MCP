"""M12 — Capability evidence and tool discoverability.

Verifies that the MCP tool surface matches the registered operation allowlist
in ``freecad_bridge/runner.py``: every registered operation has a tool, every
modeling tool maps to a known operation, unknown operations are rejected, and
every tool carries a non-empty description.

These tests run without FreeCAD and never mistake mocks for native evidence.
"""

from __future__ import annotations

import asyncio

import pytest
from mcp import Client

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge.runner import OPERATIONS, handle_request
from freecad_mcp.server import create_server

#: Diagnostic / non-operation tools that have no entry in runner.OPERATIONS.
NON_OP_TOOLS = {
    "server_info",
    "freecad_status",
    "taiga_status",
    "taiga_score_actions",
    "taiga_build",
}

#: Operations in runner.OPERATIONS that are intentionally not exposed as
#: standalone MCP tools (covered by a diagnostic tool or not yet wired).
OP_WITHOUT_TOOL = {
    "ping",          # covered by freecad_status
    "get_version",   # covered by freecad_status
    "validate",      # exposed as validate_document tool (aliased name)
    "delete_object", # operation exists; MCP tool not yet wired (M12 documentation gap)
}

#: MCP tool names that map to an operation under a different name.
TOOL_TO_OP_ALIAS = {
    "validate_document": "validate",
}


async def _list_tools():
    async with Client(create_server(Settings())) as client:
        return await client.list_tools()


async def _tool_descriptions() -> dict[str, str]:
    async with Client(create_server(Settings())) as client:
        result = await client.list_tools()
    return {tool.name: tool.description or "" for tool in result.tools}


def _registered_tool_names() -> set[str]:
    tools = asyncio.run(_list_tools())
    return {tool.name for tool in tools.tools}


def test_every_registered_operation_has_a_mcp_tool() -> None:
    """Each name in runner.OPERATIONS must be exposed as an MCP tool
    (or intentionally documented as not yet wired)."""
    tool_names = _registered_tool_names()
    missing = sorted(op for op in OPERATIONS if op not in tool_names and op not in OP_WITHOUT_TOOL)
    assert not missing, (
        f"Operations missing from MCP tool surface: {missing}.  Add a tool "
        "wrapper in src/freecad_mcp/tools/modeling.py, move the operation to "
        "OP_WITHOUT_TOOL, or remove it from runner.OPERATIONS."
    )


def test_every_mcp_tool_maps_to_a_known_operation() -> None:
    """Each modeling MCP tool must correspond to a registered operation
    (possibly under an aliased name)."""
    tool_names = _registered_tool_names()
    op_names = set(OPERATIONS)
    unexpected = sorted(
        n for n in tool_names
        if n not in op_names and n not in NON_OP_TOOLS and n not in TOOL_TO_OP_ALIAS
    )
    # Also verify that aliased names actually point to real operations.
    for tool_name, op_name in TOOL_TO_OP_ALIAS.items():
        assert tool_name in tool_names, f"Aliased tool {tool_name!r} is not registered."
        assert op_name in op_names, f"Alias target {op_name!r} is not a registered operation."
    assert not unexpected, (
        f"MCP tools with no matching runner.OPERATIONS entry: {unexpected}.  "
        "Either register the operation, add to TOOL_TO_OP_ALIAS, or remove the tool."
    )


def test_unknown_operation_is_rejected() -> None:
    """The operation registry rejects unknown operations with a clear error."""
    result = handle_request({"op": "this_operation_does_not_exist", "params": {}})
    assert result.get("ok") is False
    combined = str(result).lower()
    assert "unknown" in combined or "unknownoperationerror" in combined


def test_every_tool_has_a_description() -> None:
    """Every exposed MCP tool must carry a non-empty human-readable description."""
    descriptions = asyncio.run(_tool_descriptions())
    missing = sorted(name for name, desc in descriptions.items() if not desc.strip())
    assert not missing, f"Tools missing descriptions: {missing}"


def test_tool_input_schemas_are_objects() -> None:
    """Every MCP tool must declare a JSON Schema object input schema."""
    result = asyncio.run(_list_tools())
    bad = []
    for tool in result.tools:
        schema = tool.input_schema
        if not isinstance(schema, dict) or schema.get("type") != "object":
            bad.append(tool.name)
    assert not bad, f"Tools with non-object input_schema: {bad}"
