"""Focused tests for M19 P2 model-state snapshots and artifact references."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import Client
from pydantic import ValidationError

from freecad_mcp import __version__
from freecad_mcp.app.workflow_contracts import (
    DocumentArtifactRef,
    ModelStateReference,
    ModelStateSnapshot,
    ModelStateSummary,
    ObjectStateSummary,
)
from freecad_mcp.app.workflows import build_document_artifact_ref, build_model_state_snapshot
from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import workspace as ws_mod
from freecad_mcp.server import create_server


def _write_meta(workspace: ws_mod.Workspace, doc_id: str, **fields: object) -> None:
    payload = {"document_id": doc_id, "revision": 1}
    payload.update(fields)
    ws_mod.write_meta(workspace.meta_path(doc_id), payload)


def test_snapshot_and_artifact_contracts_serialize_and_validate() -> None:
    artifact = DocumentArtifactRef(
        document_id="demo",
        workspace_root="/tmp/ws",
        fcstd_path="/tmp/ws/demo.fcstd",
        fcstd_exists=True,
        meta_path="/tmp/ws/demo.meta.json",
        meta_exists=True,
        revision=3,
        snapshot_count=0,
    )
    state_ref = ModelStateReference(
        document_id="demo",
        workspace_root="/tmp/ws",
        revision=3,
        freshness="live_inspection",
        collected_at="2026-01-01T00:00:00+00:00",
    )
    summary = ModelStateSummary(
        object_count=1,
        root_hierarchy_count=1,
        all_valid=True,
        objects=[ObjectStateSummary(name="Box", type="Part::Box", valid=True)],
        hierarchy_available=True,
        validation_available=True,
        truncated=False,
        notes=[],
    )
    snapshot = ModelStateSnapshot(
        document_id="demo",
        workspace_root="/tmp/ws",
        state_ref=state_ref,
        artifact_ref=artifact,
        summary=summary,
    )
    dumped = json.loads(snapshot.model_dump_json())
    assert dumped["document_id"] == "demo"
    assert dumped["artifact_ref"]["revision"] == 3
    assert dumped["artifact_ref"]["fcstd_exists"] is True
    assert dumped["state_ref"]["freshness"] == "live_inspection"
    assert dumped["summary"]["objects"][0]["name"] == "Box"

    with pytest.raises(ValidationError):
        DocumentArtifactRef(  # type: ignore[call-arg]
            document_id="demo",
            workspace_root="/tmp/ws",
            fcstd_exists=True,
            meta_exists=False,
            snapshot_count=0,
            extra="nope",  # type: ignore[arg-type]
        )


def test_artifact_ref_reflects_persisted_metadata_and_missing_files(tmp_path: Path) -> None:
    wks = ws_mod.Workspace(root=tmp_path)
    _write_meta(
        wks,
        "demo",
        revision=4,
        created_at="2026-01-01T00:00:00Z",
        last_saved_at="2026-01-02T00:00:00Z",
        last_modified_at="2026-01-03T00:00:00Z",
    )

    ref = build_document_artifact_ref("demo", workspace=str(tmp_path))
    assert ref.document_id == "demo"
    assert ref.workspace_root == str(tmp_path.resolve())
    assert ref.fcstd_path == str(wks.document_path("demo"))
    assert ref.fcstd_exists is False
    assert ref.meta_path == str(wks.meta_path("demo"))
    assert ref.meta_exists is True
    assert ref.revision == 4
    assert ref.created_at == "2026-01-01T00:00:00Z"
    assert ref.last_saved_at == "2026-01-02T00:00:00Z"
    assert ref.last_modified_at == "2026-01-03T00:00:00Z"
    assert ref.snapshot_count == 0
    assert ref.latest_snapshot_revision is None

    # Add a saved artifact and snapshots; identity/revision refs come from existing mechanisms.
    wks.document_path("demo").write_text("fcstd", encoding="utf-8")
    snaps = wks.root / "snapshots"
    snaps.mkdir()
    (snaps / "demo.r2.fcstd").write_text("s2", encoding="utf-8")
    (snaps / "demo.r5.fcstd").write_text("s5", encoding="utf-8")
    ref2 = build_document_artifact_ref("demo", workspace=str(tmp_path))
    assert ref2.fcstd_exists is True
    assert ref2.snapshot_count == 2
    assert ref2.latest_snapshot_revision == 5
    assert ref2.latest_snapshot_path == str(snaps / "demo.r5.fcstd")

    # No metadata sidecar: revision/timestamps unavailable, explicitly None.
    ref3 = build_document_artifact_ref("emptydoc", workspace=str(tmp_path))
    assert ref3.meta_exists is False
    assert ref3.revision is None
    assert ref3.created_at is None
    assert ref3.last_saved_at is None
    assert ref3.snapshot_count == 0


def test_build_model_state_snapshot_is_bounded_deterministic_and_reports_unavailable(tmp_path: Path) -> None:
    wks = ws_mod.Workspace(root=tmp_path)
    _write_meta(wks, "demo", revision=7)
    wks.document_path("demo").write_text("fcstd", encoding="utf-8")

    calls: list[str] = []

    def runner(op: str, params: dict) -> dict:
        calls.append(op)
        assert params["document_id"] == "demo"
        assert params["workspace"] == str(tmp_path)
        if op == "list_objects":
            return {
                "document_id": "demo",
                "objects": [
                    {"name": "ZObj", "type": "Part::Box", "label": "Z"},
                    {"name": "AObj", "type": "Part::Cylinder", "label": "A", "shape_type": "Solid", "volume": 10.0},
                ],
                "meta": {"revision": 7},
            }
        if op == "get_document_hierarchy":
            return {"document_id": "demo", "hierarchy": [{"name": "AObj"}, {"name": "ZObj"}]}
        if op == "validate":
            return {
                "document_id": "demo",
                "all_valid": True,
                "objects": [
                    {"name": "AObj", "valid": True},
                    {"name": "ZObj", "valid": False},
                ],
            }
        raise AssertionError(f"unexpected op {op}")

    snap = build_model_state_snapshot("demo", workspace=str(tmp_path), op_runner=runner)
    assert calls == ["list_objects", "get_document_hierarchy", "validate"]
    assert snap.document_id == "demo"
    assert snap.workspace_root == str(tmp_path.resolve())
    assert snap.state_ref.document_id == "demo"
    assert snap.state_ref.revision is None
    assert snap.state_ref.freshness == "live_inspection"
    assert snap.state_ref.collected_at
    assert snap.artifact_ref.revision == 7
    assert snap.artifact_ref.fcstd_exists is True
    assert snap.artifact_ref.meta_exists is True
    assert snap.summary.object_count == 2
    assert snap.summary.root_hierarchy_count == 2
    assert snap.summary.all_valid is True
    assert snap.summary.hierarchy_available is True
    assert snap.summary.validation_available is True
    assert snap.summary.truncated is False
    assert [o.name for o in snap.summary.objects] == ["AObj", "ZObj"]
    assert snap.summary.objects[0].valid is True
    assert snap.summary.objects[0].shape_type == "Solid"
    assert snap.summary.objects[1].valid is False
    assert any(
        "live snapshot revision not independently established" in n
        for n in snap.summary.notes
    )

    # Deterministic for same inputs/time-independent fields compared structurally.
    snap2 = build_model_state_snapshot("demo", workspace=str(tmp_path), op_runner=runner)
    assert [o.model_dump() for o in snap.summary.objects] == [o.model_dump() for o in snap2.summary.objects]
    assert snap.summary.model_dump(exclude_none=False) == snap2.summary.model_dump(exclude_none=False)
    assert snap.artifact_ref.model_dump() == snap2.artifact_ref.model_dump()

    # Partial failures: unavailable info reported explicitly, no fabricated data.
    def failing_runner(op: str, params: dict) -> dict:
        if op == "list_objects":
            raise RuntimeError("boom")
        if op == "get_document_hierarchy":
            raise RuntimeError("nope")
        if op == "validate":
            raise RuntimeError("bad")
        raise AssertionError(op)

    snap3 = build_model_state_snapshot("demo", workspace=str(tmp_path), op_runner=failing_runner)
    assert snap3.summary.object_count == 0
    assert snap3.summary.objects == []
    assert snap3.summary.hierarchy_available is False
    assert snap3.summary.validation_available is False
    assert snap3.summary.all_valid is None
    assert any("list_objects unavailable" in n for n in snap3.summary.notes)
    assert any("get_document_hierarchy unavailable" in n for n in snap3.summary.notes)
    assert any("validate unavailable" in n for n in snap3.summary.notes)
    assert any("ownership hierarchy unavailable" in n for n in snap3.summary.notes)
    assert any("validation results unavailable" in n for n in snap3.summary.notes)
    assert snap3.artifact_ref.revision == 7
    assert snap3.state_ref.revision is None
    assert any(
        "live snapshot revision not independently established" in n
        for n in snap3.summary.notes
    )


def test_build_model_state_snapshot_bounds_large_object_lists(tmp_path: Path) -> None:
    wks = ws_mod.Workspace(root=tmp_path)
    _write_meta(wks, "big", revision=1)

    def runner(op: str, params: dict) -> dict:
        if op == "list_objects":
            objs = [{"name": f"Obj{i:03d}", "type": "Part::Feature"} for i in range(60, 0, -1)]
            return {"document_id": "big", "objects": objs, "meta": {"revision": 1}}
        if op == "get_document_hierarchy":
            return {"document_id": "big", "hierarchy": []}
        if op == "validate":
            return {"document_id": "big", "all_valid": True, "objects": []}
        raise AssertionError(op)

    snap = build_model_state_snapshot("big", workspace=str(tmp_path), op_runner=runner)
    assert snap.summary.object_count == 60
    assert len(snap.summary.objects) == 50
    assert snap.summary.truncated is True
    assert [o.name for o in snap.summary.objects][:3] == ["Obj001", "Obj002", "Obj003"]
    assert any("truncated" in n for n in snap.summary.notes)


def test_build_model_state_snapshot_requires_existing_workspace_api_and_reports_empty_document(tmp_path: Path) -> None:
    # Workspace exists but no saved artifact/metadata: snapshot must not claim persistence.
    def runner(op: str, params: dict) -> dict:
        if op == "list_objects":
            return {"document_id": "empty", "objects": [], "meta": {}}
        if op == "get_document_hierarchy":
            return {"document_id": "empty", "hierarchy": []}
        if op == "validate":
            return {"document_id": "empty", "all_valid": True, "objects": []}
        raise AssertionError(op)

    snap = build_model_state_snapshot("empty", workspace=str(tmp_path), op_runner=runner)
    assert snap.summary.object_count == 0
    assert snap.summary.objects == []
    assert snap.artifact_ref.fcstd_exists is False
    assert snap.artifact_ref.meta_exists is False
    assert snap.artifact_ref.revision is None
    assert snap.state_ref.revision is None
    assert not any(
        "live snapshot revision not independently established" in n
        for n in snap.summary.notes
    )


def test_get_model_state_tool_registration_and_structured_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify MCP tool registration/schema and structured result without FreeCAD."""

    from freecad_mcp.app.workflows import build_model_state_snapshot as real_build

    calls: list[tuple[str, dict]] = []

    def fake_run(self, op: str, params: dict, *, timeout=None):
        calls.append((op, dict(params)))
        if op == "list_objects":
            return {"ok": True, "op": op, "result": {"document_id": "demo", "objects": [{"name": "Box", "type": "Part::Box"}], "meta": {}}}
        if op == "get_document_hierarchy":
            return {"ok": True, "op": op, "result": {"document_id": "demo", "hierarchy": [{"name": "Box"}]}}
        if op == "validate":
            return {"ok": True, "op": op, "result": {"document_id": "demo", "all_valid": True, "objects": [{"name": "Box", "valid": True}]}}
        raise AssertionError(op)

    monkeypatch.setattr("freecad_mcp.freecad_bridge.bridge.FreeCadBridge.run", fake_run)
    monkeypatch.setattr("freecad_mcp.freecad_bridge.bridge.FreeCadBridge.__init__", lambda self, *a, **k: None)

    async def _run() -> None:
        async with Client(create_server(Settings())) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools.tools if t.name == "get_model_state")
            assert "snapshot" in (tool.description or "").lower()
            assert "reuses" in (tool.description or "").lower() or "existing" in (tool.description or "").lower()
            assert tool.input_schema.get("type") == "object"
            props = tool.input_schema.get("properties", {})
            assert "document_id" in props
            assert "workspace" in props
            out = getattr(tool, "output_schema", None) or {}
            out_props = out.get("properties", {}) if isinstance(out, dict) else {}
            assert "document_id" in out_props
            assert "artifact_ref" in out_props
            assert "state_ref" in out_props
            assert "summary" in out_props

            result = await client.call_tool(
                "get_model_state", {"document_id": "demo", "workspace": str(tmp_path)}
            )
            data = getattr(result, "structured_content", None)
            if data is None and getattr(result, "content", None):
                text_parts = []
                for item in result.content:
                    text = getattr(item, "text", None)
                    if isinstance(text, str):
                        text_parts.append(text)
                if text_parts:
                    try:
                        data = json.loads("".join(text_parts))
                    except json.JSONDecodeError:
                        data = None
            assert isinstance(data, dict)
            assert data["document_id"] == "demo"
            assert data["workspace_root"] == str(tmp_path.resolve())
            assert data["artifact_ref"]["document_id"] == "demo"
            assert data["artifact_ref"]["fcstd_exists"] is False
            assert data["state_ref"]["freshness"] == "live_inspection"
            assert data["summary"]["object_count"] == 1
            assert data["summary"]["objects"][0]["name"] == "Box"
            assert data["summary"]["all_valid"] is True

    asyncio.run(_run())
    assert [op for op, _ in calls] == ["list_objects", "get_document_hierarchy", "validate"]


def test_live_snapshot_revision_none_when_persisted_state_may_differ(tmp_path: Path) -> None:
    """Persisted sidecar revision is artifact metadata only; live state may be unsaved."""
    wks = ws_mod.Workspace(root=tmp_path)
    _write_meta(wks, "demo", revision=4, last_saved_at="2026-01-01T00:00:00Z")
    wks.document_path("demo").write_text("saved-fcstd", encoding="utf-8")

    def runner(op: str, params: dict) -> dict:
        if op == "list_objects":
            # Live inspection differs from last saved state and may be unsaved.
            return {
                "document_id": "demo",
                "objects": [{"name": "NewUnsavedBox", "type": "Part::Box"}],
                "meta": {"revision": 4},
            }
        if op == "get_document_hierarchy":
            return {"document_id": "demo", "hierarchy": [{"name": "NewUnsavedBox"}]}
        if op == "validate":
            return {
                "document_id": "demo",
                "all_valid": True,
                "objects": [{"name": "NewUnsavedBox", "valid": True}],
            }
        raise AssertionError(op)

    snap = build_model_state_snapshot("demo", workspace=str(tmp_path), op_runner=runner)

    # Persisted artifact metadata remains available.
    assert snap.artifact_ref.fcstd_exists is True
    assert snap.artifact_ref.meta_exists is True
    assert snap.artifact_ref.revision == 4
    assert snap.artifact_ref.last_saved_at == "2026-01-01T00:00:00Z"

    # Live snapshot must not claim correspondence to persisted revision.
    assert snap.state_ref.freshness == "live_inspection"
    assert snap.state_ref.revision is None
    assert snap.summary.objects[0].name == "NewUnsavedBox"
    assert any(
        "live snapshot revision not independently established" in n
        for n in snap.summary.notes
    )


def test_import_boundary_no_freecad_loaded_for_workflow_snapshot_modules() -> None:
    # Ensure P2 modules do not import FreeCAD in parent process.
    before = {m for m in sys.modules if "FreeCAD" in m or m == "Part" or m == "FreeCADGui"}
    import importlib

    importlib.import_module("freecad_mcp.app.workflow_contracts")
    importlib.import_module("freecad_mcp.app.workflows")
    importlib.import_module("freecad_mcp.tools.workflows")
    after = {m for m in sys.modules if "FreeCAD" in m or m == "Part" or m == "FreeCADGui"}
    assert after == before
