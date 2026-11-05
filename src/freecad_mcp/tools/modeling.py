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
