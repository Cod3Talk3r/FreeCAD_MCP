"""MCP modeling tools for freecad-mpy.

Typed, validated modeling operations built on top of the
:class:`~freecad_mcp.freecad_bridge.FreeCadBridge`. Each tool validates
its inputs before delegating to the bridge, and returns structured
:class:`~pydantic.BaseModel` results.

All operations are explicit and bounded; no arbitrary Python code is
executed inside FreeCAD from the parent process.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping
from typing import Any, Optional

from pydantic import BaseModel, Field

from freecad_mcp.app.contracts import BuildRequest
from freecad_mcp.app.direct import DirectBackend, validate_direct_request
from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, FreeCadOperationError
from freecad_mcp.freecad_bridge.workspace import (
    Workspace,
    validate_document_id,
)


# ---------------------------------------------------------------------------
# Whitelisted property names per object type (used for manual validation)
# ---------------------------------------------------------------------------

_BOX_PROPS = {"Length", "Width", "Height"}
_CYLINDER_PROPS = {"Radius", "Height"}

# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------


class ObjectInfo(BaseModel):
    """Information about a single document object."""

    name: str = Field(description="Object name inside FreeCAD.")
    type: str = Field(description="FreeCAD TypeId, e.g. 'Part::Box'.")
    shape_type: Optional[str] = Field(
        default=None, description="Shape.ShapeType if available (e.g. 'Solid')."
    )
    volume: Optional[float] = Field(
        default=None, description="Shape.Volume if available."
    )
    length: Optional[float] = Field(default=None, description="Length in mm.")
    width: Optional[float] = Field(default=None, description="Width in mm.")
    height: Optional[float] = Field(default=None, description="Height in mm.")
    radius: Optional[float] = Field(
        default=None, description="Radius in mm (cylinders)."
    )


class DocumentList(BaseModel):
    """Result of listing objects in a document."""

    document_id: str = Field(description="Validated document identifier.")
    objects: list[ObjectInfo] = Field(description="List of objects with basic info.")
    all_valid: Optional[bool] = Field(
        default=None, description="True if validate_document reports all OK."
    )


class ValidationResult(BaseModel):
    """Result of validate_document."""

    document_id: str = Field(description="Validated document identifier.")
    all_valid: bool = Field(description="True if every object passes geometry checks.")
    objects: list[dict[str, Any]] = Field(
        description="Per-object validity results."
    )


class StepExportResult(BaseModel):
    """Result of export_step."""

    document_id: str = Field(description="Validated document identifier.")
    step_path: str = Field(description="Absolute path to the exported STEP file.")
    exported_count: int = Field(description="Number of objects exported.")
    success: bool = Field(description="Whether the export completed without error.")


class BooleanCutResult(BaseModel):
    """Result of boolean_cut."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the new Boolean-cut result object.")
    base_object: str = Field(description="Name of the object the cut was applied to.")
    tool_object: str = Field(description="Name of the object subtracted from the base.")
    type: str = Field(description="FreeCAD TypeId of the result object.")
    shape_type: str = Field(description="Shape.ShapeType of the resulting shape.")
    volume: float = Field(description="Volume of the resulting shape in mm^3.")
    length: Optional[float] = Field(default=None, description="Bounding-box length in mm.")
    width: Optional[float] = Field(default=None, description="Bounding-box width in mm.")
    height: Optional[float] = Field(default=None, description="Bounding-box height in mm.")


class BooleanFuseResult(BaseModel):
    """Result of boolean_fuse."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the new Boolean-fuse result object.")
    base_object: str = Field(description="Name of the first object fused.")
    tool_object: str = Field(description="Name of the second object fused into the base.")
    type: str = Field(description="FreeCAD TypeId of the result object.")
    shape_type: str = Field(description="Shape.ShapeType of the resulting shape.")
    volume: float = Field(description="Volume of the resulting shape in mm^3.")
    length: Optional[float] = Field(default=None, description="Bounding-box length in mm.")
    width: Optional[float] = Field(default=None, description="Bounding-box width in mm.")
    height: Optional[float] = Field(default=None, description="Bounding-box height in mm.")


class BooleanCommonResult(BaseModel):
    """Result of boolean_common."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the new Boolean-common result object.")
    base_object: str = Field(description="Name of the first object intersected.")
    tool_object: str = Field(description="Name of the second object intersected with the base.")
    type: str = Field(description="FreeCAD TypeId of the result object.")
    shape_type: str = Field(description="Shape.ShapeType of the resulting shape.")
    volume: float = Field(description="Volume of the resulting shape in mm^3.")
    length: Optional[float] = Field(default=None, description="Bounding-box length in mm.")
    width: Optional[float] = Field(default=None, description="Bounding-box width in mm.")
    height: Optional[float] = Field(default=None, description="Bounding-box height in mm.")


class CreateSketchResult(BaseModel):
    """Result of create_sketch."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the new Sketch object.")
    type: str = Field(description="FreeCAD TypeId, 'Sketcher::SketchObject'.")
    plane: str = Field(description="Initial sketch plane, e.g. 'XY'.")
    edges: int = Field(description="Number of Sketcher geometries in the sketch (0 on creation).")


class SketchAddRectangleResult(BaseModel):
    """Result of sketch_add_rectangle."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the rectangle was added to.")
    x: float = Field(description="X coordinate of the lower-left corner in millimetres.")
    y: float = Field(description="Y coordinate of the lower-left corner in millimetres.")
    width: float = Field(description="Rectangle width in millimetres.")
    height: float = Field(description="Rectangle height in millimetres.")
    edges_added: int = Field(description="Number of line segments added (4).")
    total_edges: Optional[int] = Field(
        default=None, description="Total Sketcher geometries in the sketch after adding the rectangle."
    )


class SketchAddCircleResult(BaseModel):
    """Result of sketch_add_circle."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the circle was added to.")
    center_x: float = Field(description="X coordinate of the circle centre in millimetres.")
    center_y: float = Field(description="Y coordinate of the circle centre in millimetres.")
    radius: float = Field(description="Circle radius in millimetres.")
    edges_added: int = Field(description="Number of geometries added (1).")
    total_edges: Optional[int] = Field(
        default=None, description="Total Sketcher geometries in the sketch after adding the circle."
    )


class SketchAddTriangleResult(BaseModel):
    """Result of sketch_add_triangle."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the triangle was added to.")
    x1: float = Field(description="X coordinate of the first vertex in millimetres.")
    y1: float = Field(description="Y coordinate of the first vertex in millimetres.")
    x2: float = Field(description="X coordinate of the second vertex in millimetres.")
    y2: float = Field(description="Y coordinate of the second vertex in millimetres.")
    x3: float = Field(description="X coordinate of the third vertex in millimetres.")
    y3: float = Field(description="Y coordinate of the third vertex in millimetres.")
    edges_added: int = Field(description="Number of line segments added (3).")
    total_edges: Optional[int] = Field(
        default=None, description="Total Sketcher geometries in the sketch after adding the triangle."
    )


class SketchAddEllipseResult(BaseModel):
    """Result of sketch_add_ellipse."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the ellipse was added to.")
    center_x: float = Field(description="X coordinate of the ellipse centre in millimetres.")
    center_y: float = Field(description="Y coordinate of the ellipse centre in millimetres.")
    major_radius: float = Field(description="Major radius of the ellipse in millimetres.")
    minor_radius: float = Field(description="Minor radius of the ellipse in millimetres.")
    edges_added: int = Field(description="Number of geometries added (1).")
    total_edges: Optional[int] = Field(
        default=None, description="Total Sketcher geometries in the sketch after adding the ellipse."
    )


class SketchAddRegularPolygonResult(BaseModel):
    """Result of sketch_add_regular_polygon."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the polygon was added to.")
    center_x: float = Field(description="X coordinate of the polygon centre in millimetres.")
    center_y: float = Field(description="Y coordinate of the polygon centre in millimetres.")
    radius: float = Field(description="Circumradius of the regular polygon in millimetres.")
    sides: int = Field(description="Number of sides of the regular polygon.")
    rotation_deg: float = Field(description="Rotation of the first vertex counter-clockwise from +X, in degrees.")
    edges_added: int = Field(description="Number of line segments added (equal to sides).")
    total_edges: Optional[int] = Field(
        default=None, description="Total Sketcher geometries in the sketch after adding the polygon."
    )


class SketchAddPolygonResult(BaseModel):
    """Result of sketch_add_polygon."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the polygon was added to.")
    edges_added: int = Field(description="Number of line segments added (equal to vertex count).")
    total_edges: Optional[int] = Field(
        default=None, description="Total Sketcher geometries in the sketch after adding the polygon."
    )


class InspectSketchResult(BaseModel):
    """Result of inspect_sketch."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the inspected sketch.")
    type: str = Field(description="FreeCAD TypeId, e.g. 'Sketcher::SketchObject'.")
    geometry_count: int = Field(description="Number of geometry objects in the sketch.")
    constraint_count: Optional[int] = Field(description="Number of constraints in the sketch.")
    geometries: list[dict[str, Any]] = Field(description="Per-geometry inspection details.")
    placement: dict[str, Any] = Field(
        default={}, description="Sketch placement (X/Y/Z) if available, otherwise empty."
    )


class SketchDeleteGeometryResult(BaseModel):
    """Result of sketch_delete_geometry."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the geometry was deleted from.")
    deleted_index: int = Field(description="Zero-based index of the deleted geometry.")
    geometry_count: int = Field(description="Number of geometries remaining in the sketch after deletion.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before deletion."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after deletion."
    )


class SketchEditLineResult(BaseModel):
    """Result of sketch_edit_line."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the line was edited in.")
    geometry_index: int = Field(description="Zero-based index of the edited line geometry.")
    start_x: float = Field(description="New start X coordinate in millimetres.")
    start_y: float = Field(description="New start Y coordinate in millimetres.")
    end_x: float = Field(description="New end X coordinate in millimetres.")
    end_y: float = Field(description="New end Y coordinate in millimetres.")
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchEditCircleResult(BaseModel):
    """Result of sketch_edit_circle."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the circle was edited in.")
    geometry_index: int = Field(description="Zero-based index of the edited circle geometry.")
    center_x: float = Field(description="New center X coordinate in millimetres.")
    center_y: float = Field(description="New center Y coordinate in millimetres.")
    radius: float = Field(description="New circle radius in millimetres.")
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchEditEllipseResult(BaseModel):
    """Result of sketch_edit_ellipse."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the ellipse was edited in.")
    geometry_index: int = Field(description="Zero-based index of the edited ellipse geometry.")
    center_x: float = Field(description="New center X coordinate in millimetres.")
    center_y: float = Field(description="New center Y coordinate in millimetres.")
    major_radius: float = Field(description="New major (semi-axis) radius in millimetres.")
    minor_radius: float = Field(description="New minor (semi-axis) radius in millimetres.")
    rotation_deg: float = Field(description="Major-axis rotation counter-clockwise from +X, in degrees.")
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchEditRectangleResult(BaseModel):
    """Result of sketch_edit_rectangle."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the rectangle was edited in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the edited rectangle."
    )
    x: float = Field(description="New lower-left X coordinate in millimetres (before rotation).")
    y: float = Field(description="New lower-left Y coordinate in millimetres (before rotation).")
    width: float = Field(description="New rectangle width in millimetres.")
    height: float = Field(description="New rectangle height in millimetres.")
    rotation_deg: float = Field(description="Rotation around the rectangle centre, in degrees.")
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchEditTriangleResult(BaseModel):
    """Result of sketch_edit_triangle."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the triangle was edited in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the edited triangle."
    )
    vertices: list[dict[str, float]] = Field(
        description="Three ordered vertices as [{x, y}, {x, y}, {x, y}] in millimetres."
    )
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchEditRegularPolygonResult(BaseModel):
    """Result of sketch_edit_regular_polygon."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the regular polygon was edited in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the edited regular polygon."
    )
    center_x: float = Field(description="New centre X coordinate in millimetres.")
    center_y: float = Field(description="New centre Y coordinate in millimetres.")
    radius: float = Field(description="New circumradius in millimetres (must be positive).")
    sides: int = Field(description="Number of sides of the regular polygon.")
    rotation_deg: float = Field(description="Rotation of the first vertex counter-clockwise from +X, in degrees.")
    vertices: list[dict[str, float]] = Field(
        description="Ordered vertices as [{x, y}, ...] in millimetres, on the circumcircle."
    )
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchEditPolygonResult(BaseModel):
    """Result of sketch_edit_polygon."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch the polygon was edited in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the edited polygon."
    )
    vertices: list[dict[str, float]] = Field(
        description="Ordered vertices as [{x, y}, ...] in millimetres."
    )
    geometry_count: int = Field(description="Number of geometries in the sketch after editing.")
    constraint_count_before: Optional[int] = Field(
        default=None, description="Number of constraints before editing."
    )
    constraint_count_after: Optional[int] = Field(
        default=None, description="Number of constraints after editing."
    )


class SketchPadResult(BaseModel):
    """Result of sketch_pad."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch used as the Pad profile.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Pad.")
    pad_name: str = Field(description="Name of the created PartDesign::Pad.")
    profile_name: str = Field(description="Name of the sketch used as the profile.")
    length: float = Field(description="Extrusion length in millimetres.")
    reversed: bool = Field(description="Whether the extrusion direction is reversed.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class SketchPocketResult(BaseModel):
    """Result of sketch_pocket."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch used as the Pocket profile.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Pocket.")
    pocket_name: str = Field(description="Name of the created PartDesign::Pocket.")
    profile_name: str = Field(description="Name of the sketch used as the profile.")
    length: float = Field(description="Cut depth in millimetres.")
    reversed: bool = Field(description="Whether the cut direction is reversed.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class SketchRevolutionResult(BaseModel):
    """Result of sketch_revolution."""

    document_id: str = Field(description="Validated document identifier.")
    sketch_name: str = Field(description="Name of the sketch used as the Revolution profile.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Revolution.")
    revolution_name: str = Field(description="Name of the created PartDesign::Revolution.")
    profile_name: str = Field(description="Name of the sketch used as the profile.")
    axis: str = Field(description="Revolution axis ('X_Axis', 'Y_Axis', or 'Z_Axis').")
    angle: float = Field(description="Revolution angle in degrees.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class PartDesignFilletResult(BaseModel):
    """Result of partdesign_fillet."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the solid feature that was filleted.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Fillet.")
    fillet_name: str = Field(description="Name of the created PartDesign::Fillet.")
    base_feature: str = Field(description="Name of the feature used as the Fillet base.")
    edge_indices: list[int] = Field(description="1-based edge indices that were filleted.")
    radius: float = Field(description="Fillet radius in millimetres.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class PartDesignChamferResult(BaseModel):
    """Result of partdesign_chamfer."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the solid feature that was chamfered.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Chamfer.")
    chamfer_name: str = Field(description="Name of the created PartDesign::Chamfer.")
    base_feature: str = Field(description="Name of the feature used as the Chamfer base.")
    edge_indices: list[int] = Field(description="1-based edge indices that were chamfered.")
    size: float = Field(description="Chamfer size in millimetres.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class PartDesignLinearPatternResult(BaseModel):
    """Result of partdesign_linear_pattern."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the solid feature that was patterned.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the LinearPattern.")
    linear_pattern_name: str = Field(description="Name of the created PartDesign::LinearPattern.")
    base_feature: str = Field(description="Name of the feature used as the LinearPattern base.")
    direction: str = Field(description="Principal axis direction: 'X_Axis', 'Y_Axis', or 'Z_Axis'.")
    direction_edge_index: int = Field(description="1-based edge index used for the pattern direction.")
    length: float = Field(description="Pattern length in millimetres.")
    occurrences: int = Field(description="Number of pattern occurrences.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class PartDesignPolarPatternResult(BaseModel):
    """Result of partdesign_polar_pattern."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the solid feature that was patterned.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the PolarPattern.")
    polar_pattern_name: str = Field(description="Name of the created PartDesign::PolarPattern.")
    base_feature: str = Field(description="Name of the feature used as the PolarPattern base.")
    axis: str = Field(description="Principal axis: 'X_Axis', 'Y_Axis', or 'Z_Axis'.")
    axis_edge_index: int = Field(description="1-based edge index used for the pattern axis.")
    angle: float = Field(description="Pattern angle in degrees (0 < angle <= 360).")
    occurrences: int = Field(description="Number of pattern occurrences.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class PartDesignMirroredResult(BaseModel):
    """Result of partdesign_mirrored."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the solid feature that was mirrored.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Mirrored.")
    mirrored_name: str = Field(description="Name of the created PartDesign::Mirrored.")
    base_feature: str = Field(description="Name of the feature used as the Mirrored base.")
    plane: str = Field(description="Mirror plane: 'XY_Plane', 'XZ_Plane', or 'YZ_Plane'.")
    mirror_face_index: int = Field(description="1-based face index used for the mirror plane.")
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


class PartDesignThicknessResult(BaseModel):
    """Result of partdesign_thickness."""

    document_id: str = Field(description="Validated document identifier.")
    object_name: str = Field(description="Name of the solid feature that was thickened.")
    body_name: str = Field(description="Name of the PartDesign::Body containing the Thickness.")
    thickness_name: str = Field(description="Name of the created PartDesign::Thickness.")
    base_feature: str = Field(description="Name of the feature used as the Thickness base.")
    thickness_value: float = Field(description="Wall thickness in millimetres.")
    direction: str = Field(description="Offset direction: 'inward' or 'outward'.")
    mode: str = Field(description="Thickness mode: 'Skin' (only mode supported by FreeCAD 1.1.4).")
    base: Optional[str] = Field(
        default=None,
        description="Native Base LinkSub reference, e.g. '(Pad, [Face6])', when faces are removed."
    )
    removed_faces: list[int] = Field(
        default=[],
        description="1-based face indices removed/opened by the Thickness feature."
    )
    base_volume: Optional[float] = Field(
        default=None, description="Volume of the base feature before thickness, in mm^3."
    )
    volume_reduction: Optional[float] = Field(
        default=None, description="Volume removed by the Thickness operation, in mm^3."
    )
    volume: Optional[float] = Field(
        default=None, description="Volume of the resulting solid in cubic millimetres, if available."
    )
    bounding_box: Optional[dict[str, float]] = Field(
        default=None, description="Bounding box of the resulting solid, if available."
    )
    solids_count: int = Field(description="Number of solids in the resulting shape.")
    meta_before: dict[str, Any] = Field(
        description="Document metadata snapshot before the operation."
    )


# ---------------------------------------------------------------------------
# Input schemas (per-operation) — minimal fields; validation is done manually
# in the tool handlers because Pydantic V2-style @validator is not guaranteed.
# ---------------------------------------------------------------------------


class CreateDocumentParams(BaseModel):
    """Parameters for creating a new document."""

    document_id: str = Field(
        description="User-chosen identifier; validated/sanitized to a safe filename."
    )
    workspace: Optional[str] = Field(
        default=None,
        description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace.",
    )


class CreateBoxParams(BaseModel):
    """Parameters for creating a Part::Box."""

    document_id: str = Field(description="Existing document to add the box to.")
    object_name: str = Field(default="Box1", description="Name for the new Box object.")
    length: float = Field(default=10.0, description="Length in millimetres.")
    width: float = Field(default=5.0, description="Width in millimetres.")
    height: float = Field(default=3.0, description="Height in millimetres.")
    x: float = Field(default=0.0, description="X position in millimetres.")
    y: float = Field(default=0.0, description="Y position in millimetres.")
    z: float = Field(default=0.0, description="Z position in millimetres.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class CreateCylinderParams(BaseModel):
    """Parameters for creating a Part::Cylinder."""

    document_id: str = Field(description="Existing document to add the cylinder to.")
    object_name: str = Field(default="Cyl1", description="Name for the new Cylinder object.")
    radius: float = Field(default=2.0, description="Radius in millimetres.")
    height: float = Field(default=7.0, description="Height in millimetres.")
    x: float = Field(default=0.0, description="X position in millimetres.")
    y: float = Field(default=0.0, description="Y position in millimetres.")
    z: float = Field(default=0.0, description="Z position in millimetres.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SetParameterParams(BaseModel):
    """Parameters for setting a whitelisted property on an object."""

    document_id: str = Field(description="Existing document containing the object.")
    object_name: str = Field(description="Name of the object to modify.")
    property_name: str = Field(description="Property to change (whitelisted).")
    value: float = Field(description="New value (positive number in mm).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SetPlacementParams(BaseModel):
    """Parameters for changing the position of an existing Box or Cylinder."""

    document_id: str = Field(description="Existing document containing the object.")
    object_name: str = Field(description="Name of the Box or Cylinder to move.")
    x: float = Field(description="New X position in millimetres.")
    y: float = Field(description="New Y position in millimetres.")
    z: float = Field(description="New Z position in millimetres.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class CreateSketchParams(BaseModel):
    """Parameters for creating a Sketcher::SketchObject."""

    document_id: str = Field(description="Existing document to add the sketch to.")
    sketch_name: str = Field(default="Sketch", description="Name for the new Sketch object.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchAddRectangleParams(BaseModel):
    """Parameters for adding a rectangle to an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to add the rectangle to.")
    x: float = Field(default=0.0, description="X coordinate of the lower-left corner in millimetres.")
    y: float = Field(default=0.0, description="Y coordinate of the lower-left corner in millimetres.")
    width: float = Field(description="Rectangle width in millimetres (must be positive).")
    height: float = Field(description="Rectangle height in millimetres (must be positive).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchAddCircleParams(BaseModel):
    """Parameters for adding a circle to an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to add the circle to.")
    center_x: float = Field(default=0.0, description="X coordinate of the circle centre in millimetres.")
    center_y: float = Field(default=0.0, description="Y coordinate of the circle centre in millimetres.")
    radius: float = Field(description="Circle radius in millimetres (must be positive).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchAddTriangleParams(BaseModel):
    """Parameters for adding a triangle to an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to add the triangle to.")
    x1: float = Field(default=0.0, description="X coordinate of the first vertex in millimetres.")
    y1: float = Field(default=0.0, description="Y coordinate of the first vertex in millimetres.")
    x2: float = Field(default=1.0, description="X coordinate of the second vertex in millimetres.")
    y2: float = Field(default=0.0, description="Y coordinate of the second vertex in millimetres.")
    x3: float = Field(default=0.0, description="X coordinate of the third vertex in millimetres.")
    y3: float = Field(default=1.0, description="Y coordinate of the third vertex in millimetres.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchAddEllipseParams(BaseModel):
    """Parameters for adding an ellipse to an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to add the ellipse to.")
    center_x: float = Field(default=0.0, description="X coordinate of the ellipse centre in millimetres.")
    center_y: float = Field(default=0.0, description="Y coordinate of the ellipse centre in millimetres.")
    major_radius: float = Field(description="Major radius of the ellipse in millimetres (must be positive).")
    minor_radius: float = Field(description="Minor radius of the ellipse in millimetres (must be positive).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchAddRegularPolygonParams(BaseModel):
    """Parameters for adding a regular polygon to an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to add the polygon to.")
    center_x: float = Field(default=0.0, description="X coordinate of the polygon centre in millimetres.")
    center_y: float = Field(default=0.0, description="Y coordinate of the polygon centre in millimetres.")
    radius: float = Field(description="Circumradius of the regular polygon in millimetres (must be positive).")
    sides: int = Field(description="Number of sides (must be between 3 and MAX_POLYGON_SIDES).")
    rotation_deg: float = Field(
        default=0.0, description="Rotation of the first vertex counter-clockwise from +X, in degrees."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchAddPolygonParams(BaseModel):
    """Parameters for adding a custom polygon to an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to add the polygon to.")
    vertices: list[dict[str, float]] = Field(
        description="Ordered list of {x, y} vertex coordinates in millimetres (at least 3)."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class InspectSketchParams(BaseModel):
    """Parameters for inspecting an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to inspect.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchDeleteGeometryParams(BaseModel):
    """Parameters for deleting geometry from an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to delete geometry from.")
    geometry_index: int = Field(description="Zero-based index of the geometry to delete.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditLineParams(BaseModel):
    """Parameters for editing line geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the line in.")
    geometry_index: int = Field(description="Zero-based index of the line geometry to edit.")
    start_x: float = Field(default=0.0, description="New start X coordinate in millimetres.")
    start_y: float = Field(default=0.0, description="New start Y coordinate in millimetres.")
    end_x: float = Field(default=1.0, description="New end X coordinate in millimetres.")
    end_y: float = Field(default=0.0, description="New end Y coordinate in millimetres.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditCircleParams(BaseModel):
    """Parameters for editing circle geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the circle in.")
    geometry_index: int = Field(description="Zero-based index of the circle geometry to edit.")
    center_x: float = Field(default=0.0, description="New center X coordinate in millimetres.")
    center_y: float = Field(default=0.0, description="New center Y coordinate in millimetres.")
    radius: float = Field(default=5.0, description="New circle radius in millimetres (must be positive).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditEllipseParams(BaseModel):
    """Parameters for editing ellipse geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the ellipse in.")
    geometry_index: int = Field(description="Zero-based index of the ellipse geometry to edit.")
    center_x: float = Field(default=0.0, description="New center X coordinate in millimetres.")
    center_y: float = Field(default=0.0, description="New center Y coordinate in millimetres.")
    major_radius: float = Field(
        default=5.0, description="New major (semi-axis) radius in millimetres (must be positive)."
    )
    minor_radius: float = Field(
        default=3.0, description="New minor (semi-axis) radius in millimetres (must be positive)."
    )
    rotation_deg: float = Field(
        default=0.0, description="Major-axis rotation counter-clockwise from +X, in degrees."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditRectangleParams(BaseModel):
    """Parameters for editing rectangle geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the rectangle in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the rectangle to edit."
    )
    x: float = Field(default=0.0, description="New lower-left X coordinate in millimetres (before rotation).")
    y: float = Field(default=0.0, description="New lower-left Y coordinate in millimetres (before rotation).")
    width: float = Field(default=5.0, description="New rectangle width in millimetres (must be positive).")
    height: float = Field(default=3.0, description="New rectangle height in millimetres (must be positive).")
    rotation_deg: float = Field(
        default=0.0, description="Rotation around the rectangle centre, in degrees."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditTriangleParams(BaseModel):
    """Parameters for editing triangle geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the triangle in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the triangle to edit."
    )
    vertices: list[dict[str, float]] = Field(
        description="Three ordered vertices as [{x, y}, {x, y}, {x, y}] in millimetres."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditRegularPolygonParams(BaseModel):
    """Parameters for editing regular polygon geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the regular polygon in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the regular polygon to edit."
    )
    center_x: float = Field(default=0.0, description="New centre X coordinate in millimetres.")
    center_y: float = Field(default=0.0, description="New centre Y coordinate in millimetres.")
    radius: float = Field(default=5.0, description="New circumradius in millimetres (must be positive).")
    sides: int = Field(default=5, description="Number of sides of the regular polygon.")
    rotation_deg: float = Field(
        default=0.0, description="Rotation of the first vertex counter-clockwise from +X, in degrees."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchEditPolygonParams(BaseModel):
    """Parameters for editing custom polygon geometry in an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the sketch to edit the polygon in.")
    geometry_index: int = Field(
        description="Zero-based index of the first line of the polygon to edit."
    )
    vertices: list[dict[str, float]] = Field(
        description="Ordered vertices as [{x, y}, ...] in millimetres."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchPadParams(BaseModel):
    """Parameters for creating a PartDesign::Pad from an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the Sketcher::SketchObject to use as the Pad profile.")
    length: float = Field(default=10.0, description="Extrusion length in millimetres (must be positive).")
    reversed: bool = Field(
        default=False, description="Whether to extrude in the reversed direction."
    )
    pad_name: str = Field(default="Pad", description="Name for the new PartDesign::Pad object.")
    body_name: str = Field(default="Body", description="Name for the PartDesign::Body (created if needed).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


# ---------------------------------------------------------------------------
# Input schemas (per-operation) — minimal fields; validation is done manually
# in the tool handlers because Pydantic V2-style @validator is not guaranteed.
# ---------------------------------------------------------------------------


class SketchPocketParams(BaseModel):
    """Parameters for creating a PartDesign::Pocket from an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the Sketcher::SketchObject to use as the Pocket profile.")
    length: float = Field(default=10.0, description="Cut depth in millimetres (must be positive).")
    reversed: bool = Field(
        default=False, description="Whether to cut in the reversed direction."
    )
    pocket_name: str = Field(default="Pocket", description="Name for the new PartDesign::Pocket object.")
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the sketch.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SketchRevolutionParams(BaseModel):
    """Parameters for creating a PartDesign::Revolution from an existing sketch."""

    document_id: str = Field(description="Existing document containing the sketch.")
    sketch_name: str = Field(description="Name of the Sketcher::SketchObject to use as the Revolution profile.")
    axis: str = Field(
        default="Z_Axis", description="Revolution axis: one of 'X_Axis', 'Y_Axis', 'Z_Axis' (principal origin axes)."
    )
    angle: float = Field(
        default=360.0, description="Revolution angle in degrees (must be positive, maximum 360)."
    )
    revolution_name: str = Field(default="Revolution", description="Name for the new PartDesign::Revolution object.")
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the sketch.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class PartDesignFilletParams(BaseModel):
    """Parameters for creating a PartDesign::Fillet on an existing solid feature."""

    document_id: str = Field(description="Existing document containing the target feature.")
    object_name: str = Field(description="Name of the solid feature to fillet.")
    edge_indices: list[int] = Field(
        description="1-based edge indices of the target feature to fillet (non-empty, unique)."
    )
    radius: float = Field(description="Fillet radius in millimetres (must be positive).")
    fillet_name: str = Field(default="Fillet", description="Name for the new PartDesign::Fillet object.")
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the target.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class PartDesignChamferParams(BaseModel):
    """Parameters for creating a PartDesign::Chamfer on an existing solid feature."""

    document_id: str = Field(description="Existing document containing the target feature.")
    object_name: str = Field(description="Name of the solid feature to chamfer.")
    edge_indices: list[int] = Field(
        description="1-based edge indices of the target feature to chamfer (non-empty, unique)."
    )
    size: float = Field(description="Chamfer size in millimetres (must be positive).")
    chamfer_name: str = Field(default="Chamfer", description="Name for the new PartDesign::Chamfer object.")
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the target.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class PartDesignLinearPatternParams(BaseModel):
    """Parameters for creating a PartDesign::LinearPattern on an existing solid feature."""

    document_id: str = Field(description="Existing document containing the target feature.")
    object_name: str = Field(description="Name of the solid feature to pattern.")
    direction: str = Field(
        description="Principal axis direction: 'X_Axis', 'Y_Axis', or 'Z_Axis'."
    )
    length: float = Field(description="Pattern length in millimetres (must be positive).")
    occurrences: int = Field(
        description="Number of pattern occurrences (must be >= 2)."
    )
    linear_pattern_name: str = Field(
        default="LinearPattern", description="Name for the new PartDesign::LinearPattern object."
    )
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the target.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class PartDesignPolarPatternParams(BaseModel):
    """Parameters for creating a PartDesign::PolarPattern on an existing solid feature."""

    document_id: str = Field(description="Existing document containing the target feature.")
    object_name: str = Field(description="Name of the solid feature to pattern.")
    axis: str = Field(
        description="Principal axis direction: 'X_Axis', 'Y_Axis', or 'Z_Axis'."
    )
    angle: float = Field(
        description="Pattern angle in degrees (must be finite, positive, and at most 360)."
    )
    occurrences: int = Field(
        description="Number of pattern occurrences (must be >= 2)."
    )
    polar_pattern_name: str = Field(
        default="PolarPattern", description="Name for the new PartDesign::PolarPattern object."
    )
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the target.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class PartDesignMirroredParams(BaseModel):
    """Parameters for creating a PartDesign::Mirrored on an existing solid feature."""

    document_id: str = Field(description="Existing document containing the target feature.")
    object_name: str = Field(description="Name of the solid feature to mirror.")
    plane: str = Field(
        description="Mirror plane: 'XY_Plane', 'XZ_Plane', or 'YZ_Plane'."
    )
    mirrored_name: str = Field(
        default="Mirrored", description="Name for the new PartDesign::Mirrored object."
    )
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the target.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class PartDesignThicknessParams(BaseModel):
    """Parameters for creating a PartDesign::Thickness on an existing solid feature."""

    document_id: str = Field(description="Existing document containing the target feature.")
    object_name: str = Field(description="Name of the solid feature to apply thickness to.")
    thickness: float = Field(
        description="Wall thickness in millimetres (must be finite and positive)."
    )
    reversed: bool = Field(
        default=False, description="Whether to offset in the reversed direction."
    )
    thickness_name: str = Field(
        default="Thickness", description="Name for the new PartDesign::Thickness object."
    )
    body_name: str = Field(default="Body", description="Name of the PartDesign::Body containing the target.")
    faces: list[int] = Field(
        default=[],
        description="1-based face indices of the target feature to remove/open (e.g. [6])."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class RecomputeParams(BaseModel):
    """Parameters for recomputing a document."""

    document_id: str = Field(description="Document to recompute.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class ListObjectsParams(BaseModel):
    """Parameters for listing objects in a document."""

    document_id: str = Field(description="Document to list objects from.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class InspectObjectParams(BaseModel):
    """Parameters for inspecting a single object."""

    document_id: str = Field(description="Document containing the object.")
    object_name: str = Field(description="Name of the object to inspect.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class ValidateDocParams(BaseModel):
    """Parameters for validating a document's geometry."""

    document_id: str = Field(description="Document to validate.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class SaveDocumentParams(BaseModel):
    """Parameters for explicitly saving a document."""

    document_id: str = Field(description="Document to save.")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class ExportStepParams(BaseModel):
    """Parameters for exporting objects to STEP format."""

    document_id: str = Field(description="Document to export from.")
    object_names: Optional[list[str]] = Field(
        default=None,
        description="Optional list of object names to export. If omitted, all Part objects are exported.",
    )
    step_path: Optional[str] = Field(
        default=None,
        description="Optional absolute path for the STEP file. If omitted, defaults to <workspace>/<doc_id>.step.",
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class BooleanCutParams(BaseModel):
    """Parameters for subtracting one Part shape from another."""

    document_id: str = Field(description="Existing document containing both objects.")
    base_object: str = Field(
        description="Name of the object to retain after subtraction."
    )
    tool_object: str = Field(
        description="Name of the object to subtract from the base object."
    )
    result_name: Optional[str] = Field(
        default=None,
        description=(
            "Optional name for the resulting object (default 'Cut1'). An "
            "existing name is suffixed rather than overwritten."
        ),
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class BooleanFuseParams(BaseModel):
    """Parameters for fusing two Part shapes."""

    document_id: str = Field(description="Existing document containing both objects.")
    base_object: str = Field(
        description="Name of the first object to fuse."
    )
    tool_object: str = Field(
        description="Name of the second object to fuse into the base object."
    )
    result_name: Optional[str] = Field(
        default=None,
        description=(
            "Optional name for the resulting object (default 'Fuse1'). An "
            "existing name is suffixed rather than overwritten."
        ),
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class BooleanCommonParams(BaseModel):
    """Parameters for intersecting two Part shapes."""

    document_id: str = Field(description="Existing document containing both objects.")
    base_object: str = Field(
        description="Name of the first object to intersect."
    )
    tool_object: str = Field(
        description="Name of the second object to intersect with the base object."
    )
    result_name: Optional[str] = Field(
        default=None,
        description=(
            "Optional name for the resulting object (default 'Common1'). An "
            "existing name is suffixed rather than overwritten."
        ),
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


# ---------------------------------------------------------------------------
# Tank and tubular coil input schemas
# ---------------------------------------------------------------------------

class TankParams(BaseModel):
    """Parameters for creating a tank (rectangular box)."""

    document_id: str = Field(description="Existing document to add the tank to.")
    object_name: str = Field(default="Tank1", description="Name for the new Tank object.")
    length: float = Field(default=20.0, description="Length in millimetres (must be positive).")
    width: float = Field(default=15.0, description="Width in millimetres (must be positive).")
    height: float = Field(default=10.0, description="Height in millimetres (must be positive).")
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )


class TubularCoilParams(BaseModel):
    """Parameters for creating a tubular coil (helix with pipe)."""

    document_id: str = Field(description="Existing document to add the coil to.")
    object_name: str = Field(default="Coil1", description="Name for the new Coil object.")
    coil_radius: float = Field(
        default=10.0, description="Radius of the coil center path in millimetres (must be positive)."
    )
    tube_radius: float = Field(
        default=2.0, description="Radius of the tube in millímetros (must be positive)."
    )
    num_turns: int = Field(default=5, description="Number of coil turns (must be positive).")
    pitch: float = Field(
        default=5.0, description="Pitch (height per turn) in millimetres (must be positive)."
    )
    workspace: Optional[str] = Field(
        default=None, description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE env or cwd/freecad-mcp-workspace."
    )

# ---------------------------------------------------------------------------
# Bridge helper
# ---------------------------------------------------------------------------


def _make_bridge(settings: Settings) -> FreeCadBridge:
    """Create a FreeCadBridge from the given settings."""
    return FreeCadBridge.from_settings(settings)


def _run_bridge_op(
    bridge: FreeCadBridge,
    op: str,
    params: dict[str, Any],
    *,
    timeout: Optional[float] = None,
) -> dict[str, Any]:
    """Run a bridge operation and return the result dict, raising on failure."""
    result = DirectBackend(bridge).execute(BuildRequest(op, params, timeout))
    return dict(result.data)


# ---------------------------------------------------------------------------
# Modelling tools
# ---------------------------------------------------------------------------


def _register_tools(server: Any, settings: Settings) -> None:
    """Internal: register all modelling tools on the MCP server.

    This is called from ``tools/__init__.py``'s ``register_all``.
    """

    # ------------------------------------------------------------------
    # create_document
    # ------------------------------------------------------------------
    @server.tool(
        name="create_document",
        title="Create document",
        description=(
            "Create a new empty FreeCAD document and save it atomically to the "
            "controlled workspace. The *document_id* is validated to be a safe "
            "filename (letters, digits, '.', '_', '-', max 64 chars).  The "
            "document is stored under ``<workspace>/<document_id>.fcstd`` and "
            "its metadata revision is set to 1.  Use ``create_box`` or "
            "``create_cylinder`` to add geometry."
        ),
        structured_output=True,
    )
    def create_document_tool(params: CreateDocumentParams) -> DocumentList:
        """Create a new document and return basic info."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "create_document", bridge_params)

        # Build object list (empty for a fresh document)
        objects = []
        return DocumentList(
            document_id=params.document_id,
            objects=objects,
        )

    # ------------------------------------------------------------------
    # create_box
    # ------------------------------------------------------------------
    @server.tool(
        name="create_box",
        title="Create box",
        description=(
            "Add a Part::Box to an existing document.  The document is locked "
            "exclusively during the operation; other processes cannot modify it "
            "concurrently.  Parameters *length*, *width*, *height* must be "
            "positive numbers (millimetres)."
        ),
        structured_output=True,
    )
    def create_box_tool(params: CreateBoxParams) -> ObjectInfo:
        """Create a box and return its info."""
        # Manual positive-check (Pydantic doesn't enforce it strictly here)
        validate_direct_request("create_box", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "length": params.length,
            "width": params.width,
            "height": params.height,
            "x": params.x,
            "y": params.y,
            "z": params.z,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "create_box", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", "Part::Box"),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
        )
        return info

    # ------------------------------------------------------------------
    # create_cylinder
    # ------------------------------------------------------------------
    @server.tool(
        name="create_cylinder",
        title="Create cylinder",
        description=(
            "Add a Part::Cylinder to an existing document.  The document is "
            "locked exclusively during the operation.  Parameters *radius* and "
            "*height* must be positive numbers (millimetres)."
        ),
        structured_output=True,
    )
    def create_cylinder_tool(params: CreateCylinderParams) -> ObjectInfo:
        """Create a cylinder and return its info."""
        validate_direct_request("create_cylinder", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "radius": params.radius,
            "height": params.height,
            "x": params.x,
            "y": params.y,
            "z": params.z,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "create_cylinder", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", "Part::Cylinder"),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            radius=result.get("radius"),
            height=result.get("height"),
        )
        return info

    # ------------------------------------------------------------------
    # set_parameter
    # ------------------------------------------------------------------
    @server.tool(
        name="set_parameter",
        title="Set parameter",
        description=(
            "Change a whitelisted parameter on an existing object.  Currently "
            "whitelisted properties are:\n"
            "  - Box: Length, Width, Height\n"
            "  - Cylinder: Radius, Height\n"
            "The value must be a positive number (millimetres).  The document "
            "is locked exclusively during the operation."
        ),
        structured_output=True,
    )
    def set_parameter_tool(params: SetParameterParams) -> ObjectInfo:
        """Set a parameter and return the object's updated info."""
        validate_direct_request("set_parameter", {"property": params.property_name, "value": params.value})

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "property": params.property_name,
            "value": params.value,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "set_parameter", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", ""),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
            radius=result.get("radius"),
        )
        return info

    # ------------------------------------------------------------------
    # set_placement
    # ------------------------------------------------------------------
    @server.tool(
        name="set_placement",
        title="Set placement",
        description=(
            "Change the position (X/Y/Z) of an existing Box or Cylinder without "
            "recreating it.  The object's existing Rotation is preserved and only "
            "its translation is updated.  Supported object types are Part::Box "
            "and Part::Cylinder.  The document is locked exclusively during the "
            "operation and saved atomically."
        ),
        structured_output=True,
    )
    def set_placement_tool(params: SetPlacementParams) -> ObjectInfo:
        """Set the placement of an existing object and return its updated info."""
        # Manual finite-number check before delegating to the bridge.
        for name, value in (("x", params.x), ("y", params.y), ("z", params.z)):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "x": params.x,
            "y": params.y,
            "z": params.z,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "set_placement", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", ""),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
            radius=result.get("radius"),
        )
        return info

    # ------------------------------------------------------------------
    # recompute
    # ------------------------------------------------------------------
    @server.tool(
        name="recompute",
        title="Recompute document",
        description=(
            "Recompute the active document geometry.  Useful after modifying "
            "parameters to update the model.  The document is locked during "
            "the operation."
        ),
        structured_output=True,
    )
    def recompute_tool(params: RecomputeParams) -> dict[str, Any]:
        """Recompute the document and return a status dict."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "workspace": params.workspace or "",
        }
        return _run_bridge_op(bridge, "recompute", bridge_params)

    # ------------------------------------------------------------------
    # list_objects
    # ------------------------------------------------------------------
    @server.tool(
        name="list_objects",
        title="List objects",
        description=(
            "List all objects in a document, returning their names, types, "
            "and basic geometry information (shape type, volume, dimensions)."
        ),
        structured_output=True,
    )
    def list_objects_tool(params: ListObjectsParams) -> DocumentList:
        """List objects and return structured info."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "list_objects", bridge_params)

        objects = []
        for obj in result.get("objects", []):
            oi = ObjectInfo(
                name=obj.get("name", ""),
                type=obj.get("type", ""),
                shape_type=obj.get("shape_type"),
                volume=obj.get("volume"),
                length=obj.get("length"),
                width=obj.get("width"),
                height=obj.get("height"),
                radius=obj.get("radius"),
            )
            objects.append(oi)

        all_valid = result.get("all_valid")
        return DocumentList(
            document_id=params.document_id,
            objects=objects,
            all_valid=all_valid,
        )

    # ------------------------------------------------------------------
    # inspect_object
    # ------------------------------------------------------------------
    @server.tool(
        name="inspect_object",
        title="Inspect object",
        description=(
            "Return detailed information about a single object by name, "
            "including its type, shape type, volume, and dimensional "
            "parameters (Length/Width/Height/Radius)."
        ),
        structured_output=True,
    )
    def inspect_object_tool(params: InspectObjectParams) -> ObjectInfo:
        """Inspect a single object and return its info."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "inspect_object", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", ""),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
            radius=result.get("radius"),
        )
        return info

    # ------------------------------------------------------------------
    # validate_document
    # ------------------------------------------------------------------
    @server.tool(
        name="validate_document",
        title="Validate document",
        description=(
            "Validate the geometry of all objects in a document.  Returns "
            "per-object results indicating whether each object is a valid "
            "Solid with positive volume, plus an overall ``all_valid`` flag. "
            "Use after modifying parameters or before exporting STEP."
        ),
        structured_output=True,
    )
    def validate_doc_tool(params: ValidateDocParams) -> ValidationResult:
        """Validate the document and return structured results."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "validate", bridge_params)

        objects = result.get("objects", [])
        obj_dicts = []
        for obj_result in objects:
            d = {
                "name": obj_result.get("name", ""),
                "type": obj_result.get("type", ""),
                "valid": obj_result.get("valid", False),
                "errors": obj_result.get("errors", []),
            }
            obj_dicts.append(d)

        all_valid = result.get("all_valid", False)
        return ValidationResult(
            document_id=params.document_id,
            all_valid=all_valid,
            objects=obj_dicts,
        )

    # ------------------------------------------------------------------
    # save_document
    # ------------------------------------------------------------------
    @server.tool(
        name="save_document",
        title="Save document",
        description=(
            "Explicitly save the current document atomically.  This is "
            "equivalent to the auto-save that happens after create_box/create_cylinder/"
            "set_parameter, but can be called independently.  After a "
            "successful save the document's metadata revision is incremented."
        ),
        structured_output=True,
    )
    def save_document_tool(params: SaveDocumentParams) -> StepExportResult:
        """Save the document and return status."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "save_document", bridge_params)

        return StepExportResult(
            document_id=params.document_id,
            step_path=result.get("saved_path", ""),
            exported_count=0,
            success=True,
        )

    # ------------------------------------------------------------------
    # export_step
    # ------------------------------------------------------------------
    @server.tool(
        name="export_step",
        title="Export STEP",
        description=(
            "Export selected objects from a document to STEP format.  If "
            "\"object_names\" are provided, only those objects are exported; "
            "otherwise all Part objects in the document are exported.  The "
            "default output path is ``<workspace>/<document_id>.step``, but "
            "a custom path may be supplied (must remain within the controlled "
            "workspace)."
        ),
        structured_output=True,
    )
    def export_step_tool(params: ExportStepParams) -> StepExportResult:
        """Export to STEP and return result."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_names": params.object_names,
            "step_path": params.step_path,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "export_step", bridge_params)

        return StepExportResult(
            document_id=params.document_id,
            step_path=result.get("step_path", ""),
            exported_count=result.get("exported_count", 0),
            success=result.get("success", True),
        )

    # ------------------------------------------------------------------
    # boolean_cut
    # ------------------------------------------------------------------
    @server.tool(
        name="boolean_cut",
        title="Boolean cut",
        description=(
            "Subtract one existing Part shape from another using FreeCAD's "
            "native Part Boolean cut.  *base_object* is retained and "
            "*tool_object* is subtracted from it.  The operation creates a "
            "separate result object (named *result_name*, default 'Cut1') and "
            "leaves both source objects unchanged.  Both objects must exist in "
            "*document_id* and have valid Solid shapes; identical names, "
            "invalid/empty shapes and empty cut results are reported as "
            "structured errors.  The document is locked exclusively during the "
            "operation and saved atomically."
        ),
        structured_output=True,
    )
    def boolean_cut_tool(params: BooleanCutParams) -> BooleanCutResult:
        """Subtract *tool_object* from *base_object* and return result info."""
        validate_direct_request("boolean_cut", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "base_object": params.base_object,
            "tool_object": params.tool_object,
            "result_name": params.result_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "boolean_cut", bridge_params)

        return BooleanCutResult(
            document_id=params.document_id,
            object_name=result.get("object_name", ""),
            base_object=result.get("base_object", params.base_object),
            tool_object=result.get("tool_object", params.tool_object),
            type=result.get("type", "Part::Feature"),
            shape_type=result.get("shape_type", ""),
            volume=result.get("volume", 0.0),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
        )

    # ------------------------------------------------------------------
    # boolean_fuse
    # ------------------------------------------------------------------
    @server.tool(
        name="boolean_fuse",
        title="Boolean fuse",
        description=(
            "Fuse (union) two existing Part shapes using FreeCAD's native "
            "Part Boolean fuse.  Both objects are retained and a separate "
            "result object (named *result_name*, default 'Fuse1') is created. "
            "Both objects must exist in *document_id* and have valid Solid "
            "shapes; identical names, invalid/empty shapes and empty fuse "
            "results are reported as structured errors.  The document is "
            "locked exclusively during the operation and saved atomically."
        ),
        structured_output=True,
    )
    def boolean_fuse_tool(params: BooleanFuseParams) -> BooleanFuseResult:
        """Fuse *base_object* with *tool_object* and return result info."""
        validate_direct_request("boolean_fuse", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "base_object": params.base_object,
            "tool_object": params.tool_object,
            "result_name": params.result_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "boolean_fuse", bridge_params)

        return BooleanFuseResult(
            document_id=params.document_id,
            object_name=result.get("object_name", ""),
            base_object=result.get("base_object", params.base_object),
            tool_object=result.get("tool_object", params.tool_object),
            type=result.get("type", "Part::Feature"),
            shape_type=result.get("shape_type", ""),
            volume=result.get("volume", 0.0),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
        )

    # ------------------------------------------------------------------
    # boolean_common
    # ------------------------------------------------------------------
    @server.tool(
        name="boolean_common",
        title="Boolean common",
        description=(
            "Intersect (common) two existing Part shapes using FreeCAD's "
            "native Part Boolean common.  Both objects are retained and a "
            "separate result object (named *result_name*, default 'Common1') "
            "is created.  Both objects must exist in *document_id* and have "
            "valid Solid shapes; identical names, invalid/empty shapes and "
            "empty common results are reported as structured errors.  The "
            "document is locked exclusively during the operation and saved "
            "atomically."
        ),
        structured_output=True,
    )
    def boolean_common_tool(params: BooleanCommonParams) -> BooleanCommonResult:
        """Intersect *base_object* with *tool_object* and return result info."""
        validate_direct_request("boolean_common", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "base_object": params.base_object,
            "tool_object": params.tool_object,
            "result_name": params.result_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "boolean_common", bridge_params)

        return BooleanCommonResult(
            document_id=params.document_id,
            object_name=result.get("object_name", ""),
            base_object=result.get("base_object", params.base_object),
            tool_object=result.get("tool_object", params.tool_object),
            type=result.get("type", "Part::Feature"),
            shape_type=result.get("shape_type", ""),
            volume=result.get("volume", 0.0),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
        )

    # ------------------------------------------------------------------
    # create_sketch
    # ------------------------------------------------------------------
    @server.tool(
        name="create_sketch",
        title="Create sketch",
        description=(
            "Add a Sketcher::SketchObject to an existing document.  The sketch "
            "lies on the XY plane and starts empty; use ``sketch_add_rectangle`` "
            "or ``sketch_add_circle`` to add editable Sketcher geometry.  The "
            "document is locked exclusively during the operation and saved "
            "atomically."
        ),
        structured_output=True,
    )
    def create_sketch_tool(params: CreateSketchParams) -> CreateSketchResult:
        """Create a sketch and return its info."""
        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "create_sketch", bridge_params)

        return CreateSketchResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            type=result.get("type", "Sketcher::SketchObject"),
            plane=result.get("plane", "XY"),
            edges=result.get("edges", 0),
        )

    # ------------------------------------------------------------------
    # sketch_add_rectangle
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_add_rectangle",
        title="Add rectangle to sketch",
        description=(
            "Add four connected line segments forming a rectangle to an "
            "existing Sketcher::SketchObject.  The rectangle lies on the "
            "sketch XY plane (Z = 0), counter-clockwise from the lower-left "
            "corner (*x*, *y*).  *width* and *height* must be positive "
            "numbers (millimetres).  No constraints are added, so the "
            "geometry stays editable.  The document is locked exclusively "
            "during the operation and saved atomically."
        ),
        structured_output=True,
    )
    def sketch_add_rectangle_tool(params: SketchAddRectangleParams) -> SketchAddRectangleResult:
        """Add a rectangle to a sketch and return its info."""
        # Manual finite-number / positive-dimension check before delegating.
        validate_direct_request("sketch_add_rectangle", params.model_dump())
        for name, value in (
            ("x", params.x),
            ("y", params.y),
            ("width", params.width),
            ("height", params.height),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "x": params.x,
            "y": params.y,
            "width": params.width,
            "height": params.height,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_add_rectangle", bridge_params)

        return SketchAddRectangleResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            x=result.get("x", params.x),
            y=result.get("y", params.y),
            width=result.get("width", params.width),
            height=result.get("height", params.height),
            edges_added=result.get("edges_added", 4),
            total_edges=result.get("total_edges"),
        )

    # ------------------------------------------------------------------
    # sketch_add_circle
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_add_circle",
        title="Add circle to sketch",
        description=(
            "Add a proper Sketcher circle geometry to an existing "
            "Sketcher::SketchObject.  The circle lies on the sketch XY plane "
            "(Z = 0) at (*center_x*, *center_y*) with *radius*.  *radius* "
            "must be a positive number (millimetres).  No constraints are "
            "added, so the geometry stays editable.  The document is locked "
            "exclusively during the operation and saved atomically."
        ),
        structured_output=True,
    )
    def sketch_add_circle_tool(params: SketchAddCircleParams) -> SketchAddCircleResult:
        """Add a circle to a sketch and return its info."""
        validate_direct_request("sketch_add_circle", params.model_dump())
        for name, value in (("center_x", params.center_x), ("center_y", params.center_y), ("radius", params.radius)):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "center_x": params.center_x,
            "center_y": params.center_y,
            "radius": params.radius,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_add_circle", bridge_params)

        return SketchAddCircleResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            center_x=result.get("center_x", params.center_x),
            center_y=result.get("center_y", params.center_y),
            radius=result.get("radius", params.radius),
            edges_added=result.get("edges_added", 1),
            total_edges=result.get("total_edges"),
        )

# ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # sketch_add_triangle
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_add_triangle",
        title="Add triangle to sketch",
        description=(
            "Add three connected line segments forming a triangle to an "
            "existing Sketcher::SketchObject.  The triangle sides lie on the "
            "sketch XY plane (Z = 0) and share endpoints so the geometry is "
            "closed and topologically connected.  Vertex order is preserved. "
            "Coordinates must be finite and the three vertices must not be "
            "collinear (zero signed area).  No constraints are added, so the "
            "geometry stays editable.  The document is locked exclusively "
            "during the operation and saved atomically."
        ),
        structured_output=True,
    )
    def sketch_add_triangle_tool(params: SketchAddTriangleParams) -> SketchAddTriangleResult:
        """Add a triangle to a sketch and return its info."""
        validate_direct_request("sketch_add_triangle", params.model_dump())
        for name, value in (
            ("x1", params.x1),
            ("y1", params.y1),
            ("x2", params.x2),
            ("y2", params.y2),
            ("x3", params.x3),
            ("y3", params.y3),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "x1": params.x1,
            "y1": params.y1,
            "x2": params.x2,
            "y2": params.y2,
            "x3": params.x3,
            "y3": params.y3,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_add_triangle", bridge_params)

        return SketchAddTriangleResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            x1=result.get("x1", params.x1),
            y1=result.get("y1", params.y1),
            x2=result.get("x2", params.x2),
            y2=result.get("y2", params.y2),
            x3=result.get("x3", params.x3),
            y3=result.get("y3", params.y3),
            edges_added=result.get("edges_added", 3),
            total_edges=result.get("total_edges"),
        )

    # ------------------------------------------------------------------
    # sketch_add_ellipse
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_add_ellipse",
        title="Add ellipse to sketch",
        description=(
            "Add a single axis-aligned ellipse geometry to an existing "
            "Sketcher::SketchObject.  The ellipse centre lies on the sketch "
            "XY plane (Z = 0) at (*center_x*, *center_y*) with *major_radius* "
            "and *minor_radius*.  Both radii must be positive numbers "
            "(millimetres).  The major axis is aligned with the sketch X "
            "direction and the minor axis with the sketch Y direction.  No "
            "constraints are added, so the geometry stays editable.  The "
            "document is locked exclusively during the operation and saved "
            "atomically."
        ),
        structured_output=True,
    )
    def sketch_add_ellipse_tool(params: SketchAddEllipseParams) -> SketchAddEllipseResult:
        """Add an ellipse to a sketch and return its info."""
        validate_direct_request("sketch_add_ellipse", params.model_dump())
        for name, value in (
            ("center_x", params.center_x),
            ("center_y", params.center_y),
            ("major_radius", params.major_radius),
            ("minor_radius", params.minor_radius),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "center_x": params.center_x,
            "center_y": params.center_y,
            "major_radius": params.major_radius,
            "minor_radius": params.minor_radius,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_add_ellipse", bridge_params)

        return SketchAddEllipseResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            center_x=result.get("center_x", params.center_x),
            center_y=result.get("center_y", params.center_y),
            major_radius=result.get("major_radius", params.major_radius),
            minor_radius=result.get("minor_radius", params.minor_radius),
            edges_added=result.get("edges_added", 1),
            total_edges=result.get("total_edges"),
        )

    # ------------------------------------------------------------------
    # sketch_add_regular_polygon
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_add_regular_polygon",
        title="Add regular polygon to sketch",
        description=(
            "Add a regular polygon to an existing Sketcher::SketchObject.  "
            "Vertices lie on a circle centred at (*center_x*, *center_y*) on "
            "the sketch XY plane (Z = 0) with the given *radius* (circumradius).  "
            "Consecutive vertices are connected by line segments and the last "
            "vertex closes back to the first.  *rotation_deg* rotates the first "
            "vertex counter-clockwise from the positive X axis.  *sides* must "
            "be at least 3 and at most MAX_POLYGON_SIDES.  No constraints are "
            "added, so the geometry stays editable.  The document is locked "
            "exclusively during the operation and saved atomically."
        ),
        structured_output=True,
    )
    def sketch_add_regular_polygon_tool(params: SketchAddRegularPolygonParams) -> SketchAddRegularPolygonResult:
        """Add a regular polygon to a sketch and return its info."""
        validate_direct_request("sketch_add_regular_polygon", params.model_dump())
        for name, value in (
            ("center_x", params.center_x),
            ("center_y", params.center_y),
            ("radius", params.radius),
            ("rotation_deg", params.rotation_deg),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "center_x": params.center_x,
            "center_y": params.center_y,
            "radius": params.radius,
            "sides": params.sides,
            "rotation_deg": params.rotation_deg,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_add_regular_polygon", bridge_params)

        return SketchAddRegularPolygonResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            center_x=result.get("center_x", params.center_x),
            center_y=result.get("center_y", params.center_y),
            radius=result.get("radius", params.radius),
            sides=result.get("sides", params.sides),
            rotation_deg=result.get("rotation_deg", params.rotation_deg),
            edges_added=result.get("edges_added", params.sides),
            total_edges=result.get("total_edges"),
        )

    # ------------------------------------------------------------------
    # sketch_add_polygon
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_add_polygon",
        title="Add custom polygon to sketch",
        description=(
            "Add a custom polygon from ordered vertices to an existing "
            "Sketcher::SketchObject.  Each vertex is connected to the next "
            "and the last back to the first via line segments on the sketch "
            "XY plane (Z = 0).  Vertex order is preserved; no reordering or "
            "repair is performed.  At least 3 vertices are required; "
            "consecutive duplicates, a repeated final vertex equal to the "
            "first, and zero-area polygons are rejected.  Self-intersection "
            "is not validated.  No constraints are added.  The document is "
            "locked exclusively during the operation and saved atomically."
        ),
        structured_output=True,
    )
    def sketch_add_polygon_tool(params: SketchAddPolygonParams) -> SketchAddPolygonResult:
        """Add a custom polygon to a sketch and return its info."""
        validate_direct_request("sketch_add_polygon", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "vertices": params.vertices,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_add_polygon", bridge_params)

        return SketchAddPolygonResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            edges_added=result.get("edges_added", len(params.vertices)),
            total_edges=result.get("total_edges"),
        )

    # ------------------------------------------------------------------
    # inspect_sketch
    # ------------------------------------------------------------------
    @server.tool(
        name="inspect_sketch",
        title="Inspect sketch",
        description=(
            "Inspect an existing Sketcher::SketchObject and return structured "
            "information about its geometry (read-only).  For each geometry "
            "reports its index, type, relevant parameters (line endpoints, "
            "circle center/radius, ellipse center/radii/directions), "
            "construction status, and coordinates in the sketch-local XY "
            "plane.  Also reports geometry count, constraint count, and "
            "sketch placement if available.  Does not modify the document."
        ),
        structured_output=True,
    )
    def inspect_sketch_tool(params: InspectSketchParams) -> InspectSketchResult:
        """Inspect a sketch and return its geometry information."""
        validate_direct_request("inspect_sketch", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "inspect_sketch", bridge_params)

        return InspectSketchResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            type=result.get("type", "Sketcher::SketchObject"),
            geometry_count=result.get("geometry_count", 0),
            constraint_count=result.get("constraint_count"),
            geometries=result.get("geometries", []),
            placement=result.get("placement", {}),
        )

    # ------------------------------------------------------------------
    # sketch_delete_geometry
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_delete_geometry",
        title="Delete geometry from sketch",
        description=(
            "Delete one geometry element from an existing Sketcher::SketchObject "
            "by its zero-based geometry index.  The document is locked "
            "exclusively during the operation, recomputed, saved atomically, "
            "and its revision is bumped.  FreeCAD automatically removes any "
            "constraints that referenced the deleted geometry."
        ),
        structured_output=True,
    )
    def sketch_delete_geometry_tool(params: SketchDeleteGeometryParams) -> SketchDeleteGeometryResult:
        """Delete geometry from a sketch and return its info."""
        validate_direct_request("sketch_delete_geometry", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_delete_geometry", bridge_params)

        return SketchDeleteGeometryResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            deleted_index=result.get("deleted_index", params.geometry_index),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_line
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_line",
        title="Edit line geometry in sketch",
        description=(
            "Edit an existing line geometry in a Sketcher::SketchObject by its "
            "zero-based geometry index.  Validates that the geometry is a line "
            "segment, that all coordinates are finite and non-degenerate, and "
            "that no constraints reference the geometry being edited.  The "
            "document is locked exclusively during the operation, recomputed, "
            "saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_line_tool(params: SketchEditLineParams) -> SketchEditLineResult:
        """Edit line geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_line", params.model_dump())
        for name, value in (
            ("start_x", params.start_x),
            ("start_y", params.start_y),
            ("end_x", params.end_x),
            ("end_y", params.end_y),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "start_x": params.start_x,
            "start_y": params.start_y,
            "end_x": params.end_x,
            "end_y": params.end_y,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_line", bridge_params)

        return SketchEditLineResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            start_x=result.get("start_x", params.start_x),
            start_y=result.get("start_y", params.start_y),
            end_x=result.get("end_x", params.end_x),
            end_y=result.get("end_y", params.end_y),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_circle
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_circle",
        title="Edit circle geometry in sketch",
        description=(
            "Edit an existing circle geometry in a Sketcher::SketchObject by its "
            "zero-based geometry index.  Validates that the geometry is a circle, "
            "that all coordinates and radius are finite with radius > 0, and that "
            "no constraints reference the geometry being edited.  The document is "
            "locked exclusively during the operation, recomputed, saved atomically, "
            "and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_circle_tool(params: SketchEditCircleParams) -> SketchEditCircleResult:
        """Edit circle geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_circle", params.model_dump())
        for name, value in (("center_x", params.center_x), ("center_y", params.center_y), ("radius", params.radius)):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "center_x": params.center_x,
            "center_y": params.center_y,
            "radius": params.radius,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_circle", bridge_params)

        return SketchEditCircleResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            center_x=result.get("center_x", params.center_x),
            center_y=result.get("center_y", params.center_y),
            radius=result.get("radius", params.radius),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_ellipse
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_ellipse",
        title="Edit ellipse geometry in sketch",
        description=(
            "Edit an existing ellipse geometry in a Sketcher::SketchObject by its "
            "zero-based geometry index.  *major_radius* and *minor_radius* are "
            "semi-axis lengths (millimetres); both must be positive and "
            "major_radius must be >= minor_radius.  Validates that the geometry "
            "is an ellipse, that all values are finite, and that no constraints "
            "reference the geometry being edited.  *rotation_deg* rotates the "
            "major axis counter-clockwise from the sketch +X direction "
            "(degrees; default 0 = axis-aligned).  The document is locked "
            "exclusively during the operation, recomputed, saved atomically, "
            "and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_ellipse_tool(params: SketchEditEllipseParams) -> SketchEditEllipseResult:
        """Edit ellipse geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_ellipse", params.model_dump())
        for name, value in (
            ("center_x", params.center_x),
            ("center_y", params.center_y),
            ("major_radius", params.major_radius),
            ("minor_radius", params.minor_radius),
            ("rotation_deg", params.rotation_deg),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "center_x": params.center_x,
            "center_y": params.center_y,
            "major_radius": params.major_radius,
            "minor_radius": params.minor_radius,
            "rotation_deg": params.rotation_deg,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_ellipse", bridge_params)

        return SketchEditEllipseResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            center_x=result.get("center_x", params.center_x),
            center_y=result.get("center_y", params.center_y),
            major_radius=result.get("major_radius", params.major_radius),
            minor_radius=result.get("minor_radius", params.minor_radius),
            rotation_deg=result.get("rotation_deg", params.rotation_deg),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_rectangle
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_rectangle",
        title="Edit rectangle geometry in sketch",
        description=(
            "Edit an existing rectangle (four connected LineSegment geometries) "
            "in a Sketcher::SketchObject.  *geometry_index* is the index of the "
            "first line of the rectangle; the rectangle occupies four consecutive "
            "geometry slots.  *x*, *y* are the new lower-left corner before "
            "rotation, and *width*, *height* are positive dimensions "
            "(millimetres).  *rotation_deg* rotates the rectangle around its "
            "centre (degrees; default 0 = axis-aligned).  Validates that the "
            "four geometries actually form a connected rectangle, that all values "
            "are finite, and that no constraints reference the geometry being "
            "edited.  The document is locked exclusively during the operation, "
            "recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_rectangle_tool(params: SketchEditRectangleParams) -> SketchEditRectangleResult:
        """Edit rectangle geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_rectangle", params.model_dump())
        for name, value in (
            ("x", params.x),
            ("y", params.y),
            ("width", params.width),
            ("height", params.height),
            ("rotation_deg", params.rotation_deg),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "x": params.x,
            "y": params.y,
            "width": params.width,
            "height": params.height,
            "rotation_deg": params.rotation_deg,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_rectangle", bridge_params)

        return SketchEditRectangleResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            x=result.get("x", params.x),
            y=result.get("y", params.y),
            width=result.get("width", params.width),
            height=result.get("height", params.height),
            rotation_deg=result.get("rotation_deg", params.rotation_deg),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_triangle
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_triangle",
        title="Edit triangle geometry in sketch",
        description=(
            "Edit an existing triangle (three connected LineSegment geometries) "
            "in a Sketcher::SketchObject.  *geometry_index* is the index of the "
            "first line of the triangle; the triangle occupies three consecutive "
            "geometry slots.  *vertices* is a list of exactly three ordered "
            "{x, y} vertices in millimetres; vertex ordering is preserved and the "
            "three segments connect vertex 0->1, 1->2, and 2->0 (closing back to "
            "the start).  Validates that the three geometries actually form a "
            "connected, non-degenerate triangle, that all coordinates are finite, "
            "and that no constraints reference the geometry being edited.  The "
            "document is locked exclusively during the operation, recomputed, "
            "saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_triangle_tool(params: SketchEditTriangleParams) -> SketchEditTriangleResult:
        """Edit triangle geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_triangle", params.model_dump())
        for i, v in enumerate(params.vertices):
            if not isinstance(v, dict) or "x" not in v or "y" not in v:
                raise ValueError(f"vertices[{i}] must be an object with 'x' and 'y' keys.")
            for name in ("x", "y"):
                if math.isnan(v[name]) or math.isinf(v[name]):
                    raise ValueError(f"vertices[{i}].{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "vertices": params.vertices,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_triangle", bridge_params)

        return SketchEditTriangleResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            vertices=result.get("vertices", params.vertices),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_regular_polygon
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_regular_polygon",
        title="Edit regular polygon geometry in sketch",
        description=(
            "Edit an existing regular polygon (``sides`` connected LineSegment "
            "geometries) in a Sketcher::SketchObject.  *geometry_index* is the "
            "index of the first line of the polygon; the polygon occupies "
            "*sides* consecutive geometry slots.  Vertices lie on a circle "
            "centred at *center_x*, *center_y* with *radius* circumradius, and "
            "*rotation_deg* rotates the first vertex counter-clockwise from +X, "
            "matching sketch_add_regular_polygon exactly.  Validates that the "
            "selected geometries form a regular polygon (equal edge lengths, "
            "equal distance from centre, connected closed loop, non-degenerate), "
            "that all values are finite, and that no constraints reference the "
            "geometry being edited.  The document is locked exclusively during "
            "the operation, recomputed, saved atomically, and its revision is "
            "bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_regular_polygon_tool(params: SketchEditRegularPolygonParams) -> SketchEditRegularPolygonResult:
        """Edit regular polygon geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_regular_polygon", params.model_dump())
        for name, value in (
            ("center_x", params.center_x),
            ("center_y", params.center_y),
            ("radius", params.radius),
            ("rotation_deg", params.rotation_deg),
        ):
            if math.isnan(value) or math.isinf(value):
                raise ValueError(f"{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "center_x": params.center_x,
            "center_y": params.center_y,
            "radius": params.radius,
            "sides": params.sides,
            "rotation_deg": params.rotation_deg,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_regular_polygon", bridge_params)

        return SketchEditRegularPolygonResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            center_x=result.get("center_x", params.center_x),
            center_y=result.get("center_y", params.center_y),
            radius=result.get("radius", params.radius),
            sides=result.get("sides", params.sides),
            rotation_deg=result.get("rotation_deg", params.rotation_deg),
            vertices=result.get("vertices", []),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_edit_polygon
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_edit_polygon",
        title="Edit polygon geometry in sketch",
        description=(
            "Edit an existing custom polygon (connected LineSegment geometries) "
            "in a Sketcher::SketchObject.  *geometry_index* is the index of the "
            "first line of the polygon; the polygon occupies "
            "len(vertices) consecutive geometry slots.  The number of supplied "
            "vertices must match the existing polygon's number of edges.  Vertex "
            "ordering is preserved; no reordering or repair is performed.  The "
            "final edge connects the last vertex back to the first (closed "
            "loop).  Validates that the selected geometries form a valid closed "
            "polygon, that all coordinates are finite, and that no constraints "
            "reference the geometry being edited.  The document is locked "
            "exclusively during the operation, recomputed, saved atomically, "
            "and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_edit_polygon_tool(params: SketchEditPolygonParams) -> SketchEditPolygonResult:
        """Edit polygon geometry in a sketch and return its info."""
        validate_direct_request("sketch_edit_polygon", params.model_dump())
        for i, v in enumerate(params.vertices):
            if not isinstance(v, dict) or "x" not in v or "y" not in v:
                raise ValueError(f"vertices[{i}] must be an object with 'x' and 'y' keys.")
            for name in ("x", "y"):
                if math.isnan(v[name]) or math.isinf(v[name]):
                    raise ValueError(f"vertices[{i}].{name} must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "geometry_index": params.geometry_index,
            "vertices": params.vertices,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_edit_polygon", bridge_params)

        return SketchEditPolygonResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            geometry_index=result.get("geometry_index", params.geometry_index),
            vertices=result.get("vertices", params.vertices),
            geometry_count=result.get("geometry_count", 0),
            constraint_count_before=result.get("constraint_count_before"),
            constraint_count_after=result.get("constraint_count_after"),
        )

    # ------------------------------------------------------------------
    # sketch_pad
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_pad",
        title="Create a PartDesign Pad from a sketch",
        description=(
            "Create a native PartDesign::Pad that extrudes an existing "
            "Sketcher::SketchObject into a 3D solid.  A PartDesign::Body is "
            "created if the sketch is not already inside one; otherwise the "
            "existing Body is reused.  The sketch is added to the Body via "
            "body.addObject(sketch), preserving its identity, geometry, and "
            "parametric editability.  The Pad profile is set to the sketch, "
            "and *length* and *reversed* are applied.  The document is "
            "recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_pad_tool(params: SketchPadParams) -> SketchPadResult:
        """Create a PartDesign Pad from a sketch and return its info."""
        validate_direct_request("sketch_pad", params.model_dump())
        if math.isnan(params.length) or math.isinf(params.length):
            raise ValueError("length must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "length": params.length,
            "reversed": params.reversed,
            "pad_name": params.pad_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_pad", bridge_params)

        return SketchPadResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            body_name=result.get("body_name", params.body_name),
            pad_name=result.get("pad_name", params.pad_name),
            profile_name=result.get("profile_name", params.sketch_name),
            length=result.get("length", params.length),
            reversed=result.get("reversed", params.reversed),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # sketch_pocket
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_pocket",
        title="Create a PartDesign Pocket from a sketch",
        description=(
            "Create a native PartDesign::Pocket that cuts an existing "
            "Sketcher::SketchObject out of a solid in a PartDesign::Body. "
            "The sketch must already belong to a Body, or the document must "
            "contain exactly one (unambiguous) Body.  A new Body is never "
            "silently created for a Pocket -- a Pocket requires an existing "
            "solid feature to cut from.  The Pocket profile is set to the "
            "sketch, and *length* and *reversed* are applied.  The document "
            "is recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_pocket_tool(params: SketchPocketParams) -> SketchPocketResult:
        """Create a PartDesign Pocket from a sketch and return its info."""
        validate_direct_request("sketch_pocket", params.model_dump())
        if math.isnan(params.length) or math.isinf(params.length):
            raise ValueError("length must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "length": params.length,
            "reversed": params.reversed,
            "pocket_name": params.pocket_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_pocket", bridge_params)

        return SketchPocketResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            body_name=result.get("body_name", params.body_name),
            pocket_name=result.get("pocket_name", params.pocket_name),
            profile_name=result.get("profile_name", params.sketch_name),
            length=result.get("length", params.length),
            reversed=result.get("reversed", params.reversed),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # sketch_revolution
    # ------------------------------------------------------------------
    @server.tool(
        name="sketch_revolution",
        title="Create a PartDesign Revolution from a sketch",
        description=(
            "Create a native PartDesign::Revolution that revolves an existing "
            "Sketcher::SketchObject around a principal origin axis.  A "
            "PartDesign::Body is created if the sketch is not already inside "
            "one; otherwise the existing Body is reused.  The sketch is added "
            "to the Body via body.addObject(sketch), preserving its identity, "
            "geometry, and parametric editability.  The Revolution profile is "
            "set to the sketch, and *axis* and *angle* are applied.  The "
            "document is recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def sketch_revolution_tool(params: SketchRevolutionParams) -> SketchRevolutionResult:
        """Create a PartDesign Revolution from a sketch and return its info."""
        validate_direct_request("sketch_revolution", params.model_dump())
        if math.isnan(params.angle) or math.isinf(params.angle):
            raise ValueError("angle must be a finite number.")
        if params.angle <= 0:
            raise ValueError("angle must be positive.")
        if params.angle > 360:
            raise ValueError("angle must not exceed 360 degrees.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "sketch_name": params.sketch_name,
            "axis": params.axis,
            "angle": params.angle,
            "revolution_name": params.revolution_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "sketch_revolution", bridge_params)

        return SketchRevolutionResult(
            document_id=params.document_id,
            sketch_name=result.get("sketch_name", params.sketch_name),
            body_name=result.get("body_name", params.body_name),
            revolution_name=result.get("revolution_name", params.revolution_name),
            profile_name=result.get("profile_name", params.sketch_name),
            axis=result.get("axis", params.axis),
            angle=result.get("angle", params.angle),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # partdesign_fillet
    # ------------------------------------------------------------------
    @server.tool(
        name="partdesign_fillet",
        title="Create a PartDesign Fillet on a solid feature",
        description=(
            "Create a native PartDesign::Fillet that rounds selected edges of an "
            "existing solid feature inside a PartDesign::Body.  The target feature "
            "must already belong to a Body, or the document must contain exactly one "
            "(unambiguous) Body.  *edge_indices* are 1-based edge indices of the "
            "target feature; *radius* must be finite and positive.  The document is "
            "recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def partdesign_fillet_tool(params: PartDesignFilletParams) -> PartDesignFilletResult:
        """Create a PartDesign Fillet on a solid feature and return its info."""
        validate_direct_request("partdesign_fillet", params.model_dump())
        if math.isnan(params.radius) or math.isinf(params.radius):
            raise ValueError("radius must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "edge_indices": params.edge_indices,
            "radius": params.radius,
            "fillet_name": params.fillet_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "partdesign_fillet", bridge_params)

        return PartDesignFilletResult(
            document_id=params.document_id,
            object_name=result.get("object_name", params.object_name),
            body_name=result.get("body_name", params.body_name),
            fillet_name=result.get("fillet_name", params.fillet_name),
            base_feature=result.get("base_feature", params.object_name),
            edge_indices=result.get("edge_indices", params.edge_indices),
            radius=result.get("radius", params.radius),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # partdesign_chamfer
    # ------------------------------------------------------------------
    @server.tool(
        name="partdesign_chamfer",
        title="Create a PartDesign Chamfer on a solid feature",
        description=(
            "Create a native PartDesign::Chamfer that chamfers selected edges of an "
            "existing solid feature inside a PartDesign::Body.  The target feature "
            "must already belong to a Body, or the document must contain exactly one "
            "(unambiguous) Body.  *edge_indices* are 1-based edge indices of the "
            "target feature; *size* must be finite and positive.  The document is "
            "recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def partdesign_chamfer_tool(params: PartDesignChamferParams) -> PartDesignChamferResult:
        """Create a PartDesign Chamfer on a solid feature and return its info."""
        validate_direct_request("partdesign_chamfer", params.model_dump())
        if math.isnan(params.size) or math.isinf(params.size):
            raise ValueError("size must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "edge_indices": params.edge_indices,
            "size": params.size,
            "chamfer_name": params.chamfer_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "partdesign_chamfer", bridge_params)

        return PartDesignChamferResult(
            document_id=params.document_id,
            object_name=result.get("object_name", params.object_name),
            body_name=result.get("body_name", params.body_name),
            chamfer_name=result.get("chamfer_name", params.chamfer_name),
            base_feature=result.get("base_feature", params.object_name),
            edge_indices=result.get("edge_indices", params.edge_indices),
            size=result.get("size", params.size),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # partdesign_linear_pattern
    # ------------------------------------------------------------------
    @server.tool(
        name="partdesign_linear_pattern",
        title="Create a PartDesign Linear Pattern on a solid feature",
        description=(
            "Create a native PartDesign::LinearPattern that patterns an existing "
            "solid feature along a principal axis inside a PartDesign::Body.  The "
            "target feature must already belong to a Body, or the document must "
            "contain exactly one (unambiguous) Body.  *direction* selects the "
            "principal axis ('X_Axis', 'Y_Axis', or 'Z_Axis'); *length* must be "
            "finite and positive; *occurrences* must be an integer >= 2.  The "
            "document is recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def partdesign_linear_pattern_tool(params: PartDesignLinearPatternParams) -> PartDesignLinearPatternResult:
        """Create a PartDesign Linear Pattern on a solid feature and return its info."""
        validate_direct_request("partdesign_linear_pattern", params.model_dump())
        if math.isnan(params.length) or math.isinf(params.length):
            raise ValueError("length must be a finite number.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "direction": params.direction,
            "length": params.length,
            "occurrences": params.occurrences,
            "linear_pattern_name": params.linear_pattern_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "partdesign_linear_pattern", bridge_params)

        return PartDesignLinearPatternResult(
            document_id=params.document_id,
            object_name=result.get("object_name", params.object_name),
            body_name=result.get("body_name", params.body_name),
            linear_pattern_name=result.get("linear_pattern_name", params.linear_pattern_name),
            base_feature=result.get("base_feature", params.object_name),
            direction=result.get("direction", params.direction),
            direction_edge_index=result.get("direction_edge_index", 1),
            length=result.get("length", params.length),
            occurrences=result.get("occurrences", params.occurrences),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # partdesign_polar_pattern
    # ------------------------------------------------------------------
    @server.tool(
        name="partdesign_polar_pattern",
        title="Create a PartDesign Polar Pattern on a solid feature",
        description=(
            "Create a native PartDesign::PolarPattern that patterns an existing "
            "solid feature around a principal axis inside a PartDesign::Body.  The "
            "target feature must already belong to a Body, or the document must "
            "contain exactly one (unambiguous) Body.  *axis* selects the "
            "principal axis ('X_Axis', 'Y_Axis', or 'Z_Axis'); *angle* must be "
            "finite, positive, and at most 360 degrees; *occurrences* must be an "
            "integer >= 2.  The document is recomputed, saved atomically, and its "
            "revision is bumped."
        ),
        structured_output=True,
    )
    def partdesign_polar_pattern_tool(params: PartDesignPolarPatternParams) -> PartDesignPolarPatternResult:
        """Create a PartDesign Polar Pattern on a solid feature and return its info."""
        validate_direct_request("partdesign_polar_pattern", params.model_dump())
        if math.isnan(params.angle) or math.isinf(params.angle):
            raise ValueError("angle must be a finite number.")
        if params.angle <= 0:
            raise ValueError("angle must be positive")
        if params.angle > 360:
            raise ValueError("angle must be at most 360 degrees.")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "axis": params.axis,
            "angle": params.angle,
            "occurrences": params.occurrences,
            "polar_pattern_name": params.polar_pattern_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "partdesign_polar_pattern", bridge_params)

        return PartDesignPolarPatternResult(
            document_id=params.document_id,
            object_name=result.get("object_name", params.object_name),
            body_name=result.get("body_name", params.body_name),
            polar_pattern_name=result.get("polar_pattern_name", params.polar_pattern_name),
            base_feature=result.get("base_feature", params.object_name),
            axis=result.get("axis", params.axis),
            axis_edge_index=result.get("axis_edge_index", 1),
            angle=result.get("angle", params.angle),
            occurrences=result.get("occurrences", params.occurrences),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # partdesign_mirrored
    # ------------------------------------------------------------------
    @server.tool(
        name="partdesign_mirrored",
        title="Create a PartDesign Mirrored feature on a solid feature",
        description=(
            "Create a native PartDesign::Mirrored that mirrors an existing solid "
            "feature across a principal origin plane inside a PartDesign::Body.  The "
            "target feature must already belong to a Body, or the document must "
            "contain exactly one (unambiguous) Body.  *plane* selects the principal "
            "plane ('XY_Plane', 'XZ_Plane', or 'YZ_Plane').  The document is "
            "recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def partdesign_mirrored_tool(params: PartDesignMirroredParams) -> PartDesignMirroredResult:
        """Create a PartDesign Mirrored feature on a solid feature and return its info."""
        validate_direct_request("partdesign_mirrored", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "plane": params.plane,
            "mirrored_name": params.mirrored_name,
            "body_name": params.body_name,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "partdesign_mirrored", bridge_params)

        return PartDesignMirroredResult(
            document_id=params.document_id,
            object_name=result.get("object_name", params.object_name),
            body_name=result.get("body_name", params.body_name),
            mirrored_name=result.get("mirrored_name", params.mirrored_name),
            base_feature=result.get("base_feature", params.object_name),
            plane=result.get("plane", params.plane),
            mirror_face_index=result.get("mirror_face_index", 1),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # partdesign_thickness
    # ------------------------------------------------------------------
    @server.tool(
        name="partdesign_thickness",
        title="Create a PartDesign Thickness on a solid feature",
        description=(
            "Create a native PartDesign::Thickness that hollows an existing "
            "solid feature into a shell of uniform wall thickness inside a "
            "PartDesign::Body.  The target feature must already belong to a "
            "Body, or the document must contain exactly one (unambiguous) "
            "Body.  *thickness* must be finite and positive (millimetres); "
            "*reversed* controls the offset direction.  FreeCAD 1.1.4 exposes "
            "only the Mode='Skin' enumeration for Thickness direction and does "
            "not expose a settable property for selecting faces to remove, so "
            "face removal is not supported; the resulting solid is validated "
            "as non-null with at least one solid.  The document is "
            "recomputed, saved atomically, and its revision is bumped."
        ),
        structured_output=True,
    )
    def partdesign_thickness_tool(params: PartDesignThicknessParams) -> PartDesignThicknessResult:
        """Create a PartDesign Thickness on a solid feature and return its info."""
        validate_direct_request("partdesign_thickness", params.model_dump())
        if math.isnan(params.thickness) or math.isinf(params.thickness):
            raise ValueError("thickness must be a finite number.")
        if params.thickness <= 0:
            raise ValueError("thickness must be positive")

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "thickness": params.thickness,
            "reversed": params.reversed,
            "thickness_name": params.thickness_name,
            "body_name": params.body_name,
            "faces": params.faces,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "partdesign_thickness", bridge_params)

        return PartDesignThicknessResult(
            document_id=params.document_id,
            object_name=result.get("object_name", params.object_name),
            body_name=result.get("body_name", params.body_name),
            thickness_name=result.get("thickness_name", params.thickness_name),
            base_feature=result.get("base_feature", params.object_name),
            thickness_value=result.get("thickness_value", params.thickness),
            direction=result.get("direction", "outward" if params.reversed else "inward"),
            mode=result.get("mode", "Skin"),
            base=result.get("base"),
            removed_faces=result.get("removed_faces", params.faces),
            base_volume=result.get("base_volume"),
            volume_reduction=result.get("volume_reduction"),
            volume=result.get("volume"),
            bounding_box=result.get("bounding_box"),
            solids_count=result.get("solids_count", 0),
            meta_before=result.get("meta_before", {}),
        )

    # ------------------------------------------------------------------
    # tank
    # ------------------------------------------------------------------
    @server.tool(
        name="tank",
        title="Create tank",
        description=(
            "Add a rectangular Part::Box (tank) to an existing document. "
            "Parameters *length*, *width*, *height* must be positive numbers "
            "(millimetres). The document is locked exclusively during the "
            "operation."
        ),
        structured_output=True,
    )
    def tank_tool(params: TankParams) -> ObjectInfo:
        """Create a tank and return its info."""
        # Manual positive check
        validate_direct_request("tank", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "length": params.length,
            "width": params.width,
            "height": params.height,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "tank", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", "Part::Box"),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            length=result.get("length"),
            width=result.get("width"),
            height=result.get("height"),
        )
        return info

    # ------------------------------------------------------------------
    # tubular_coil
    # ------------------------------------------------------------------
    @server.tool(
        name="tubular_coil",
        title="Create tubular coil",
        description=(
            "Add a tubular helix coil to an existing document using a "
            "Part::Helix and pipe. Parameters *coil_radius*, *tube_radius*, "
            "*num_turns*, *pitch* must be positive numbers (millimetres). "
            "The document is locked exclusively during the operation."
        ),
        structured_output=True,
    )
    def tubular_coil_tool(params: TubularCoilParams) -> ObjectInfo:
        """Create a tubular coil and return its info."""
        validate_direct_request("tubular_coil", params.model_dump())

        bridge = _make_bridge(settings)
        bridge_params: dict[str, Any] = {
            "document_id": params.document_id,
            "object_name": params.object_name,
            "coil_radius": params.coil_radius,
            "tube_radius": params.tube_radius,
            "num_turns": params.num_turns,
            "pitch": params.pitch,
            "workspace": params.workspace or "",
        }
        result = _run_bridge_op(bridge, "tubular_coil", bridge_params)

        info = ObjectInfo(
            name=params.object_name,
            type=result.get("type", "Part::Feature"),
            shape_type=result.get("shape_type"),
            volume=result.get("volume"),
            radius=result.get("radius"),
        )
        return info


# ---------------------------------------------------------------------------
# Public registration
# ---------------------------------------------------------------------------


def register_all(server: MCPServer, settings: Settings) -> None:
    """Register every modelling tool on *server*.

    This mirrors the pattern from the outer ``tools/__init__.py``.
    """
    _register_tools(server, settings)
