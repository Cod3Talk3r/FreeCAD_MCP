"""Regression test for the modeling tool parameter schemas.

Every modeling tool handler builds its bridge payload with
``"workspace": params.workspace or ""``.  If the matching ``*Params`` model does
not declare a ``workspace`` field, the handler raises
``AttributeError: '<Model>' object has no attribute 'workspace'`` before the
FreeCAD bridge is ever contacted, and the client only sees the opaque
``Error executing tool <name>``.

This test pins the contract: every parameter model used by a modeling tool must
expose an optional ``workspace`` that defaults to ``None``.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from freecad_mcp.tools import modeling

#: Every ``*Params`` model referenced by a modeling tool handler, together with
#: the minimum payload each one needs to validate.
MODELING_PARAMS_CASES: tuple[tuple[Any, dict[str, Any]], ...] = (
    (modeling.CreateDocumentParams, {"document_id": "doc"}),
    (modeling.CreateBoxParams, {"document_id": "doc"}),
    (modeling.CreateCylinderParams, {"document_id": "doc"}),
    (
        modeling.SetParameterParams,
        {"document_id": "doc", "object_name": "Box", "property_name": "Length", "value": 1.0},
    ),
    (modeling.RecomputeParams, {"document_id": "doc"}),
    (modeling.ListObjectsParams, {"document_id": "doc"}),
    (modeling.InspectObjectParams, {"document_id": "doc", "object_name": "Box"}),
    (modeling.ValidateDocParams, {"document_id": "doc"}),
    (modeling.SaveDocumentParams, {"document_id": "doc"}),
    (modeling.ExportStepParams, {"document_id": "doc"}),
    (
        modeling.BooleanCutParams,
        {"document_id": "doc", "base_object": "Base", "tool_object": "Tool"},
    ),
    (modeling.TankParams, {"document_id": "doc"}),
    (modeling.TubularCoilParams, {"document_id": "doc"}),
    (modeling.CreateSketchParams, {"document_id": "doc", "sketch_name": "Sketch"}),
    (
        modeling.SketchAddRectangleParams,
        {"document_id": "doc", "sketch_name": "Sketch", "width": 1.0, "height": 1.0},
    ),
    (
        modeling.SketchAddCircleParams,
        {"document_id": "doc", "sketch_name": "Sketch", "radius": 1.0},
    ),
    (
        modeling.SketchAddTriangleParams,
        {"document_id": "doc", "sketch_name": "Sketch", "x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 0.0, "x3": 0.0, "y3": 1.0},
    ),
    (
        modeling.SketchAddEllipseParams,
        {"document_id": "doc", "sketch_name": "Sketch", "center_x": 0.0, "center_y": 0.0, "major_radius": 1.0, "minor_radius": 1.0},
    ),
    (
        modeling.SketchAddRegularPolygonParams,
        {"document_id": "doc", "sketch_name": "Sketch", "radius": 1.0, "sides": 5},
    ),
    (
        modeling.SketchAddPolygonParams,
        {"document_id": "doc", "sketch_name": "Sketch", "vertices": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0}]},
    ),
    (
        modeling.InspectSketchParams,
        {"document_id": "doc", "sketch_name": "Sketch"},
    ),
    (
        modeling.SketchDeleteGeometryParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0},
    ),
    (
        modeling.SketchEditLineParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0},
    ),
    (
        modeling.SketchEditCircleParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0},
    ),
    (
        modeling.SketchEditEllipseParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0},
    ),
    (
        modeling.SketchEditRectangleParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0},
    ),
    (
        modeling.SketchEditTriangleParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0, "vertices": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0}]},
    ),
    (
        modeling.SketchEditRegularPolygonParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0, "radius": 1.0, "sides": 5},
    ),
    (
        modeling.SketchEditPolygonParams,
        {"document_id": "doc", "sketch_name": "Sketch", "geometry_index": 0, "vertices": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0}]},
    ),
    (
        modeling.SketchPadParams,
        {"document_id": "doc", "sketch_name": "Sketch"},
    ),
    (
        modeling.SketchPocketParams,
        {"document_id": "doc", "sketch_name": "Sketch"},
    ),
    (
        modeling.SketchRevolutionParams,
        {"document_id": "doc", "sketch_name": "Sketch"},
    ),
    (
        modeling.SketchGrooveParams,
        {"document_id": "doc", "sketch_name": "Sketch"},
    ),
    (
        modeling.PartDesignFilletParams,
        {"document_id": "doc", "object_name": "Box", "edge_indices": [1], "radius": 1.0},
    ),
    (
        modeling.PartDesignChamferParams,
        {"document_id": "doc", "object_name": "Box", "edge_indices": [1], "size": 1.0},
    ),
    (
        modeling.PartDesignLinearPatternParams,
        {"document_id": "doc", "object_name": "Box", "direction": "X_Axis", "length": 10.0, "occurrences": 3},
    ),
    (
        modeling.PartDesignPolarPatternParams,
        {"document_id": "doc", "object_name": "Box", "axis": "Z_Axis", "angle": 360.0, "occurrences": 4},
    ),
    (
        modeling.PartDesignMirroredParams,
        {"document_id": "doc", "object_name": "Box", "plane": "XY_Plane"},
    ),
    (
        modeling.PartDesignThicknessParams,
        {"document_id": "doc", "object_name": "Box", "thickness": 1.0},
    ),
    (
        modeling.BooleanFuseParams,
        {"document_id": "doc", "base_object": "Base", "tool_object": "Tool"},
    ),
    (
        modeling.BooleanCommonParams,
        {"document_id": "doc", "base_object": "Base", "tool_object": "Tool"},
    ),
)

_MODELING_PARAMS_MODELS = tuple(model for model, _ in MODELING_PARAMS_CASES)
_IDS = tuple(model.__name__ for model, _ in MODELING_PARAMS_CASES)


@pytest.mark.parametrize("params_model", _MODELING_PARAMS_MODELS, ids=_IDS)
def test_params_model_exposes_workspace_field(params_model: type) -> None:
    """A params model must declare ``workspace`` or the handler raises AttributeError."""
    assert "workspace" in params_model.model_fields, (
        f"{params_model.__name__} is missing the 'workspace' field; the matching "
        f"tool handler reads params.workspace and would raise AttributeError."
    )

    field = params_model.model_fields["workspace"]
    assert field.default is None
    assert field.annotation == Optional[str]


@pytest.mark.parametrize(
    ("params_model", "payload"),
    MODELING_PARAMS_CASES,
    ids=_IDS,
)
def test_params_model_workspace_defaults_to_none(params_model: type, payload: dict[str, Any]) -> None:
    """Omitting ``workspace`` must yield ``None`` so the bridge uses its default."""
    instance = params_model(**payload)
    assert instance.workspace is None
