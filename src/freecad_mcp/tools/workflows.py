"""Read-only Method A workflow capability discovery (M19 P1).

Derives capability metadata from currently registered MCP tools and existing
bridge/runner operation metadata. Does not execute workflows, inspect live
model state, compose snapshots, or perform recovery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import __version__
from ..app.workflow_contracts import CapabilityCatalog, OperationCapability
from ..config import Settings
from ..freecad_bridge.runner import OPERATIONS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.mcpserver import MCPServer

# Known MCP tool-to-operation aliases where the tool name differs from the
# allowlisted runner operation name.
_TOOL_TO_OP_ALIAS: dict[str, str] = {
    "validate_document": "validate",
}


def _operation_name_for_tool(tool_name: str) -> str | None:
    if tool_name in OPERATIONS:
        return tool_name
    alias = _TOOL_TO_OP_ALIAS.get(tool_name)
    if alias in OPERATIONS:
        return alias
    return None


def _kind_for_tool(tool_name: str, operation_name: str | None) -> str:
    if tool_name.startswith("taiga_"):
        return "taiga"
    if tool_name == "describe_workflow_capabilities":
        return "workflow"
    if operation_name is not None:
        return "modeling_operation"
    return "diagnostic"


async def _collect_capability_catalog(
    server: MCPServer, settings: Settings
) -> CapabilityCatalog:
    tools = await server.list_tools()
    capabilities: list[OperationCapability] = []
    operation_tools: list[str] = []
    non_operation_tools: list[str] = []

    for tool in sorted(tools, key=lambda item: item.name):
        tool_name = tool.name
        operation_name = _operation_name_for_tool(tool_name)
        description = (tool.description or "").strip()
        title = getattr(tool, "title", None)
        input_schema = tool.input_schema or {
            "type": "object",
            "properties": {},
        }
        output_schema = getattr(tool, "output_schema", None)
        kind = _kind_for_tool(tool_name, operation_name)

        capabilities.append(
            OperationCapability(
                tool_name=tool_name,
                title=title,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                operation_name=operation_name,
                kind=kind,
            )
        )
        if operation_name is not None:
            operation_tools.append(tool_name)
        else:
            non_operation_tools.append(tool_name)

    return CapabilityCatalog(
        server_name=settings.server_name,
        server_version=__version__,
        tools=capabilities,
        operation_tools=operation_tools,
        non_operation_tools=non_operation_tools,
    )


def register(server: MCPServer, settings: Settings) -> None:
    """Register read-only workflow capability discovery tools on ``server``."""

    @server.tool(
        name="describe_workflow_capabilities",
        title="Describe workflow capabilities",
        description=(
            "Return a deterministic, read-only catalog of registered MCP capabilities "
            "for Method A workflows. Derived from currently registered MCP tools "
            "(names, titles, descriptions, input/output schemas) and existing "
            "bridge/runner operation metadata/aliases. Does not execute FreeCAD "
            "operations, run workflows, inspect live model state, or perform recovery."
        ),
        structured_output=True,
    )
    async def describe_workflow_capabilities() -> CapabilityCatalog:
        return await _collect_capability_catalog(server, settings)
