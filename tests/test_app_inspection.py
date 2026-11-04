"""Application inspection contracts and DirectInspectionBackend delegation.

These tests run without FreeCAD or Taiga: the backend is driven by a mock
executor that stands in for ``FreeCadBridge``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest

from freecad_mcp.app.contracts import BuildRequest
from freecad_mcp.app.direct import DirectBackend
from freecad_mcp.app.inspection import (
    DocumentInspection,
    InspectionBackend,
    InspectionRequest,
    InspectionResult,
    ObjectInspection,
    ShapeInfo,
    _build_shape_info,
)
from freecad_mcp.app.inspection import DirectInspectionBackend
from freecad_mcp.app.contracts import ApplicationError
from freecad_mcp.freecad_bridge import FreeCadOperationError


# ---------------------------------------------------------------------------
# Contract defaults and immutability
# ---------------------------------------------------------------------------


def test_shape_info_defaults_are_all_none() -> None:
    info = ShapeInfo()
    assert info.shape_type is None
    assert info.valid is None
    assert info.volume is None
    assert info.area is None
    assert info.length is None
    assert info.width is None
    assert info.height is None
    assert info.solid_count is None
    assert info.face_count is None
    assert info.edge_count is None
    assert info.vertex_count is None


def test_shape_info_from_mapping_populates_available_fields() -> None:
    info = _build_shape_info(
        {
            "shape_type": "Solid",
            "shape_valid": True,
            "volume": 3000.0,
            "length": 10.0,
            "width": 5.0,
            "height": 3.0,
        }
    )
    assert info.shape_type == "Solid"
    assert info.valid is True
    assert info.volume == 3000.0
    assert info.length == 10.0
    assert info.width == 5.0
    assert info.height == 3.0
    # Not available from current bridge operations
    assert info.area is None
    assert info.solid_count is None


def test_shape_info_without_shape_type_is_empty() -> None:
    assert _build_shape_info({}) == ShapeInfo()
    assert _build_shape_info({"volume": 5.0}) == ShapeInfo()


def test_object_inspection_immutable() -> None:
    obj = ObjectInspection(name="Box1", type="Part::Box")
    with pytest.raises(FrozenInstanceError):
        obj.name = "other"


def test_document_inspection_defaults() -> None:
    doc = DocumentInspection(document_id="demo")
    assert doc.document_id == "demo"
    assert doc.objects == ()
    assert doc.object_count == 0
    assert doc.all_valid is None
    assert doc.meta == {}


def test_inspection_result_frozen() -> None:
    result = InspectionResult(ok=True, document_id="demo")
    with pytest.raises(FrozenInstanceError):
        result.ok = False


def test_build_request_supports_document_id() -> None:
    # document_id is optional and defaults to None
    req = BuildRequest("create_box", {"length": 10})
    assert req.document_id is None

    # document_id can be supplied as a keyword
    req2 = BuildRequest("list_objects", {}, document_id="demo")
    assert req2.document_id == "demo"

    # Existing callers using positional args are unaffected
    req3 = BuildRequest("build", {"goal": {}}, 12.0)
    assert req3.document_id is None


# ---------------------------------------------------------------------------
# DirectInspectionBackend delegation
# ---------------------------------------------------------------------------


def _make_list_result(objects: list[dict]) -> dict:
    return {"document_id": "demo", "objects": objects}


def _make_inspect_result(info: dict) -> dict:
    return {
        "document_id": "demo",
        "object_name": info.get("name", ""),
        "info": info,
    }


def test_direct_inspection_delegates_to_list_objects() -> None:
    executor = Mock()
    executor.run.side_effect = [
        _make_list_result(
            [
                {"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 3000.0},
                {"name": "Sketch1", "type": "Sketcher::SketchObject"},
            ]
        ),
        _make_inspect_result(
            {"name": "Box1", "type": "Part::Box", "label": "Box", "shape_type": "Solid", "volume": 3000.0, "shape_valid": True}
        ),
        _make_inspect_result(
            {"name": "Sketch1", "type": "Sketcher::SketchObject", "label": "Sketch"}
        ),
    ]
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id="demo"))

    assert result.ok is True
    assert result.document_id == "demo"
    assert result.document is not None
    assert result.document.object_count == 2
    assert result.error is None

    executor.run.assert_any_call("list_objects", {"document_id": "demo", "workspace": ""})
    # Two inspect_object calls (one per object)
    inspect_calls = [c for c in executor.run.call_args_list if c[0][0] == "inspect_object"]
    assert len(inspect_calls) == 2

    obj_names = [o.name for o in result.document.objects]
    assert obj_names == ["Box1", "Sketch1"]


def test_direct_inspection_distinguishes_shape_states() -> None:
    executor = Mock()
    executor.run.side_effect = [
        _make_list_result(
            [
                {"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 3000.0},
                {"name": "Sketch1", "type": "Sketcher::SketchObject"},
                {"name": "NullShape", "type": "Part::Feature"},
            ]
        ),
        # Box1: valid solid
        _make_inspect_result(
            {"name": "Box1", "type": "Part::Box", "label": "Box", "shape_type": "Solid", "volume": 3000.0, "shape_valid": True}
        ),
        # Sketch1: no shape attribute
        _make_inspect_result({"name": "Sketch1", "type": "Sketcher::SketchObject", "label": "Sketch"}),
        # NullShape: no shape info
        _make_inspect_result({"name": "NullShape", "type": "Part::Feature", "label": "Null"}),
    ]
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id="demo"))

    by_name = {o.name: o for o in result.document.objects}

    box = by_name["Box1"]
    assert box.has_shape is True
    assert box.shape_null is False
    assert box.shape is not None
    assert box.shape.shape_type == "Solid"
    assert box.shape.valid is True
    assert box.shape.volume == 3000.0
    assert box.label == "Box"

    sketch = by_name["Sketch1"]
    assert sketch.has_shape is False
    assert sketch.shape_null is True
    assert sketch.shape is None

    null_shape = by_name["NullShape"]
    assert null_shape.has_shape is False
    assert null_shape.shape_null is True
    assert null_shape.shape is None


def test_direct_inspection_all_valid() -> None:
    executor = Mock()
    executor.run.side_effect = [
        _make_list_result(
            [
                {"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 10.0},
                {"name": "Box2", "type": "Part::Box", "shape_type": "Solid", "volume": 20.0},
            ]
        ),
        _make_inspect_result({"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 10.0, "shape_valid": True}),
        _make_inspect_result({"name": "Box2", "type": "Part::Box", "shape_type": "Solid", "volume": 20.0, "shape_valid": True}),
    ]
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id="demo"))
    assert result.document.all_valid is True


def test_direct_inspection_all_valid_false_on_invalid_shape() -> None:
    executor = Mock()
    executor.run.side_effect = [
        _make_list_result(
            [
                {"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 10.0},
            ]
        ),
        _make_inspect_result({"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 10.0, "shape_valid": False}),
    ]
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id="demo"))
    assert result.document.all_valid is False


def test_direct_inspection_all_valid_none_on_empty_document() -> None:
    executor = Mock()
    executor.run.return_value = _make_list_result([])
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id="demo"))
    assert result.document.object_count == 0
    assert result.document.all_valid is None


def test_direct_inspection_validation_error() -> None:
    executor = Mock()
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id=".."))
    assert result.ok is False
    assert result.document_id == ".."
    assert result.document is None
    assert result.error is not None
    assert result.error.error_type == "ValidationError"
    executor.run.assert_not_called()


def test_direct_inspection_bridge_error() -> None:
    executor = Mock()
    executor.run.side_effect = FreeCadOperationError("document locked", error_type="LockError")
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id="demo"))
    assert result.ok is False
    assert result.document is None
    assert result.error is not None
    assert result.error.error_type == "LockError"
    assert "document locked" in result.error.message


def test_direct_inspection_status() -> None:
    executor = Mock()
    backend = DirectInspectionBackend(executor)
    status = backend.status()
    assert status.available is None
    assert "document_inspection" in status.capabilities
    assert "object_inspection" in status.capabilities
    assert "shape_inspection" in status.capabilities


def test_direct_inspection_passes_workspace() -> None:
    executor = Mock()
    executor.run.return_value = _make_list_result([])
    backend = DirectInspectionBackend(executor)
    backend.inspect(InspectionRequest(document_id="demo", workspace="/tmp/my-workspace"))
    executor.run.assert_called_once_with(
        "list_objects",
        {"document_id": "demo", "workspace": "/tmp/my-workspace"},
    )


def test_inspection_backend_is_a_protocol() -> None:
    # DirectInspectionBackend satisfies InspectionBackend
    executor = Mock()
    backend: InspectionBackend = DirectInspectionBackend(executor)
    assert backend.status().available is None


# ---------------------------------------------------------------------------
# Existing BuildRequest positional-arg callers remain unaffected
# ---------------------------------------------------------------------------


def test_build_request_positional_args_still_work() -> None:
    req = BuildRequest("create_box", {"length": 10.0}, 5.0)
    assert req.operation == "create_box"
    assert req.parameters == {"length": 10.0}
    assert req.timeout == 5.0
    assert req.document_id is None


def test_direct_backend_still_works_with_build_request() -> None:
    executor = Mock()
    executor.run.return_value = {"document_id": "demo"}
    backend = DirectBackend(executor)
    result = backend.execute(BuildRequest("list_objects", {"document_id": "demo"}, 3.5))
    assert result.ok is True
    assert result.data == {"document_id": "demo"}
