"""Regression tests for the MCP modeling-tool bridge helper.

These verify the parent-side failure propagation: a bare success payload is
accepted, legacy nested envelopes still work, and every failure becomes a
``FreeCadOperationError`` rather than a successful tool result.
"""

from __future__ import annotations

from typing import Any

import pytest

from freecad_mcp.freecad_bridge import FreeCadOperationError
from freecad_mcp.tools.modeling import _run_bridge_op


class _FakeBridge:
    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    def run(self, op: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
        self.calls.append((op, params, timeout))
        if self.error is not None:
            raise self.error
        return self.result


def test_run_bridge_op_accepts_bare_success_payload() -> None:
    payload = {"document_id": "demo", "objects": []}
    bridge = _FakeBridge(result=payload)

    assert _run_bridge_op(bridge, "list_objects", {"document_id": "demo"}) == payload


def test_run_bridge_op_unwraps_legacy_nested_success_payload() -> None:
    payload = {"document_id": "demo", "object_name": "Tank1"}
    bridge = _FakeBridge(result={"ok": True, "op": "tank", "result": payload})

    assert _run_bridge_op(bridge, "tank", {"document_id": "demo"}) == payload


def test_run_bridge_op_raises_on_nested_error_envelope() -> None:
    bridge = _FakeBridge(
        result={
            "ok": False,
            "op": "tank",
            "error": {"type": "OSError", "message": "Invalid project file"},
        }
    )

    with pytest.raises(FreeCadOperationError) as excinfo:
        _run_bridge_op(bridge, "tank", {"document_id": "demo"})

    assert excinfo.value.error_type == "OSError"
    assert "Invalid project file" in str(excinfo.value)


def test_run_bridge_op_raises_on_nested_string_error() -> None:
    bridge = _FakeBridge(
        result={"ok": False, "op": "tank", "error": "FreeCAD error: boom"}
    )

    with pytest.raises(FreeCadOperationError) as excinfo:
        _run_bridge_op(bridge, "tank", {"document_id": "demo"})

    assert excinfo.value.error_type == "Error"
    assert "FreeCAD error: boom" in str(excinfo.value)


def test_run_bridge_op_raises_on_minimal_nested_error_envelope() -> None:
    bridge = _FakeBridge(result={"ok": False, "op": "tank"})

    with pytest.raises(FreeCadOperationError) as excinfo:
        _run_bridge_op(bridge, "tank", {"document_id": "demo"})

    assert excinfo.value.error_type == "Error"
    assert str(excinfo.value)


def test_run_bridge_op_rejects_non_boolean_nested_ok() -> None:
    bridge = _FakeBridge(result={"ok": "false", "error": "boom"})

    with pytest.raises(FreeCadOperationError) as excinfo:
        _run_bridge_op(bridge, "tank", {"document_id": "demo"})

    assert excinfo.value.error_type == "BridgeError"
    assert str(excinfo.value)


def test_run_bridge_op_wraps_bridge_exception() -> None:
    bridge = _FakeBridge(error=RuntimeError("nope"))

    with pytest.raises(FreeCadOperationError) as excinfo:
        _run_bridge_op(bridge, "tank", {"document_id": "demo"})

    assert excinfo.value.error_type == "RuntimeError"
    assert "Bridge operation 'tank' failed" in str(excinfo.value)
