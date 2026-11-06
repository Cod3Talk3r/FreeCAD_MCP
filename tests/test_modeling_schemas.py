"""Tests for M4 modeling tool schemas (TankParams, TubularCoilParams)."""

from __future__ import annotations

import pytest

from freecad_mcp.tools.modeling import (
    CreateSketchParams,
    SketchAddCircleParams,
    SketchAddEllipseParams,
    SketchAddPolygonParams,
    SketchAddRectangleParams,
    SketchAddRegularPolygonParams,
    SketchAddTriangleParams,
    TankParams,
    TubularCoilParams,
)


def test_tank_params_valid():
    """Valid TankParams should be accepted."""
    t = TankParams(document_id="mydoc", length=10.0, width=5.0, height=3.0)
    assert t.document_id == "mydoc"
    assert t.length == 10.0
    assert t.width == 5.0
    assert t.height == 3.0
    assert t.object_name == "Tank1"


def test_tank_params_defaults():
    """TankParams should use defaults when not specified."""
    t = TankParams(document_id="mydoc")
    assert t.length == 20.0
    assert t.width == 15.0
    assert t.height == 10.0
    assert t.object_name == "Tank1"


def test_tubular_coil_params_valid():
    """Valid TubularCoilParams should be accepted."""
    tc = TubularCoilParams(
        document_id="mydoc",
        coil_radius=15.0,
        tube_radius=3.0,
        num_turns=10,
        pitch=8.0,
    )
    assert tc.coil_radius == 15.0
    assert tc.tube_radius == 3.0
    assert tc.num_turns == 10
    assert tc.pitch == 8.0


def test_tubular_coil_params_defaults():
    """TubularCoilParams should use defaults when not specified."""
    tc = TubularCoilParams(document_id="mydoc")
    assert tc.coil_radius == 10.0
    assert tc.tube_radius == 2.0
    assert tc.num_turns == 5
    assert tc.pitch == 5.0


def test_tubular_coil_params_zero_coil_radius():
    """Zero coil_radius is accepted by schema (validated manually in handler)."""
    tc = TubularCoilParams(document_id="mydoc", coil_radius=0)
    assert tc.coil_radius == 0


def test_tubular_coil_params_negative_tube_radius():
    """Negative tube_radius is accepted by schema (validated manually in handler)."""
    tc = TubularCoilParams(document_id="mydoc", tube_radius=-5.0)
    assert tc.tube_radius == -5.0


def test_create_sketch_params_valid():
    """Valid CreateSketchParams should be accepted."""
    p = CreateSketchParams(document_id="mydoc", sketch_name="MySketch")
    assert p.document_id == "mydoc"
    assert p.sketch_name == "MySketch"


def test_create_sketch_params_defaults():
    """CreateSketchParams should default the sketch name."""
    p = CreateSketchParams(document_id="mydoc")
    assert p.sketch_name == "Sketch"


def test_sketch_add_rectangle_params_valid():
    """Valid SketchAddRectangleParams should be accepted."""
    p = SketchAddRectangleParams(
        document_id="mydoc", sketch_name="MySketch", x=1.0, y=2.0, width=10.0, height=5.0
    )
    assert p.document_id == "mydoc"
    assert p.sketch_name == "MySketch"
    assert p.x == 1.0
    assert p.y == 2.0
    assert p.width == 10.0
    assert p.height == 5.0


def test_sketch_add_rectangle_params_defaults():
    """SketchAddRectangleParams should default x and y to zero."""
    p = SketchAddRectangleParams(document_id="mydoc", sketch_name="MySketch", width=10.0, height=5.0)
    assert p.x == 0.0
    assert p.y == 0.0


def test_sketch_add_circle_params_valid():
    """Valid SketchAddCircleParams should be accepted."""
    p = SketchAddCircleParams(document_id="mydoc", sketch_name="MySketch", center_x=1.0, center_y=2.0, radius=3.0)
    assert p.document_id == "mydoc"
    assert p.sketch_name == "MySketch"
    assert p.center_x == 1.0
    assert p.center_y == 2.0
    assert p.radius == 3.0


def test_sketch_add_circle_params_defaults():
    """SketchAddCircleParams should default center to zero."""
    p = SketchAddCircleParams(document_id="mydoc", sketch_name="MySketch", radius=3.0)
    assert p.center_x == 0.0
    assert p.center_y == 0.0


def test_sketch_params_accept_zero_and_negative_at_schema_level():
    """Invalid dimensions are accepted by the schema and rejected by the handler."""
    p = SketchAddRectangleParams(document_id="mydoc", sketch_name="S", width=0, height=-5)
    assert p.width == 0
    assert p.height == -5
    c = SketchAddCircleParams(document_id="mydoc", sketch_name="S", radius=0)
    assert c.radius == 0


def test_sketch_add_triangle_params_valid():
    """Valid SketchAddTriangleParams should be accepted."""
    p = SketchAddTriangleParams(
        document_id="mydoc", sketch_name="S", x1=0.0, y1=0.0, x2=1.0, y2=0.0, x3=0.0, y3=1.0
    )
    assert p.document_id == "mydoc"
    assert p.sketch_name == "S"
    assert p.x1 == 0.0
    assert p.y1 == 0.0
    assert p.x2 == 1.0
    assert p.y2 == 0.0
    assert p.x3 == 0.0
    assert p.y3 == 1.0


def test_sketch_add_triangle_params_defaults():
    """SketchAddTriangleParams defaults form a non-degenerate triangle."""
    p = SketchAddTriangleParams(document_id="mydoc", sketch_name="S")
    assert p.x1 == 0.0
    assert p.y1 == 0.0
    assert p.x2 == 1.0
    assert p.y2 == 0.0
    assert p.x3 == 0.0
    assert p.y3 == 1.0


def test_sketch_add_ellipse_params_valid():
    """Valid SketchAddEllipseParams should be accepted."""
    p = SketchAddEllipseParams(
        document_id="mydoc", sketch_name="S", center_x=1.0, center_y=2.0, major_radius=5.0, minor_radius=3.0
    )
    assert p.document_id == "mydoc"
    assert p.sketch_name == "S"
    assert p.center_x == 1.0
    assert p.center_y == 2.0
    assert p.major_radius == 5.0
    assert p.minor_radius == 3.0


def test_sketch_add_ellipse_params_defaults():
    """SketchAddEllipseParams defaults center to zero."""
    p = SketchAddEllipseParams(document_id="mydoc", sketch_name="S", major_radius=5.0, minor_radius=3.0)
    assert p.center_x == 0.0
    assert p.center_y == 0.0


def test_sketch_add_ellipse_accepts_zero_and_negative_at_schema_level():
    """Invalid radii are accepted by the schema and rejected by the handler."""
    e = SketchAddEllipseParams(document_id="mydoc", sketch_name="S", major_radius=0, minor_radius=-2)
    assert e.major_radius == 0
    assert e.minor_radius == -2


def test_sketch_add_regular_polygon_params_valid():
    """Valid SketchAddRegularPolygonParams should be accepted."""
    p = SketchAddRegularPolygonParams(
        document_id="mydoc", sketch_name="S", center_x=1.0, center_y=2.0, radius=5.0, sides=6, rotation_deg=30.0
    )
    assert p.document_id == "mydoc"
    assert p.sketch_name == "S"
    assert p.center_x == 1.0
    assert p.center_y == 2.0
    assert p.radius == 5.0
    assert p.sides == 6
    assert p.rotation_deg == 30.0


def test_sketch_add_regular_polygon_params_defaults():
    """SketchAddRegularPolygonParams defaults center and rotation to zero."""
    p = SketchAddRegularPolygonParams(document_id="mydoc", sketch_name="S", radius=5.0, sides=6)
    assert p.center_x == 0.0
    assert p.center_y == 0.0
    assert p.rotation_deg == 0.0


def test_sketch_add_regular_polygon_accepts_invalid_at_schema_level():
    """Invalid sides/radius accepted by schema, rejected by handler."""
    p = SketchAddRegularPolygonParams(document_id="mydoc", sketch_name="S", radius=0, sides=2)
    assert p.radius == 0
    assert p.sides == 2


def test_sketch_add_polygon_params_valid():
    """Valid SketchAddPolygonParams should be accepted."""
    p = SketchAddPolygonParams(
        document_id="mydoc",
        sketch_name="S",
        vertices=[{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0}],
    )
    assert p.document_id == "mydoc"
    assert p.sketch_name == "S"
    assert p.vertices == [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0}]