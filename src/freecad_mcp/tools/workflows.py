"""Read-only Method A workflow capability discovery and model-state snapshot (M19 P1/P2).

Derives capability metadata from currently registered MCP tools and existing
bridge/runner operation metadata. Provides a concise, bounded model-state
snapshot by reusing existing bridge operations and workspace metadata.

Does not execute workflows, perform recovery, or import FreeCAD in the parent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from .. import __version__
from ..app.direct import DirectBackend
from ..app.workflow_contracts import CapabilityCatalog, ModelStateSnapshot, OperationCapability
from ..app.workflows import build_model_state_snapshot
from ..config import Settings
from ..freecad_bridge import FreeCadBridge
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


def _run_bridge_op(bridge: FreeCadBridge, op: str, params: dict[str, Any]) -> dict[str, Any]:
    from ..app.contracts import BuildRequest

    result = DirectBackend(bridge).execute(BuildRequest(op, params))
    return dict(result.data or {})


def register(server: MCPServer, settings: Settings) -> None:
    """Register read-only workflow capability and model-state tools on ``server``."""

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

    @server.tool(
        name="get_model_state",
        title="Get model state snapshot",
        description=(
            "Return a concise, structured snapshot of the current document/model state "
            "and normalized artifact/revision references for Method A workflows. Reuses "
            "existing allowlisted bridge operations (list_objects, get_document_hierarchy, "
            "validate_document) and workspace metadata/sidecars; does not mutate the "
            "model, run workflows, or perform recovery. The snapshot reflects live "
            "inspection data collected during this request when available; persisted "
            "artifact fields come from workspace files/metadata and do not imply that "
            "in-memory state was saved. Unavailable or bounded information is reported "
            "explicitly."
        ),
        structured_output=True,
    )
    def get_model_state(
        document_id: str,
        workspace: Optional[str] = None,
    ) -> ModelStateSnapshot:
        """Collect a concise model-state snapshot for *document_id*."""
        bridge = FreeCadBridge.from_settings(settings)

        def runner(op: str, op_params: dict[str, Any]) -> dict[str, Any]:
            return _run_bridge_op(bridge, op, op_params)

        return build_model_state_snapshot(
            document_id,
            workspace=workspace,
            op_runner=runner,
        )
