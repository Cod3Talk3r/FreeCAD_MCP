"""Focused tests for M19 P1 workflow contracts and capability discovery."""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp import Client
from pydantic import ValidationError

from freecad_mcp import __version__
from freecad_mcp.app.workflow_contracts import (
    CapabilityCatalog,
    OperationCapability,
    WorkflowResult,
    WorkflowStepRequest,
    WorkflowStepResult,
)
from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge.runner import OPERATIONS
from freecad_mcp.server import create_server
from freecad_mcp.tools.workflows import _collect_capability_catalog


def test_operation_capability_serialization_and_defaults() -> None:
    cap = OperationCapability(
        tool_name="create_box",
        title="Create box",
        description="Create a box.",
        input_schema={"type": "object", "properties": {}},
        kind="modeling_operation",
    )
    data = cap.model_dump(mode="json")
    assert data["tool_name"] == "create_box"
    assert data["title"] == "Create box"
    assert data["output_schema"] is None
    assert data["operation_name"] is None
    assert data["kind"] == "modeling_operation"

    cap2 = OperationCapability(
        tool_name="server_info",
        description="Server info.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        operation_name=None,
        kind="diagnostic",
    )
    dumped = json.loads(cap2.model_dump_json())
    assert dumped["operation_name"] is None
    assert dumped["kind"] == "diagnostic"
    assert dumped["output_schema"] == {"type": "object"}


def test_capability_catalog_serialization() -> None:
    catalog = CapabilityCatalog(
        server_name="freecad-mcp",
        server_version="0.0.0",
        tools=[],
        operation_tools=[],
        non_operation_tools=[],
    )
    dumped = catalog.model_dump(mode="json")
    assert dumped["server_name"] == "freecad-mcp"
    assert dumped["tools"] == []


def test_workflow_step_request_defaults_and_validation() -> None:
    req = WorkflowStepRequest(operation="create_box")
    assert req.parameters == {}
    assert req.label is None
    assert req.inspect_after is False
    assert req.validate_after is False
    assert req.model_dump(mode="json")["operation"] == "create_box"

    req2 = WorkflowStepRequest(
        operation="create_box",
        parameters={"document_id": "d"},
        label="box",
        inspect_after=["list_objects"],
        validate_after=True,
    )
    assert req2.inspect_after == ["list_objects"]
    assert req2.validate_after is True

    with pytest.raises(ValidationError):
        WorkflowStepRequest()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        WorkflowStepRequest(operation="x", parameters="bad")  # type: ignore[arg-type]


def test_workflow_step_result_and_workflow_result_contracts() -> None:
    step = WorkflowStepResult(
        step_index=0,
        operation="create_box",
        status="completed",
        ok=True,
        result={"name": "Box"},
        artifact_ref={"document_id": "d", "revision": 1},
    )
    dumped = step.model_dump(mode="json")
    assert dumped["step_index"] == 0
    assert dumped["error"] is None
    assert dumped["artifact_ref"]["revision"] == 1

    wf = WorkflowResult(
        ok=False,
        status="failed",
        total_steps=2,
        completed_steps=1,
        steps=[step],
        error={"message": "boom", "error_type": "X"},
    )
    wf_dump = json.loads(wf.model_dump_json())
    assert wf_dump["status"] == "failed"
    assert len(wf_dump["steps"]) == 1

    with pytest.raises(ValidationError):
        WorkflowStepResult(step_index=0, operation="x", status="nope", ok=True)  # type: ignore[arg-type]


def test_collect_capability_catalog_is_deterministic_and_derived() -> None:
    async def _run() -> tuple[CapabilityCatalog, CapabilityCatalog]:
        server = create_server(Settings(server_name="test-server"))

        original_tm = server._tool_manager
        original_list_tools = server.list_tools
        calls = 0

        class _GuardToolManager:
            def list_tools(self):
                return original_tm.list_tools()

            @property
            def _tools(self):  # pragma: no cover - should never be accessed
                raise AssertionError("discovery must not access _tool_manager._tools")

        async def _tracked_list_tools():
            nonlocal calls
            calls += 1
            return await original_list_tools()

        server.list_tools = _tracked_list_tools  # type: ignore[assignment]
        server._tool_manager = _GuardToolManager()  # type: ignore[assignment]

        catalog = await _collect_capability_catalog(
            server, Settings(server_name="test-server")
        )
        again = await _collect_capability_catalog(
            server, Settings(server_name="test-server")
        )
        assert calls == 2
        return catalog, again

    catalog, again = asyncio.run(_run())

    assert catalog.server_name == "test-server"
    assert catalog.server_version == __version__
    assert [c.tool_name for c in catalog.tools] == sorted(
        c.tool_name for c in catalog.tools
    )
    assert [c.tool_name for c in catalog.tools] == [c.tool_name for c in again.tools]
    assert catalog.operation_tools == sorted(catalog.operation_tools)
    assert catalog.non_operation_tools == sorted(catalog.non_operation_tools)

    names = {c.tool_name for c in catalog.tools}
    assert "describe_workflow_capabilities" in names
    assert "create_document" in names
    assert "validate_document" in names

    op_tools = {c.tool_name: c for c in catalog.tools}
    assert op_tools["create_document"].operation_name == "create_document"
    assert op_tools["create_document"].kind == "modeling_operation"
    assert op_tools["validate_document"].operation_name == "validate"
    assert op_tools["validate_document"].kind == "modeling_operation"
    assert op_tools["server_info"].operation_name is None
    assert op_tools["server_info"].kind == "diagnostic"
    assert op_tools["taiga_status"].operation_name is None
    assert op_tools["taiga_status"].kind == "taiga"
    assert op_tools["describe_workflow_capabilities"].operation_name is None
    assert op_tools["describe_workflow_capabilities"].kind == "workflow"

    # Every capability maps operation_name only to known allowlisted operations.
    for cap in catalog.tools:
        assert cap.description.strip()
        assert isinstance(cap.input_schema, dict)
        assert cap.input_schema.get("type") == "object"
        if cap.operation_name is not None:
            assert cap.operation_name in OPERATIONS
            assert cap.tool_name in catalog.operation_tools
        else:
            assert cap.tool_name in catalog.non_operation_tools

    # No duplicate registries: operation tools must correspond to known ops/alias targets.
    assert "validate" not in {c.tool_name for c in catalog.tools}  # tool name stays aliased


def test_describe_workflow_capabilities_tool_schema_and_result() -> None:
    async def _run() -> None:
        async with Client(create_server(Settings())) as client:
            tools = await client.list_tools()
            tool = next(
                t for t in tools.tools if t.name == "describe_workflow_capabilities"
            )
            assert tool.description and "read-only" in tool.description.lower()
            assert "does not execute" in tool.description.lower()
            assert tool.input_schema.get("type") == "object"
            assert tool.input_schema.get("properties", {}) == {}
            # structured output schema should describe CapabilityCatalog
            out = getattr(tool, "output_schema", None) or {}
            props = out.get("properties", {}) if isinstance(out, dict) else {}
            assert "tools" in props
            assert "operation_tools" in props
            assert "non_operation_tools" in props

            result = await client.call_tool("describe_workflow_capabilities", {})
            data = result.structured_content
            assert isinstance(data, dict)
            assert data["server_name"] == "freecad-mcp"
            assert data["server_version"] == __version__
            assert isinstance(data["tools"], list) and data["tools"]
            names = [t["tool_name"] for t in data["tools"]]
            assert names == sorted(names)
            sample = next(
                t for t in data["tools"] if t["tool_name"] == "create_document"
            )
            assert sample["operation_name"] == "create_document"
            assert sample["kind"] == "modeling_operation"
            assert sample["description"]
            assert sample["input_schema"]["type"] == "object"

    asyncio.run(_run())
