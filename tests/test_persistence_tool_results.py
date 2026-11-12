"""Regression tests for persistence-related MCP result contracts."""

from __future__ import annotations

from typing import Any

from freecad_mcp.config import Settings
from freecad_mcp.tools import modeling


class _Bridge:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result

    def run(
        self, op: str, params: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        assert op in {"save_document", "restore_snapshot"}
        return self.result


class _Server:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, **metadata: Any):
        def register(fn: Any) -> Any:
            self.tools[metadata["name"]] = fn
            return fn

        return register


def _registered_tools(monkeypatch: Any, bridge: _Bridge) -> dict[str, Any]:
    monkeypatch.setattr(modeling, "_make_bridge", lambda settings: bridge)
    server = _Server()
    modeling._register_tools(server, Settings())
    return server.tools


def test_save_document_tool_returns_saved_path_and_revision_metadata(monkeypatch: Any) -> None:
    tools = _registered_tools(
        monkeypatch,
        _Bridge(
            {
                "document_id": "demo",
                "saved_path": "/workspace/demo.fcstd",
                "revision": 5,
                "meta_before": {"document_id": "demo", "revision": 4},
            }
        ),
    )

    result = tools["save_document"](modeling.SaveDocumentParams(document_id="demo"))

    assert isinstance(result, modeling.SaveDocumentResult)
    assert result.model_dump() == {
        "document_id": "demo",
        "saved_path": "/workspace/demo.fcstd",
        "revision": 5,
        "meta_before": {"document_id": "demo", "revision": 4},
    }


def test_restore_snapshot_tool_maps_unwrapped_bridge_result(monkeypatch: Any) -> None:
    payload = {
        "document_id": "demo",
        "previous_revision": 4,
        "restored_revision": 5,
        "snapshot_revision": 2,
        "restored_from_snapshot": True,
        "preservation_snapshot": {"revision": 4},
        "validation": {"all_passed": True},
    }
    tools = _registered_tools(monkeypatch, _Bridge(payload))

    result = tools["restore_snapshot"](
        modeling.RestoreSnapshotParams(document_id="demo", revision=2)
    )

    assert result.model_dump() == payload
