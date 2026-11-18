"""Mock tests for the read-only CAD state collection adapter (M20 Phase 3A).

The adapter normalizes a JSON payload produced by the FreeCAD child's
``collect_cad_state`` operation. These tests use a fake executor so no FreeCAD
process is required; native behavior is covered separately.
"""

from __future__ import annotations

from typing import Any

import pytest

from freecad_mcp.app import (
    CADStateConversionError,
    CadStateCollector,
    normalize_cad_state,
)
from freecad_mcp.freecad_bridge import FreeCadOperationError

_RAW: dict[str, Any] = {
    "document_id": "doc1",
    "doc_open": True,
    "workbench": "PartDesignWorkbench",
    "edit": None,
    "has_body": True,
    "undo_available": None,
    "tree": [
        {
            "name": "Body",
            "type": "PartDesign::Body",
            "parent": -1,
            "depth": 0,
            "num": {"tip": 0.0},
            "geo": {},
            "cons": {},
        },
        {
            "name": "Sketch",
            "type": "Sketcher::SketchObject",
            "parent": 0,
            "depth": 1,
            "num": {
                "n_geo": 2.0,
                "n_construction": 0.0,
                "n_constraints": 2.0,
                "dof": 5.0,
                "fully_constrained": 0.0,
            },
            "geo": {"line": 1, "circle": 1},
            "cons": {"Horizontal": 1, "Radius": 1},
        },
        {
            "name": "Pad",
            "type": "PartDesign::Pad",
            "parent": 0,
            "depth": 1,
            "num": {"tip": 1.0, "valid": 1.0, "n_faces": 6.0},
            "geo": {},
            "cons": {},
        },
    ],
    "selection": [
        {
            "kind": "face",
            "object": "Pad",
            "object_type": "PartDesign::Pad",
            "subs": ["Face1"],
            "normal": [0.0, 0.0, 1.0],
            "offset": 3.0,
            "count": 1,
        }
    ],
    "recent": [],
    "events": [],
    "shape": {
        "valid": True,
        "volume": 150.0,
        "area": 190.0,
        "bbox": [10.0, 5.0, 3.0],
        "n_faces": 6,
        "n_edges": 12,
        "n_solids": 1,
        "face_dirs": ["+X", "-X", "+Y", "-Y", "+Z", "-Z"],
    },
    "shape_available": True,
    "shape_source": "Pad",
    "recent_flags": [],
    "unavailable_fields": ["edit", "undo_available", "recent", "events"],
    "collected_at": "2026-11-18T00:00:00+00:00",
}


class FakeExecutor:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def run(self, op: str, params: dict[str, Any], **kwargs: Any) -> Any:
        self.calls.append((op, params, kwargs))
        return self.result


def test_normalize_full_payload_builds_cad_state() -> None:
    state = normalize_cad_state(_RAW)

    assert state.source == "freecad_live"
    assert state.doc_open is True
    assert state.workbench == "PartDesignWorkbench"
    assert state.has_body is True
    assert state.missing_required_fields() == []

    sketch = state.tree[1]
    assert sketch.name == "Sketch"
    assert sketch.parent == 0
    assert sketch.depth == 1
    assert sketch.num["dof"] == 5.0
    assert sketch.geo == {"line": 1, "circle": 1}
    assert sketch.cons == {"Horizontal": 1, "Radius": 1}

    assert state.selection[0].normal == (0.0, 0.0, 1.0)
    assert state.shape.available is True
    assert state.shape.volume == 150.0
    assert state.unavailable_fields == ["edit", "undo_available", "recent", "events"]


def test_full_payload_converts_to_taiga_state() -> None:
    payload = normalize_cad_state(_RAW).to_taiga_state_json()

    assert payload["doc_open"] is True
    assert payload["workbench"] == "PartDesignWorkbench"
    assert payload["shape"]["bbox"] == [10.0, 5.0, 3.0]
    assert payload["shape"]["volume"] == 150.0
    assert payload["selection"][0]["normal"] == [0.0, 0.0, 1.0]
    assert payload["tree"][1]["num"]["n_geo"] == 2.0


def test_missing_workbench_blocks_conversion() -> None:
    raw = dict(_RAW)
    raw["workbench"] = None
    raw["unavailable_fields"] = ["workbench", "edit", "undo_available"]

    state = normalize_cad_state(raw)

    assert state.workbench is None
    assert "workbench" in state.missing_required_fields()
    with pytest.raises(CADStateConversionError) as excinfo:
        state.to_taiga_state_json()
    assert "workbench" in excinfo.value.missing_fields


def test_unavailable_shape_is_not_fabricated() -> None:
    raw = dict(_RAW)
    raw["shape"] = {
        "valid": None,
        "volume": None,
        "area": None,
        "bbox": None,
        "n_faces": None,
        "n_edges": None,
        "n_solids": None,
        "face_dirs": None,
    }
    raw["shape_available"] = False

    state = normalize_cad_state(raw)

    assert state.shape.available is False
    missing = state.missing_required_fields()
    for field in (
        "shape.valid",
        "shape.volume",
        "shape.n_faces",
        "shape.n_edges",
        "shape.n_solids",
        "shape.face_dirs",
    ):
        assert field in missing
    with pytest.raises(CADStateConversionError):
        state.to_taiga_state_json()


def test_malformed_values_become_none_not_defaults() -> None:
    raw = dict(_RAW)
    raw["shape"] = {
        "valid": "yes",
        "volume": "150.0",
        "area": None,
        "bbox": [10.0, 5.0],
        "n_faces": 3.5,
        "n_edges": 12,
        "n_solids": 1,
        "face_dirs": "not-a-list",
    }

    state = normalize_cad_state(raw)

    assert state.shape.valid is None
    assert state.shape.volume is None
    assert state.shape.bbox is None  # not exactly three numbers
    assert state.shape.n_faces is None  # not an int
    assert state.shape.face_dirs is None
    assert state.shape.n_edges == 12


def test_selection_without_normal_blocks_conversion() -> None:
    raw = dict(_RAW)
    raw["selection"] = [
        {
            "kind": "face",
            "object": "Pad",
            "object_type": "PartDesign::Pad",
            "subs": ["Face1"],
            "normal": None,
            "offset": None,
            "count": 1,
        }
    ]

    state = normalize_cad_state(raw)

    assert "selection[].normal" in state.missing_required_fields()
    with pytest.raises(CADStateConversionError):
        state.to_taiga_state_json()


def test_collector_calls_bridge_and_normalizes() -> None:
    executor = FakeExecutor(_RAW)
    state = CadStateCollector(executor).collect("doc1", "ws")

    assert executor.calls == [
        (
            "collect_cad_state",
            {"document_id": "doc1", "workspace": "ws"},
            {},
        )
    ]
    assert state.doc_open is True
    assert state.shape.available is True


def test_collector_forwards_timeout() -> None:
    executor = FakeExecutor(_RAW)
    CadStateCollector(executor).collect("doc1", None, timeout=5.0)

    assert executor.calls[0][2] == {"timeout": 5.0}


def test_collector_rejects_non_mapping_result() -> None:
    executor = FakeExecutor(["not", "a", "mapping"])

    with pytest.raises(FreeCadOperationError):
        CadStateCollector(executor).collect("doc1")