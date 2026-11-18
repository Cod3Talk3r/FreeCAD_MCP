"""Native FreeCAD tests for the read-only CAD state adapter (M20 Phase 3A).

Skipped automatically when FreeCAD cannot be discovered. These exercise the
real ``collect_cad_state`` child operation against documents produced by the
existing MCP operations, and assert the headless limitations are reported
explicitly rather than fabricated.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from freecad_mcp.app import CADStateConversionError, CadStateCollector
from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.pathing import for_target

_DISCOVERED = find_freecad_executable()

requires_freecad = pytest.mark.skipif(
    _DISCOVERED is None,
    reason="FreeCAD console executable not found (install FreeCAD or set FREECAD_MCP_FREECAD_EXECUTABLE).",
)


def _settings() -> Settings:
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)


def _workspace(tmp_path: Path) -> str:
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    return for_target(tmp_path, target)


def _build_pad(bridge: FreeCadBridge, workspace: str, document_id: str) -> None:
    params = {"document_id": document_id, "workspace": workspace}
    bridge.run("create_document", params)
    bridge.run("create_sketch", {**params, "sketch_name": "Sketch1"})
    bridge.run(
        "sketch_add_rectangle",
        {**params, "sketch_name": "Sketch1", "x": 0.0, "y": 0.0, "width": 20.0, "height": 20.0},
    )
    bridge.run("sketch_pad", {**params, "sketch_name": "Sketch1", "length": 10.0})


@requires_freecad
def test_collect_state_from_real_pad(tmp_path: Path) -> None:
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = _workspace(tmp_path)
    _build_pad(bridge, workspace, "statepad")

    collector = CadStateCollector(bridge)

    # The raw child payload must be JSON-serializable (parent/child boundary).
    raw = collector.collect_raw("statepad", workspace)
    json.dumps(raw)

    state = collector.collect("statepad", workspace)

    assert state.doc_open is True
    assert state.has_body is True
    names = [node.name for node in state.tree]
    assert "Body" in names
    assert "Sketch1" in names
    assert "Pad" in names

    sketch = next(node for node in state.tree if node.name == "Sketch1")
    assert sketch.num["n_geo"] == 4.0
    assert sketch.geo.get("line") == 4

    assert state.shape.available is True
    assert state.shape.valid is True
    assert state.shape.volume == pytest.approx(4000.0)
    assert state.shape.n_faces == 6
    assert state.shape.n_edges == 12
    assert state.shape.n_solids == 1
    assert state.shape.bbox == pytest.approx((20.0, 20.0, 10.0))
    assert "+Z" in (state.shape.face_dirs or [])
    assert "-Z" in (state.shape.face_dirs or [])

    # Headless limitations: no workbench, edit mode, undo stack, or selection.
    assert state.workbench is None
    assert state.selection == []
    assert "workbench" in state.unavailable_fields
    assert "selection" in state.unavailable_fields
    assert "edit" in state.unavailable_fields
    assert "undo_available" in state.unavailable_fields

    # Conversion fails closed because the Taiga-required workbench is unknown.
    assert "workbench" in state.missing_required_fields()
    with pytest.raises(CADStateConversionError) as excinfo:
        state.to_taiga_state_json()
    assert "workbench" in excinfo.value.missing_fields


@requires_freecad
def test_ambiguous_multi_body_leaves_shape_unavailable(tmp_path: Path) -> None:
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = _workspace(tmp_path)
    _build_pad(bridge, workspace, "statemulti")
    bridge.run("create_body", {"document_id": "statemulti", "workspace": workspace, "body_name": "Body001"})

    state = CadStateCollector(bridge).collect("statemulti", workspace)

    assert state.has_body is True
    assert state.shape.available is False
    missing = state.missing_required_fields()
    assert "shape.valid" in missing
    assert "shape.volume" in missing
    assert "shape.face_dirs" in missing