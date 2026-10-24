"""Unit tests for the ``boolean_cut`` MCP tool.

The FreeCAD child process is replaced with an in-memory fake bridge so these
tests run on a machine without FreeCAD. They pin the tool's contract:

* the bridge operation name and payload (including workspace/document isolation),
* the structured success result,
* structured failure propagation for missing/invalid/empty operands, Boolean
  failures, and malformed bridge responses,
* rejection of identical base/tool names before the bridge is contacted.

The FreeCAD SDK intentionally reports raised tool exceptions to the client as an
opaque ``Error executing tool ...`` result (see ``test_modeling_tool_params.py``),
so the MCP-level tests assert ``is_error`` and the bridge-helper tests assert the
underlying structured messages.

The real FreeCAD path is exercised in ``test_freecad_integration.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp import Client

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge.errors import FreeCadOperationError
from freecad_mcp.freecad_bridge.runner import handle_request
from freecad_mcp.server import create_server
from freecad_mcp.tools import modeling

_SUCCESS_PAYLOAD: dict[str, Any] = {
    "document_id": "hollow_doc",
    "object_name": "HollowCylinder",
    "base_object": "OuterCyl",
    "tool_object": "InnerCyl",
    "type": "Part::Feature",
    "shape_type": "Solid",
    "volume": 94.2477796,
    "length": 4.0,
    "width": 4.0,
    "height": 10.0,
}


class _FakeBridge:
    """Minimal stand-in for ``FreeCadBridge`` that records calls."""

    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    def run(self, op: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
        self.calls.append((op, params, timeout))
        if self.error is not None:
            raise self.error
        return self.result


def _call_tool(
    monkeypatch: Any,
    bridge: _FakeBridge,
    arguments: dict[str, Any],
) -> Any:
    """Invoke the registered ``boolean_cut`` tool over an in-process MCP session."""
    monkeypatch.setattr(modeling, "_make_bridge", lambda _settings: bridge)

    async def _run() -> Any:
        async with Client(create_server(Settings())) as client:
            return await client.call_tool("boolean_cut", {"params": arguments})

    return asyncio.run(_run())


def _error_envelope(message: str, error_type: str = "OperationError") -> dict[str, Any]:
    return {
        "ok": False,
        "op": "boolean_cut",
        "error": {"type": error_type, "message": message},
    }


def _arguments(**overrides: Any) -> dict[str, Any]:
    base = {"document_id": "doc", "base_object": "Outer", "tool_object": "Inner"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_boolean_cut_subtracts_two_solids(monkeypatch: Any) -> None:
    bridge = _FakeBridge(result=_SUCCESS_PAYLOAD)

    result = _call_tool(
        monkeypatch,
        bridge,
        {
            "document_id": "hollow_doc",
            "base_object": "OuterCyl",
            "tool_object": "InnerCyl",
            "result_name": "HollowCylinder",
        },
    )

    assert result.is_error is False
    payload = result.structured_content
    assert payload is not None
    assert payload["object_name"] == "HollowCylinder"
    assert payload["base_object"] == "OuterCyl"
    assert payload["tool_object"] == "InnerCyl"
    assert payload["shape_type"] == "Solid"
    assert payload["volume"] == 94.2477796
    assert (payload["length"], payload["width"], payload["height"]) == (4.0, 4.0, 10.0)

    assert len(bridge.calls) == 1
    op, params, _timeout = bridge.calls[0]
    assert op == "boolean_cut"
    assert params["document_id"] == "hollow_doc"
    assert params["base_object"] == "OuterCyl"
    assert params["tool_object"] == "InnerCyl"
    assert params["result_name"] == "HollowCylinder"


def test_boolean_cut_forwards_workspace_and_default_result_name(monkeypatch: Any) -> None:
    """The tool must not drop the workspace override or the caller's document id."""
    bridge = _FakeBridge(result=_SUCCESS_PAYLOAD)

    result = _call_tool(
        monkeypatch,
        bridge,
        _arguments(document_id="some_doc", workspace="C:/isolated/workspace"),
    )

    assert result.is_error is False
    op, params, _timeout = bridge.calls[0]
    assert op == "boolean_cut"
    assert params["document_id"] == "some_doc"
    assert params["workspace"] == "C:/isolated/workspace"
    # ``result_name`` is optional; the bridge picks the default when it is None.
    assert params["result_name"] is None


def test_boolean_cut_returns_actual_result_name(monkeypatch: Any) -> None:
    """A suffixed name chosen by the bridge must be surfaced, not the requested one."""
    payload = dict(_SUCCESS_PAYLOAD, object_name="HollowCylinder_1")
    bridge = _FakeBridge(result=payload)

    result = _call_tool(
        monkeypatch,
        bridge,
        _arguments(result_name="HollowCylinder"),
    )

    assert result.is_error is False
    assert result.structured_content["object_name"] == "HollowCylinder_1"


# ---------------------------------------------------------------------------
# Failure path — MCP tool level
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "base object 'Nope' not found",
        "tool object 'Gone' not found",
        "base object shape is not valid",
        "tool object has an empty (zero-volume) shape",
        "Boolean cut failed: OCC error",
        "Boolean cut produced an empty result (zero volume)",
    ],
)
def test_boolean_cut_reports_bridge_failures(monkeypatch: Any, message: str) -> None:
    bridge = _FakeBridge(result=_error_envelope(message))

    result = _call_tool(monkeypatch, bridge, _arguments())

    assert result.is_error is True
    # The bridge was contacted exactly once with the expected operation.
    assert [call[0] for call in bridge.calls] == ["boolean_cut"]


def test_boolean_cut_rejects_identical_objects_before_bridge(monkeypatch: Any) -> None:
    bridge = _FakeBridge(result=_SUCCESS_PAYLOAD)

    result = _call_tool(
        monkeypatch,
        bridge,
        _arguments(base_object="Same", tool_object="Same"),
    )

    assert result.is_error is True
    assert bridge.calls == []


def test_boolean_cut_reports_malformed_bridge_response(monkeypatch: Any) -> None:
    bridge = _FakeBridge(result="not a mapping")

    result = _call_tool(monkeypatch, bridge, _arguments())

    assert result.is_error is True


def test_boolean_cut_reports_non_boolean_ok_envelope(monkeypatch: Any) -> None:
    bridge = _FakeBridge(result={"ok": "false", "op": "boolean_cut", "error": "boom"})

    result = _call_tool(monkeypatch, bridge, _arguments())

    assert result.is_error is True


def test_boolean_cut_propagates_bridge_exception(monkeypatch: Any) -> None:
    bridge = _FakeBridge(error=RuntimeError("process died"))

    result = _call_tool(monkeypatch, bridge, _arguments())

    assert result.is_error is True


# ---------------------------------------------------------------------------
# Failure path — bridge helper message propagation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "base object 'Nope' not found",
        "tool object 'Gone' not found",
        "base object shape is not valid",
        "base object has an empty (zero-volume) shape",
        "tool object is not a Solid (type: Shell)",
        "Boolean cut failed: OCC error",
        "Boolean cut produced an empty result (zero volume)",
        "Boolean cut produced an invalid shape",
    ],
)
def test_bridge_op_propagates_boolean_cut_errors(message: str) -> None:
    bridge = _FakeBridge(result=_error_envelope(message))

    with pytest.raises(FreeCadOperationError) as excinfo:
        modeling._run_bridge_op(bridge, "boolean_cut", {})

    assert excinfo.value.error_type == "OperationError"
    assert message in str(excinfo.value)


def test_bridge_op_rejects_non_boolean_ok_with_message() -> None:
    bridge = _FakeBridge(result={"ok": "false", "op": "boolean_cut", "error": "boom"})

    with pytest.raises(FreeCadOperationError) as excinfo:
        modeling._run_bridge_op(bridge, "boolean_cut", {})

    assert excinfo.value.error_type == "BridgeError"
    assert "non-boolean" in str(excinfo.value)


def test_bridge_op_rejects_malformed_response_with_message() -> None:
    bridge = _FakeBridge(result="not a mapping")

    with pytest.raises(FreeCadOperationError) as excinfo:
        modeling._run_bridge_op(bridge, "boolean_cut", {})

    assert excinfo.value.error_type == "BridgeError"
    assert "Unexpected bridge response format" in str(excinfo.value)


def test_bridge_op_success_unwraps_envelope() -> None:
    """``_run_bridge_op`` accepts the envelope returned by the real runner."""
    bridge = _FakeBridge(result={"ok": True, "op": "boolean_cut", "result": _SUCCESS_PAYLOAD})

    assert modeling._run_bridge_op(bridge, "boolean_cut", {}) == _SUCCESS_PAYLOAD


# ---------------------------------------------------------------------------
# Runner-side validation that needs no FreeCAD process
# ---------------------------------------------------------------------------


def test_runner_boolean_cut_rejects_missing_names() -> None:
    response = handle_request(
        {"op": "boolean_cut", "params": {"document_id": "doc", "base_object": "", "tool_object": "Tool"}}
    )
    assert response["ok"] is False
    assert "base_object is required" in response["error"]["message"]

    response = handle_request(
        {"op": "boolean_cut", "params": {"document_id": "doc", "base_object": "Base", "tool_object": ""}}
    )
    assert response["ok"] is False
    assert "tool_object is required" in response["error"]["message"]


def test_runner_boolean_cut_rejects_identical_names() -> None:
    response = handle_request(
        {"op": "boolean_cut", "params": {"document_id": "doc", "base_object": "Same", "tool_object": "Same"}}
    )
    assert response["ok"] is False
    assert "must be different" in response["error"]["message"]


def test_runner_boolean_cut_rejects_invalid_document_id() -> None:
    response = handle_request(
        {"op": "boolean_cut", "params": {"document_id": "../escape", "base_object": "A", "tool_object": "B"}}
    )
    assert response["ok"] is False
    assert "document_id" in response["error"]["message"]
