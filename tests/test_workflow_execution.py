"""Focused tests for M19 P3 deterministic multi-step workflow execution."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from pydantic import ValidationError

from freecad_mcp.app.workflow_contracts import WorkflowResult, WorkflowStepRequest
from freecad_mcp.app.workflows import execute_workflow
from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import workspace as ws_mod
from freecad_mcp.server import create_server


def _write_meta(workspace: ws_mod.Workspace, doc_id: str, **fields: object) -> None:
    payload = {"document_id": doc_id, "revision": 1}
    payload.update(fields)
    ws_mod.write_meta(workspace.meta_path(doc_id), payload)


def _inspection_ok_runner() -> Any:
    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op == "list_objects":
            return {"document_id": params["document_id"], "objects": [{"name": "Box", "type": "Part::Box"}], "meta": {}}
        if op == "get_document_hierarchy":
            return {"document_id": params["document_id"], "hierarchy": [{"name": "Box"}]}
        if op == "validate":
            return {"document_id": params["document_id"], "all_valid": True, "objects": [{"name": "Box", "valid": True}]}
        if op == "inspect_object":
            return {"document_id": params["document_id"], "name": "Box", "type": "Part::Box"}
        return {"ok": True, "op": op, "params": params}

    return runner


def test_workflow_step_request_extra_forbid_and_contracts() -> None:
    req = WorkflowStepRequest(operation="create_box", inspect_after=True, validate_after=True)
    assert req.parameters == {}
    with pytest.raises(ValidationError):
        WorkflowStepRequest(operation="create_box", unknown=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        WorkflowResult(ok=True, status="completed", total_steps=0, completed_steps=0, steps=[], extra=1)  # type: ignore[call-arg]


def test_sequential_ordering_and_successful_multi_step_execution(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((op, dict(params)))
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"done": op, "step": len([c for c in calls if c[0] == op])}

    steps = [
        WorkflowStepRequest(operation="create_document", parameters={"document_id": "doc1"}, label="create"),
        WorkflowStepRequest(operation="create_box", parameters={"length": 10}, label="box"),
        WorkflowStepRequest(operation="recompute", parameters={}, label="recompute"),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is True
    assert result.status == "completed"
    assert result.document_id == "doc1"
    assert result.workspace_root == str(tmp_path.resolve())
    assert result.total_steps == 3
    assert result.completed_steps == 3
    assert [s.operation for s in result.steps] == ["create_document", "create_box", "recompute"]
    assert [s.step_index for s in result.steps] == [0, 1, 2]
    assert all(s.ok and s.status == "completed" and s.error is None for s in result.steps)
    assert [s.result["done"] for s in result.steps] == ["create_document", "create_box", "recompute"]

    # Exact requested/execution order via op_runner calls (no inspection/validation requested).
    assert [c[0] for c in calls] == ["create_document", "create_box", "recompute"]
    # Consistent workflow document/workspace context injected for allowlisted ops.
    for _, params in calls:
        assert params["document_id"] == "doc1"
        assert params["workspace"] == str(tmp_path)
    assert result.artifact_ref is not None
    assert result.artifact_ref["document_id"] == "doc1"


def test_stop_on_first_failure_and_skipped_later_steps(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(op)
        if op == "create_box":
            raise RuntimeError("boom")
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True}

    steps = [
        WorkflowStepRequest(operation="create_document", parameters={}),
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.total_steps == 3
    assert result.completed_steps == 1
    assert [s.status for s in result.steps] == ["completed", "failed", "skipped"]
    assert result.steps[0].ok is True and result.steps[0].result == {"ok": True}
    assert result.steps[1].ok is False
    assert result.steps[1].error and result.steps[1].error["error_type"] == "RuntimeError"
    assert "boom" in result.steps[1].error["message"]
    assert result.steps[2].ok is False and result.steps[2].status == "skipped"
    assert result.steps[2].result is None
    assert result.steps[2].error and result.steps[2].error["error_type"] == "SkippedStep"
    assert "not executed" not in result.steps[2].error["message"].lower() or "skipped" in result.steps[2].error["message"].lower()
    assert calls == ["create_document", "create_box"]  # later step not executed
    assert result.error and result.error["error_type"] == "RuntimeError"


def test_invalid_operation_and_parameters(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(op)
        return {}

    # Unknown/non-allowlisted operation.
    result = execute_workflow(
        document_id="doc1",
        steps=[WorkflowStepRequest(operation="not_a_real_op", parameters={})],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result.ok is False and result.total_steps == 1 and result.completed_steps == 0
    assert result.steps[0].status == "failed"
    assert result.steps[0].error and result.steps[0].error["error_type"] == "UnknownOperation"
    assert calls == []

    # Conflicting document context in step parameters.
    calls.clear()
    result2 = execute_workflow(
        document_id="doc1",
        steps=[
            WorkflowStepRequest(operation="recompute", parameters={"document_id": "other"}),
            WorkflowStepRequest(operation="recompute", parameters={}),
        ],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result2.ok is False
    assert result2.steps[0].status == "failed"
    assert result2.steps[0].error and result2.steps[0].error["error_type"] == "ValueError"
    assert "conflicts" in (result2.steps[0].error or {}).get("message", "")
    assert result2.steps[1].status == "skipped"
    assert calls == []

    # Invalid/unsafe document id (existing workspace validation reused).
    result3 = execute_workflow(
        document_id="../bad",
        steps=[WorkflowStepRequest(operation="recompute", parameters={})],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result3.ok is False
    assert result3.error and result3.steps == []


def test_inspect_after_success_attaches_state_and_artifact_refs(tmp_path: Path) -> None:
    _write_meta(ws_mod.Workspace(root=tmp_path), "doc1", revision=5, last_saved_at="2026-01-01T00:00:00Z")
    calls: list[str] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(op)
        if op == "create_box":
            return {"name": "Box"}
        return _inspection_ok_runner()(op, params)

    result = execute_workflow(
        document_id="doc1",
        steps=[WorkflowStepRequest(operation="create_box", parameters={"length": 1}, inspect_after=True)],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result.ok is True and result.steps[0].status == "completed"
    step = result.steps[0]
    assert step.inspection and step.inspection["ok"] is True
    assert isinstance(step.inspection.get("snapshot"), dict)
    assert step.state_ref and step.state_ref["freshness"] == "live_inspection"
    assert step.state_ref["revision"] is None  # P2 semantics preserved
    assert step.artifact_ref and step.artifact_ref["document_id"] == "doc1"
    assert step.artifact_ref["revision"] == 5  # persisted metadata ref
    assert calls[0] == "create_box"
    assert "list_objects" in calls and "get_document_hierarchy" in calls and "validate" in calls

    # inspect_after can request specific inspection operations (allowlisted).
    calls.clear()
    result2 = execute_workflow(
        document_id="doc1",
        steps=[WorkflowStepRequest(operation="create_box", parameters={}, inspect_after=["inspect_object"])],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result2.ok is True
    assert result2.steps[0].inspection and result2.steps[0].inspection["ok"] is True
    assert result2.steps[0].inspection["operations"]["inspect_object"]["name"] == "Box"
    assert "inspect_object" in calls
    for op, params in [(o, p) for o, p in zip(calls, [{}] * len(calls))]:
        pass
    # Additional inspection ops must not duplicate core snapshot ops and must use same context.
    calls.clear()
    result3 = execute_workflow(
        document_id="doc1",
        steps=[
            WorkflowStepRequest(
                operation="create_box",
                parameters={},
                inspect_after=["validate", "inspect_object", "list_objects"],
            )
        ],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result3.ok is True and result3.completed_steps == 1
    step3 = result3.steps[0]
    assert step3.status == "completed" and step3.ok is True
    assert step3.inspection and step3.inspection["ok"] is True
    assert "snapshot" in step3.inspection
    assert step3.inspection["operations"] == {
        "inspect_object": {"document_id": "doc1", "name": "Box", "type": "Part::Box"}
    }
    # Core snapshot ops run once (as part of snapshot), plus explicit extra op; no duplicates.
    assert calls.count("list_objects") == 1
    assert calls.count("get_document_hierarchy") == 1
    assert calls.count("validate") == 1
    assert calls.count("inspect_object") == 1
    assert calls[0] == "create_box"
    assert step3.state_ref and step3.state_ref["document_id"] == "doc1"
    assert step3.state_ref["workspace_root"] == str(tmp_path.resolve())
    assert step3.artifact_ref and step3.artifact_ref["document_id"] == "doc1"


def test_inspect_after_failure_distinct_from_operation_failure(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(op)
        if op == "create_box":
            return {"name": "Box"}
        if op == "list_objects":
            raise RuntimeError("inspect boom")
        if op in ("get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {}

    result = execute_workflow(
        document_id="doc1",
        steps=[WorkflowStepRequest(operation="create_box", parameters={}, inspect_after=True)],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result.ok is False and result.completed_steps == 0
    step = result.steps[0]
    assert step.status == "failed" and step.ok is False
    assert step.result == {"name": "Box"}  # operation succeeded
    assert step.error and step.error.get("operation_succeeded") is True
    assert step.error.get("inspection_failed") is True
    assert step.inspection and step.inspection["ok"] is False
    assert "inspect boom" in (step.inspection["error"]["message"])
    assert step.artifact_ref is None and step.state_ref is None


def test_validate_after_behavior_separate_from_operation_success(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(op)
        if op == "create_box":
            return {"name": "Box"}
        if op == "validate":
            raise RuntimeError("validate boom")
        if op in ("list_objects", "get_document_hierarchy"):
            return _inspection_ok_runner()(op, params)
        return {}

    result = execute_workflow(
        document_id="doc1",
        steps=[WorkflowStepRequest(operation="create_box", parameters={}, validate_after=True)],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result.ok is False
    step = result.steps[0]
    assert step.result == {"name": "Box"}  # operation succeeded
    assert step.validation and step.validation["ok"] is False
    assert "validate boom" in step.validation["error"]["message"]
    assert step.error and step.error.get("operation_succeeded") is True
    assert step.error.get("validation_failed") is True
    assert step.inspection is None
    assert calls == ["create_box", "validate"]


def test_validate_after_success_payload(tmp_path: Path) -> None:
    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op == "create_box":
            return {"name": "Box"}
        if op == "validate":
            return {"document_id": "doc1", "all_valid": True, "objects": []}
        if op in ("list_objects", "get_document_hierarchy"):
            return _inspection_ok_runner()(op, params)
        return {}

    result = execute_workflow(
        document_id="doc1",
        steps=[WorkflowStepRequest(operation="create_box", parameters={}, validate_after=True)],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    assert result.ok is True
    step = result.steps[0]
    assert step.status == "completed"
    assert step.validation and step.validation["ok"] is True
    assert step.validation["result"]["all_valid"] is True


def test_structured_result_error_semantics_and_consistent_context(tmp_path: Path) -> None:
    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op == "create_box":
            return {"name": "Box"}
        return _inspection_ok_runner()(op, params)

    result = execute_workflow(
        document_id="doc1",
        steps=[
            WorkflowStepRequest(operation="create_box", parameters={"document_id": "doc1", "length": 1}, label="b"),
        ],
        workspace=str(tmp_path),
        op_runner=runner,
    )
    dumped = json.loads(WorkflowResult.model_validate(result.model_dump()).model_dump_json())
    assert dumped["ok"] is True and dumped["status"] == "completed"
    assert dumped["steps"][0]["label"] == "b"
    assert dumped["steps"][0]["status"] == "completed"
    assert dumped["steps"][0]["error"] is None
    assert dumped["artifact_ref"]["workspace_root"] == str(tmp_path.resolve())


def test_run_workflow_mcp_registration_schema_and_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run(self, op: str, params: dict[str, Any], *, timeout=None):
        calls.append((op, dict(params)))
        if op == "create_box":
            return {"ok": True, "op": op, "result": {"name": "Box"}}
        if op == "list_objects":
            return {"ok": True, "op": op, "result": {"document_id": "doc1", "objects": [{"name": "Box", "type": "Part::Box"}], "meta": {}}}
        if op == "get_document_hierarchy":
            return {"ok": True, "op": op, "result": {"document_id": "doc1", "hierarchy": [{"name": "Box"}]}}
        if op == "validate":
            return {"ok": True, "op": op, "result": {"document_id": "doc1", "all_valid": True, "objects": [{"name": "Box", "valid": True}]}}
        raise AssertionError(op)

    monkeypatch.setattr("freecad_mcp.freecad_bridge.bridge.FreeCadBridge.run", fake_run)
    monkeypatch.setattr("freecad_mcp.freecad_bridge.bridge.FreeCadBridge.__init__", lambda self, *a, **k: None)

    async def _run() -> None:
        async with Client(create_server(Settings())) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools.tools if t.name == "run_workflow")
            assert "sequential" in (tool.description or "").lower()
            assert "inspect_after" in (tool.description or "")
            assert "validate_after" in (tool.description or "")
            props = tool.input_schema.get("properties", {})
            assert "document_id" in props and "steps" in props and "workspace" in props
            out_props = (getattr(tool, "output_schema", None) or {}).get("properties", {})
            assert "steps" in out_props and "ok" in out_props and "status" in out_props

            result = await client.call_tool(
                "run_workflow",
                {
                    "document_id": "doc1",
                    "workspace": str(tmp_path),
                    "steps": [{"operation": "create_box", "parameters": {"length": 10}, "inspect_after": True, "validate_after": True}],
                },
            )
            data = getattr(result, "structured_content", None)
            if data is None and getattr(result, "content", None):
                text = "".join(getattr(c, "text", "") for c in result.content if getattr(c, "text", None))
                data = json.loads(text) if text else None
            assert isinstance(data, dict)
            assert data["ok"] is True and data["status"] == "completed"
            assert data["document_id"] == "doc1"
            assert len(data["steps"]) == 1
            step = data["steps"][0]
            assert step["operation"] == "create_box" and step["status"] == "completed"
            assert step["result"] == {"name": "Box"}
            assert step["validation"]["ok"] is True
            assert step["inspection"]["ok"] is True
            assert step["state_ref"]["freshness"] == "live_inspection"
            assert step["state_ref"]["revision"] is None

    asyncio.run(_run())
    # create_box + validate_after + inspect snapshot ops (list, hierarchy, validate)
    assert calls[0][0] == "create_box"
    assert "validate" in [c[0] for c in calls[1:]]


def test_workflow_execution_modules_remain_freecad_free() -> None:
    before = {m for m in sys.modules if "FreeCAD" in m or m == "Part" or m == "FreeCADGui"}
    import importlib

    importlib.import_module("freecad_mcp.app.workflows")
    importlib.import_module("freecad_mcp.app.workflow_contracts")
    importlib.import_module("freecad_mcp.tools.workflows")
    after = {m for m in sys.modules if "FreeCAD" in m or m == "Part" or m == "FreeCADGui"}
    assert after == before


# =============================================================================
# M19 P4 Recovery Tests
# =============================================================================


def _pre_step_snapshot_runner() -> Any:
    """Runner that simulates save_document + create_snapshot + operation."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == "restore_snapshot":
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 6, "validation": {"all_passed": True}}}
        if op == "list_objects":
            return {"document_id": params["document_id"], "objects": [{"name": "Box", "type": "Part::Box"}], "meta": {}}
        if op == "get_document_hierarchy":
            return {"document_id": params["document_id"], "hierarchy": [{"name": "Box"}]}
        if op == "validate":
            return {"document_id": params["document_id"], "all_valid": True, "objects": [{"name": "Box", "valid": True}]}
        return {"ok": True, "op": op, "params": params}

    runner.call_log = call_log  # type: ignore[attr-defined]
    return runner


def _failing_operation_runner(fail_op: str = "create_box") -> Any:
    """Runner where a specific operation fails."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == fail_op:
            return {"ok": False, "op": op, "error": {"message": f"{fail_op} failed", "error_type": "OperationError"}}
        if op == "restore_snapshot":
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 6, "validation": {"all_passed": True}}}
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    runner.call_log = call_log  # type: ignore[attr-defined]
    return runner


def _recovery_restore_fails_runner() -> Any:
    """Runner where restore_snapshot fails."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == "create_box":
            return {"ok": False, "op": "create_box", "error": {"message": "create_box failed", "error_type": "OperationError"}}
        if op == "restore_snapshot":
            return {"ok": False, "op": "restore_snapshot", "error": "snapshot not found"}
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    runner.call_log = call_log  # type: ignore[attr-defined]
    return runner


def _recovery_post_validation_fails_runner() -> Any:
    """Runner where post-recovery validation fails."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == "create_box":
            return {"ok": False, "op": "create_box", "error": {"message": "create_box failed", "error_type": "OperationError"}}
        if op == "restore_snapshot":
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 6, "validation": {"all_passed": True}}}
        if op == "list_objects":
            return {"document_id": params["document_id"], "objects": [{"name": "Box", "type": "Part::Box"}], "meta": {}}
        if op == "get_document_hierarchy":
            return {"document_id": params["document_id"], "hierarchy": [{"name": "Box"}]}
        if op == "validate":
            return {"document_id": params["document_id"], "all_valid": False, "objects": [{"name": "Box", "valid": False}]}
        return {"ok": True, "op": op, "params": params}

    runner.call_log = call_log  # type: ignore[attr-defined]
    return runner


def test_recovery_disabled_by_default(tmp_path: Path) -> None:
    """Recovery is disabled by default; operation failure stops workflow."""
    runner = _failing_operation_runner()

    steps = [
        WorkflowStepRequest(operation="create_document", parameters={}),
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.completed_steps == 1
    assert result.steps[1].status == "failed"
    assert result.steps[1].recovery is None
    assert result.steps[2].status == "skipped"
    # save_document and create_snapshot should NOT be called
    save_calls = [c for c in runner.call_log if c[0] == "save_document"]
    snapshot_calls = [c for c in runner.call_log if c[0] == "create_snapshot"]
    assert len(save_calls) == 0
    assert len(snapshot_calls) == 0


def test_pre_step_save_failure_prevents_operation(tmp_path: Path) -> None:
    """If save_document fails, the step fails and operation is not executed."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": False, "op": "save_document", "error": "save failed"}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.steps[0].status == "failed"
    assert result.steps[0].error and result.steps[0].error["error_type"] == "PreStepSaveFailed"
    assert result.steps[0].recovery and result.steps[0].recovery["attempted"] is False
    # create_snapshot should NOT be called
    snapshot_calls = [c for c in call_log if c[0] == "create_snapshot"]
    assert len(snapshot_calls) == 0
    # Operation should NOT be executed
    op_calls = [c for c in call_log if c[0] == "create_box"]
    assert len(op_calls) == 0


def test_pre_step_snapshot_failure_prevents_operation(tmp_path: Path) -> None:
    """If create_snapshot fails, the step fails and operation is not executed."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": False, "op": "create_snapshot", "error": "snapshot failed"}
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.steps[0].status == "failed"
    assert result.steps[0].error and result.steps[0].error["error_type"] == "PreStepSnapshotFailed"
    assert result.steps[0].recovery and result.steps[0].recovery["attempted"] is False
    # Operation should NOT be executed
    op_calls = [c for c in call_log if c[0] == "create_box"]
    assert len(op_calls) == 0


def test_exact_pre_step_snapshot_revision_used(tmp_path: Path) -> None:
    """The exact revision from create_snapshot response is used for recovery."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 42}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 42}}
        if op == "create_box":
            return {"ok": False, "op": "create_box", "error": {"message": "failed", "error_type": "OpError"}}
        if op == "restore_snapshot":
            # Verify the exact revision is passed
            assert params["revision"] == 42, f"Expected revision 42, got {params['revision']}"
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 43, "validation": {"all_passed": True}}}
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False  # Workflow stopped after recovery
    assert result.status == "recovered"
    assert result.steps[0].status == "recovered"
    assert result.steps[0].ok is True
    assert result.steps[0].recovery and result.steps[0].recovery["target_revision"] == 42
    assert result.steps[0].recovery["restoration_ok"] is True
    assert result.steps[0].recovery["validation_ok"] is True
    assert result.completed_steps == 0  # Recovered step not counted


def test_operation_failure_triggers_recovery(tmp_path: Path) -> None:
    """Operation failure with recover_on_failure triggers recovery."""
    runner = _failing_operation_runner()

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False  # Workflow stopped after recovery (default stop)
    assert result.status == "recovered"
    assert result.steps[0].status == "recovered"
    assert result.steps[0].ok is True
    assert result.steps[0].recovery is not None
    assert result.steps[0].recovery["attempted"] is True
    assert result.steps[0].recovery["restoration_ok"] is True
    assert result.steps[0].recovery["validation_ok"] is True
    assert result.steps[0].recovery["target_revision"] == 5
    assert result.completed_steps == 0  # Recovered step not counted


def test_restore_failure_marks_step_failed(tmp_path: Path) -> None:
    """If restore_snapshot fails, step is marked failed with recovery details."""
    runner = _recovery_restore_fails_runner()

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert result.steps[0].ok is False
    assert result.steps[0].recovery is not None
    assert result.steps[0].recovery["attempted"] is True
    assert result.steps[0].recovery["restoration_ok"] is False
    assert "snapshot not found" in (result.steps[0].recovery["error"] or "")
    assert result.completed_steps == 0


def test_post_recovery_verification_failure(tmp_path: Path) -> None:
    """If post-recovery validation fails, step is marked failed."""
    runner = _recovery_post_validation_fails_runner()

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert result.steps[0].recovery is not None
    assert result.steps[0].recovery["restoration_ok"] is True
    assert result.steps[0].recovery["validation_ok"] is False
    assert "validation failed" in (result.steps[0].recovery["error"] or "").lower()


def test_recovered_step_not_counted_in_completed_steps(tmp_path: Path) -> None:
    """Recovered step has ok=True but is not counted in completed_steps."""
    runner = _failing_operation_runner()

    steps = [
        WorkflowStepRequest(operation="create_document", parameters={}),
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    # Step 0 completed, step 1 recovered, step 2 skipped (workflow stops after recovery)
    assert result.steps[0].status == "completed"
    assert result.steps[1].status == "recovered"
    assert result.steps[2].status == "skipped"
    assert result.completed_steps == 1  # Only step 0 counted
    assert result.total_steps == 3


def test_workflow_status_recovered_when_recovery_succeeds(tmp_path: Path) -> None:
    """Workflow status is 'recovered' when at least one step recovered and no later failure."""
    runner = _failing_operation_runner()

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "recovered"


def test_workflow_status_failed_when_later_step_fails_after_recovery(tmp_path: Path) -> None:
    """If a later step fails after successful recovery, workflow status is 'failed'."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == "create_box":
            # First call fails, second call (after recovery) succeeds
            if len([c for c in call_log if c[0] == "create_box"]) == 1:
                return {"ok": False, "op": "create_box", "error": {"message": "failed", "error_type": "OpError"}}
            return {"ok": True, "op": "create_box", "result": {"name": "Box"}}
        if op == "recompute":
            return {"ok": False, "op": "recompute", "error": {"message": "recompute failed", "error_type": "OpError"}}
        if op == "restore_snapshot":
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 6, "validation": {"all_passed": True}}}
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True, recovery_action="continue"),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.steps[0].status == "recovered"
    assert result.steps[1].status == "failed"


def test_recovery_action_continue_proceeds_to_next_step(tmp_path: Path) -> None:
    """recovery_action='continue' proceeds to next step after successful recovery."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == "create_box":
            if len([c for c in call_log if c[0] == "create_box"]) == 1:
                return {"ok": False, "op": "create_box", "error": {"message": "failed", "error_type": "OpError"}}
            return {"ok": True, "op": "create_box", "result": {"name": "Box"}}
        if op == "recompute":
            return {"ok": True, "op": "recompute", "result": {"done": "recompute"}}
        if op == "restore_snapshot":
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 6, "validation": {"all_passed": True}}}
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True, recovery_action="continue"),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False  # Not all steps completed (step 0 recovered)
    assert result.status == "recovered"
    assert result.steps[0].status == "recovered"
    assert result.steps[1].status == "completed"
    assert result.completed_steps == 1  # Only step 1 completed


def test_recovery_action_stop_halts_workflow(tmp_path: Path) -> None:
    """recovery_action='stop' (default) halts workflow after recovery."""
    runner = _failing_operation_runner()

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True, recovery_action="stop"),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "recovered"
    assert result.steps[0].status == "recovered"
    assert result.steps[1].status == "skipped"
    assert result.completed_steps == 0


def test_validation_failure_does_not_trigger_recovery(tmp_path: Path) -> None:
    """Validation failure does not trigger recovery by default."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "create_box":
            return {"ok": True, "op": "create_box", "result": {"name": "Box"}}
        if op == "validate":
            return {"ok": True, "op": "validate", "result": {"all_valid": False, "objects": [{"name": "Box", "valid": False}]}}
        if op in ("save_document", "create_snapshot", "restore_snapshot"):
            return {"ok": True, "op": op, "result": {"revision": 5}}
        if op in ("list_objects", "get_document_hierarchy"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True, validate_after=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert result.steps[0].error and result.steps[0].error.get("validation_failed") is True
    # Recovery should not be attempted (restore_snapshot not called)
    assert result.steps[0].recovery is None
    # Pre-step preparation (save_document, create_snapshot) still runs for recovery-enabled steps
    save_calls = [c for c in call_log if c[0] == "save_document"]
    snapshot_calls = [c for c in call_log if c[0] == "create_snapshot"]
    restore_calls = [c for c in call_log if c[0] == "restore_snapshot"]
    assert len(save_calls) == 1
    assert len(snapshot_calls) == 1
    assert len(restore_calls) == 0


def test_inspection_failure_does_not_trigger_recovery(tmp_path: Path) -> None:
    """Inspection failure does not trigger recovery."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "create_box":
            return {"ok": True, "op": "create_box", "result": {"name": "Box"}}
        if op == "list_objects":
            raise RuntimeError("inspect boom")
        if op in ("get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        if op in ("save_document", "create_snapshot", "restore_snapshot"):
            return {"ok": True, "op": op, "result": {"revision": 5}}
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True, inspect_after=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.steps[0].status == "failed"
    assert result.steps[0].error and result.steps[0].error.get("inspection_failed") is True
    # Recovery should not be attempted (restore_snapshot not called)
    assert result.steps[0].recovery is None
    # Pre-step preparation (save_document, create_snapshot) still runs for recovery-enabled steps
    save_calls = [c for c in call_log if c[0] == "save_document"]
    snapshot_calls = [c for c in call_log if c[0] == "create_snapshot"]
    restore_calls = [c for c in call_log if c[0] == "restore_snapshot"]
    assert len(save_calls) == 1
    assert len(snapshot_calls) == 1
    assert len(restore_calls) == 0


def test_document_workspace_mismatch_protection_in_recovery(tmp_path: Path) -> None:
    """Recovery uses consistent document_id and workspace context."""
    call_log: list[tuple[str, dict[str, Any]]] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        call_log.append((op, dict(params)))
        if op == "save_document":
            assert params["document_id"] == "doc1"
            assert params["workspace"] == str(tmp_path)
            return {"ok": True, "op": "save_document", "result": {"revision": 5}}
        if op == "create_snapshot":
            assert params["document_id"] == "doc1"
            assert params["workspace"] == str(tmp_path)
            return {"ok": True, "op": "create_snapshot", "result": {"revision": 5}}
        if op == "create_box":
            return {"ok": False, "op": "create_box", "error": {"message": "failed", "error_type": "OpError"}}
        if op == "restore_snapshot":
            assert params["document_id"] == "doc1"
            assert params["workspace"] == str(tmp_path)
            assert params["revision"] == 5
            return {"ok": True, "op": "restore_snapshot", "result": {"restored_revision": 6, "validation": {"all_passed": True}}}
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True, "op": op, "params": params}

    steps = [
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}, recover_on_failure=True),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.steps[0].status == "recovered"


def test_existing_p3_behavior_unchanged_without_recovery(tmp_path: Path) -> None:
    """Workflows without recover_on_failure behave exactly as P3."""
    calls: list[str] = []

    def runner(op: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(op)
        if op == "create_box":
            raise RuntimeError("boom")
        if op in ("list_objects", "get_document_hierarchy", "validate"):
            return _inspection_ok_runner()(op, params)
        return {"ok": True}

    steps = [
        WorkflowStepRequest(operation="create_document", parameters={}),
        WorkflowStepRequest(operation="create_box", parameters={"length": 1}),
        WorkflowStepRequest(operation="recompute", parameters={}),
    ]
    result = execute_workflow(document_id="doc1", steps=steps, workspace=str(tmp_path), op_runner=runner)

    assert result.ok is False
    assert result.status == "failed"
    assert result.completed_steps == 1
    assert [s.status for s in result.steps] == ["completed", "failed", "skipped"]
    assert calls == ["create_document", "create_box"]
    # save_document and create_snapshot should NOT be called
    assert "save_document" not in calls
    assert "create_snapshot" not in calls
