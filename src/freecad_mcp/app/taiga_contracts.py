"""Taiga-agnostic CAD state, action, and parameter contracts (M20 Phase 1B).

This module is deliberately **FreeCAD-free** and **Taiga-free**: it must not
import ``FreeCAD``, ``torch`` or ``freecad_s1``. It defines the versioned,
explicit contracts that a future live-state adapter and execution boundary must
satisfy before any Taiga-S1 action can be scored or executed.

Three concerns are covered:

1. A versioned CAD state contract (:class:`CADState`) covering the fields the
   pinned Taiga ``State`` schema consumes, with explicit handling for
   unavailable/unsupported fields. Missing required fields are reported, never
   fabricated (:func:`CADState.missing_required_fields`,
   :meth:`CADState.to_taiga_state_json`).
2. A fail-closed action registry (:data:`ACTION_REGISTRY`) recording the
   verified Taiga action ids and their allowlisted MCP operation names, split
   into direct / conditional / partial / unsupported mappings.
3. A parameter-translation contract (:func:`translate_parameters`) that
   validates required inputs and reference requirements and returns a
   structured failure for missing or ambiguous parameters. It never applies
   Taiga's permissive numeric defaults and never executes an operation.

The existing M19 ``ModelStateSnapshot`` is a bounded *summary* (object
name/type/label/shape_type/volume/valid plus counts). It does **not** carry the
per-node numeric attributes, sketch geometry/constraint counts, selection
references, or shape ``face_dirs`` that Taiga's ``State`` and
``enumerate_actions`` require, so it is **not** sufficient as a Taiga state;
see :data:`M19_SNAPSHOT_LIMITATIONS`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "ACTION_REGISTRY",
    "CAD_STATE_SCHEMA_VERSION",
    "CADNode",
    "CADSelectionItem",
    "CADShapeDiagnostics",
    "CADState",
    "CADStateConversionError",
    "CONSTRAINT_KINDS",
    "GEOMETRY_KINDS",
    "GOAL_KINDS",
    "M19_SNAPSHOT_LIMITATIONS",
    "MappingKind",
    "NODE_TYPES",
    "NUMERIC_PARAMS",
    "ParameterIssue",
    "ParameterTranslationError",
    "ParameterTranslationResult",
    "ReferenceContext",
    "ReferenceRequirement",
    "SELECTION_KINDS",
    "SUPPORTED_CAD_STATE_SCHEMA_VERSIONS",
    "TERMINAL_ACTIONS",
    "TaigaContractError",
    "UnknownActionError",
    "VERIFIED_ACTION_IDS",
    "WORKBENCHES",
    "get_action_mapping",
    "is_executable",
    "is_terminal",
    "translate_parameters",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TaigaContractError(Exception):
    """Base class for structured contract failures."""


class UnknownActionError(TaigaContractError):
    """Raised when an action id is not present in the verified registry."""

    def __init__(self, action_id: str) -> None:
        super().__init__(f"Unknown Taiga action id: {action_id!r}")
        self.action_id = action_id


class CADStateConversionError(TaigaContractError):
    """Raised when a CAD state cannot be converted without fabricating data."""

    def __init__(self, missing_fields: list[str]) -> None:
        super().__init__(
            "CAD state is missing required field(s): " + ", ".join(missing_fields)
        )
        self.missing_fields = list(missing_fields)


class ParameterTranslationError(TaigaContractError):
    """Raised by :func:`translate_parameters` when translation fails."""

    def __init__(self, issues: list["ParameterIssue"]) -> None:
        super().__init__(
            "Parameter translation failed: "
            + "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        )
        self.issues = list(issues)


# ---------------------------------------------------------------------------
# Fixed vocabularies (verified from the pinned upstream schema/actions modules)
# ---------------------------------------------------------------------------

#: Taiga ``schema.WORKBENCHES`` (index 0 is the unknown/padding bucket).
WORKBENCHES: tuple[str, ...] = (
    "<unk>",
    "NoneWorkbench",
    "StartWorkbench",
    "PartWorkbench",
    "PartDesignWorkbench",
    "SketcherWorkbench",
)

#: Taiga ``schema.NODE_TYPES``.
NODE_TYPES: tuple[str, ...] = (
    "<unk>",
    "PartDesign::Body",
    "Sketcher::SketchObject",
    "PartDesign::Pad",
    "PartDesign::Pocket",
    "PartDesign::Revolution",
    "PartDesign::Groove",
    "PartDesign::Hole",
    "PartDesign::Fillet",
    "PartDesign::Chamfer",
    "PartDesign::Draft",
    "PartDesign::Thickness",
    "PartDesign::Mirrored",
    "PartDesign::LinearPattern",
    "PartDesign::PolarPattern",
    "Part::Box",
    "Part::Cylinder",
)

#: Taiga ``schema.GEOMETRY_KINDS``.
GEOMETRY_KINDS: tuple[str, ...] = ("line", "circle", "arc", "point", "other")

#: Taiga ``schema.CONSTRAINT_KINDS``.
CONSTRAINT_KINDS: tuple[str, ...] = (
    "Coincident",
    "Horizontal",
    "Vertical",
    "DistanceX",
    "DistanceY",
    "Distance",
    "Radius",
    "Diameter",
    "Equal",
    "PointOnObject",
    "Symmetric",
    "Lock",
    "Other",
)

#: Taiga ``schema.SELECTION_KINDS``.
SELECTION_KINDS: tuple[str, ...] = (
    "<none>",
    "plane",
    "face",
    "edges",
    "feature",
    "sketch",
    "other",
)

#: Taiga ``schema.GOAL_KINDS``.
GOAL_KINDS: tuple[str, ...] = (
    "<unk>",
    "base_box",
    "base_cyl",
    "base_hex",
    "base_ring",
    "boss_cyl",
    "boss_box",
    "hole",
    "hole_std",
    "pocket_rect",
    "polar_pattern",
    "linear_pattern",
    "mirror",
    "fillet_top",
    "fillet_vertical",
    "chamfer_top",
    "shell",
)

#: Terminal action id(s): they stop the loop and are never executed.
TERMINAL_ACTIONS: frozenset[str] = frozenset({"Done"})

#: Documented reasons the M19 ``ModelStateSnapshot`` is insufficient as a Taiga
#: state. Kept as data so callers/tests can assert the gap explicitly.
M19_SNAPSHOT_LIMITATIONS: tuple[str, ...] = (
    "no workbench identifier",
    "no per-node numeric attributes (tip/valid/n_geo/n_constraints/dof/...)",
    "no sketch geometry or constraint kind counts",
    "no tree parent/depth relationships (only a root count)",
    "no selection references (kind/object/subs/normal/offset)",
    "no shape face_dirs/area/n_faces/n_edges/n_solids",
    "no recent-action history",
)


# ---------------------------------------------------------------------------
# 1. Versioned CAD state contract
# ---------------------------------------------------------------------------

CAD_STATE_SCHEMA_VERSION = 1
SUPPORTED_CAD_STATE_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})


class CADNode(BaseModel):
    """One object in the feature tree, in document (= build) order.

    Mirrors Taiga ``schema.Node``. ``num`` carries the numeric per-node
    attributes; ``geo``/``cons`` carry sketch geometry/constraint kind counts.
    Availability flags mark sections that could not be captured; when a section
    is unavailable its mapping is empty and the flag is ``False`` rather than
    being filled with fabricated zeros.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Stable internal object name.")
    type: str = Field(description="FreeCAD TypeId (a NODE_TYPES entry when known).")
    parent: int = Field(
        default=-1, description="Index of the owning node (the Body); -1 for top level."
    )
    depth: int = Field(default=0, description="Nesting depth in the ownership tree.")
    num: dict[str, float] = Field(
        default_factory=dict, description="Numeric per-node attributes."
    )
    geo: dict[str, int] = Field(
        default_factory=dict, description="Sketch geometry kind counts."
    )
    cons: dict[str, int] = Field(
        default_factory=dict, description="Sketch constraint kind counts."
    )
    num_available: bool = Field(default=True, description="Whether ``num`` was captured.")
    geo_available: bool = Field(default=True, description="Whether ``geo`` was captured.")
    cons_available: bool = Field(
        default=True, description="Whether ``cons`` was captured."
    )


class CADSelectionItem(BaseModel):
    """One selected object/subelement reference.

    Mirrors Taiga ``schema.SelItem``. ``normal`` is required by Taiga's
    ``State.from_json`` for every selection item, so a selection item without a
    normal cannot be converted and is reported rather than defaulted.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="One of SELECTION_KINDS.")
    object: str = Field(description="Selected object name.")
    object_type: str = Field(description="Selected object TypeId.")
    subs: list[str] = Field(
        default_factory=list, description="Selected subelement names (e.g. Face1)."
    )
    normal: Optional[tuple[float, float, float]] = Field(
        default=None, description="Face/plane normal; required for conversion."
    )
    offset: Optional[float] = Field(
        default=None, description="Signed distance of the face/plane from origin."
    )
    count: Optional[int] = Field(
        default=None, description="Number of sub-elements in the selection."
    )


class CADShapeDiagnostics(BaseModel):
    """Shape-level diagnostics mirroring Taiga ``schema.ShapeInfo``.

    ``available`` is ``False`` when no shape could be inspected; individual
    fields stay ``None`` (unknown) rather than being fabricated.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = Field(default=False, description="Whether shape data was captured.")
    valid: Optional[bool] = Field(default=None, description="Whether the shape is valid.")
    volume: Optional[float] = Field(default=None, description="Volume in mm^3.")
    area: Optional[float] = Field(default=None, description="Surface area in mm^2.")
    bbox: Optional[tuple[float, float, float]] = Field(
        default=None, description="Bounding box X/Y/Z extents; required for conversion."
    )
    n_faces: Optional[int] = Field(default=None, description="Number of faces.")
    n_edges: Optional[int] = Field(default=None, description="Number of edges.")
    n_solids: Optional[int] = Field(default=None, description="Number of solids.")
    face_dirs: Optional[list[str]] = Field(
        default=None,
        description="Face normal directions present, e.g. ['+Z', '-Z', '+X'].",
    )


class CADState(BaseModel):
    """Versioned, FreeCAD-free CAD state contract.

    Covers the fields the pinned Taiga ``State`` schema consumes. Fields that a
    capture path cannot provide stay ``None`` (or empty with an availability
    flag) and are listed in :attr:`unavailable_fields`; they are never
    fabricated. :meth:`missing_required_fields` and :meth:`to_taiga_state_json`
    make the required-field contract explicit.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(
        default=CAD_STATE_SCHEMA_VERSION,
        description="Contract version. See SUPPORTED_CAD_STATE_SCHEMA_VERSIONS.",
    )
    source: str = Field(
        description="How this state was obtained (e.g. 'live_capture', 'm19_snapshot')."
    )
    doc_open: Optional[bool] = Field(
        default=None, description="Whether a document is open; required for conversion."
    )
    workbench: Optional[str] = Field(
        default=None,
        description="Active workbench (a WORKBENCHES entry when known); required for conversion.",
    )
    edit: Optional[str] = Field(
        default=None, description="Name of the object currently in edit mode, if any."
    )
    has_body: Optional[bool] = Field(
        default=None, description="Whether a PartDesign Body exists."
    )
    undo_available: Optional[bool] = Field(
        default=None, description="Whether Undo is available."
    )
    tree: list[CADNode] = Field(
        default_factory=list, description="Feature tree in document order."
    )
    selection: list[CADSelectionItem] = Field(
        default_factory=list, description="Current selection references."
    )
    recent: list[str] = Field(
        default_factory=list, description="Recent action ids (most recent last)."
    )
    events: list[str] = Field(
        default_factory=list, description="Recent observer events (debug/telemetry)."
    )
    shape: CADShapeDiagnostics = Field(
        default_factory=CADShapeDiagnostics, description="Shape-level diagnostics."
    )
    recent_flags: list[float] = Field(
        default_factory=list, description="Per-recent-action OK/field-match flags."
    )
    unavailable_fields: list[str] = Field(
        default_factory=list,
        description="Dotted field paths that could not be captured (never fabricated).",
    )
    collected_at: Optional[str] = Field(
        default=None, description="UTC ISO-8601 timestamp when the state was assembled."
    )

    @field_validator("schema_version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        if value not in SUPPORTED_CAD_STATE_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported CAD state schema_version {value!r}; "
                f"supported: {sorted(SUPPORTED_CAD_STATE_SCHEMA_VERSIONS)}"
            )
        return value

    @staticmethod
    def required_taiga_fields() -> tuple[str, ...]:
        """Field paths Taiga needs to featurize a state without fabricating data.

        ``State.from_json`` itself only requires ``doc_open``, ``workbench`` and
        ``shape.bbox``; the featurizer (``encode_state``) additionally reads the
        other shape metrics unconditionally (``float(sh.valid)``,
        ``sh.volume / tgt_vol``, ``d in sh.face_dirs``, ...) and raises
        ``TypeError`` on ``None``. They are therefore required for conversion
        too. ``shape.area`` is never read by the featurizer and stays optional.
        """
        return (
            "doc_open",
            "workbench",
            "shape.bbox",
            "shape.valid",
            "shape.volume",
            "shape.n_faces",
            "shape.n_edges",
            "shape.n_solids",
            "shape.face_dirs",
        )

    def missing_required_fields(self) -> list[str]:
        """Return required field paths that are absent (``None``)."""
        missing: list[str] = []
        if self.doc_open is None:
            missing.append("doc_open")
        if self.workbench is None:
            missing.append("workbench")
        if self.shape.bbox is None:
            missing.append("shape.bbox")
        if self.shape.valid is None:
            missing.append("shape.valid")
        if self.shape.volume is None:
            missing.append("shape.volume")
        if self.shape.n_faces is None:
            missing.append("shape.n_faces")
        if self.shape.n_edges is None:
            missing.append("shape.n_edges")
        if self.shape.n_solids is None:
            missing.append("shape.n_solids")
        if self.shape.face_dirs is None:
            missing.append("shape.face_dirs")
        if any(item.normal is None for item in self.selection):
            missing.append("selection[].normal")
        return missing

    def to_taiga_state_json(self) -> dict[str, Any]:
        """Render a JSON object accepted by Taiga ``State.from_json``.

        Raises:
            CADStateConversionError: if a required field is missing. Values are
                never fabricated; optional numeric fields that are unknown are
                omitted so the schema default applies.
        """
        missing = self.missing_required_fields()
        if missing:
            raise CADStateConversionError(missing)

        assert self.doc_open is not None
        assert self.workbench is not None
        assert self.shape.bbox is not None

        shape_payload: dict[str, Any] = {
            "valid": self.shape.valid,
            "volume": self.shape.volume,
            "area": self.shape.area,
            "bbox": list(self.shape.bbox),
            "n_faces": self.shape.n_faces,
            "n_edges": self.shape.n_edges,
            "n_solids": self.shape.n_solids,
            "face_dirs": self.shape.face_dirs,
        }

        return {
            "doc_open": self.doc_open,
            "workbench": self.workbench,
            "edit": self.edit,
            "has_body": bool(self.has_body) if self.has_body is not None else False,
            "undo_available": (
                bool(self.undo_available) if self.undo_available is not None else False
            ),
            "tree": [
                {
                    "name": node.name,
                    "type": node.type,
                    "parent": node.parent,
                    "depth": node.depth,
                    "num": dict(node.num),
                    "geo": dict(node.geo),
                    "cons": dict(node.cons),
                }
                for node in self.tree
            ],
            "selection": [
                {
                    "kind": item.kind,
                    "object": item.object,
                    "object_type": item.object_type,
                    "subs": list(item.subs),
                    "normal": list(item.normal) if item.normal is not None else None,
                    **(
                        {"offset": item.offset}
                        if item.offset is not None
                        else {}
                    ),
                    **({"count": item.count} if item.count is not None else {}),
                }
                for item in self.selection
            ],
            "recent": list(self.recent),
            "events": list(self.events),
            "shape": shape_payload,
            "ui": None,
            "recent_flags": list(self.recent_flags),
        }


# ---------------------------------------------------------------------------
# 2. Fail-closed action registry
# ---------------------------------------------------------------------------


class MappingKind(str, Enum):
    """How a Taiga action id maps onto an allowlisted MCP operation."""

    DIRECT = "direct"
    CONDITIONAL = "conditional"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class ReferenceRequirement(str, Enum):
    """A live-state reference a mapping needs before it can be executed."""

    DOCUMENT = "document"
    BODY = "body"
    PROFILE_SKETCH = "profile_sketch"
    SOLID_FEATURE = "solid_feature"
    SELECTED_EDGES = "selected_edges"
    BASE_FACE = "base_face"


@dataclass(frozen=True)
class ActionMapping:
    """Verified mapping from one Taiga action id to an MCP operation.

    ``operation`` is an allowlisted FreeCAD bridge operation name (never an
    unrelated primitive). ``required_params`` lists parameters that must be
    supplied explicitly (or resolved from references via ``param_sources``).
    ``reference_requirements`` documents the live-state references the mapping
    needs; ``param_sources`` says which required params may be resolved from a
    reference. ``injected_params`` carries constant parameters implied by the
    action id itself (e.g. ``Sketcher_CreateHexagon`` -> ``sides=6``), never
    Taiga's permissive numeric defaults.
    """

    action_id: str
    mapping_kind: MappingKind
    operation: Optional[str]
    required_params: tuple[str, ...] = ()
    reference_requirements: tuple[ReferenceRequirement, ...] = ()
    param_sources: dict[str, ReferenceRequirement] = field(default_factory=dict)
    injected_params: dict[str, Any] = field(default_factory=dict)
    constraint_type: Optional[str] = None
    notes: str = ""

    @property
    def executable(self) -> bool:
        """True only for a concrete, non-terminal, allowlisted operation."""
        return (
            self.mapping_kind in (MappingKind.DIRECT, MappingKind.CONDITIONAL, MappingKind.PARTIAL)
            and self.operation is not None
        )


def _direct(
    action_id: str,
    operation: str,
    *,
    required: tuple[str, ...] = (),
    injected: Optional[dict[str, Any]] = None,
    constraint_type: Optional[str] = None,
    notes: str = "",
) -> ActionMapping:
    return ActionMapping(
        action_id=action_id,
        mapping_kind=MappingKind.DIRECT,
        operation=operation,
        required_params=required,
        injected_params=dict(injected or {}),
        constraint_type=constraint_type,
        notes=notes,
    )


def _conditional(
    action_id: str,
    operation: str,
    *,
    required: tuple[str, ...],
    refs: tuple[ReferenceRequirement, ...],
    sources: Optional[dict[str, ReferenceRequirement]] = None,
    notes: str = "",
) -> ActionMapping:
    return ActionMapping(
        action_id=action_id,
        mapping_kind=MappingKind.CONDITIONAL,
        operation=operation,
        required_params=required,
        reference_requirements=refs,
        param_sources=dict(sources or {}),
        notes=notes,
    )


def _partial(
    action_id: str,
    operation: str,
    *,
    required: tuple[str, ...],
    refs: tuple[ReferenceRequirement, ...] = (),
    sources: Optional[dict[str, ReferenceRequirement]] = None,
    injected: Optional[dict[str, Any]] = None,
    notes: str = "",
) -> ActionMapping:
    return ActionMapping(
        action_id=action_id,
        mapping_kind=MappingKind.PARTIAL,
        operation=operation,
        required_params=required,
        reference_requirements=refs,
        param_sources=dict(sources or {}),
        injected_params=dict(injected or {}),
        notes=notes,
    )


def _unsupported(action_id: str, notes: str) -> ActionMapping:
    return ActionMapping(
        action_id=action_id,
        mapping_kind=MappingKind.UNSUPPORTED,
        operation=None,
        notes=notes,
    )


_PROFILE_REFS = (ReferenceRequirement.PROFILE_SKETCH, ReferenceRequirement.BODY)
_SOLID_REFS = (ReferenceRequirement.SOLID_FEATURE, ReferenceRequirement.BODY)

#: Verified Taiga action id -> allowlisted MCP operation mapping.
#:
#: Every action id in the pinned ``actions.CATALOGUE`` is present (mapped or
#: explicitly unsupported). Operation names are allowlisted FreeCAD bridge
#: operations; a test cross-checks them against ``runner.OPERATIONS``. No entry
#: maps an action to an unrelated operation.
ACTION_REGISTRY: dict[str, ActionMapping] = {
    # --- std / document ---
    "Std_New": _direct("Std_New", "create_document", required=("document_id",)),
    # --- workbench / view / undo: no MCP equivalent ---
    "Std_Workbench:PartDesignWorkbench": _unsupported(
        "Std_Workbench:PartDesignWorkbench", "No MCP workbench-switch operation."
    ),
    "Std_Workbench:PartWorkbench": _unsupported(
        "Std_Workbench:PartWorkbench", "No MCP workbench-switch operation."
    ),
    "Std_Workbench:SketcherWorkbench": _unsupported(
        "Std_Workbench:SketcherWorkbench", "No MCP workbench-switch operation."
    ),
    "Std_Undo": _unsupported("Std_Undo", "No MCP undo operation."),
    "Std_ViewFitAll": _unsupported("Std_ViewFitAll", "No MCP view operation."),
    # --- terminal (never executed) ---
    "Done": _unsupported("Done", "Terminal action: ends the loop; never executed."),
    # --- selection pseudo-commands: no general selection operation ---
    "Select:Plane:XY": _unsupported("Select:Plane:XY", "No general selection operation."),
    "Select:Plane:XZ": _unsupported("Select:Plane:XZ", "No general selection operation."),
    "Select:Plane:YZ": _unsupported("Select:Plane:YZ", "No general selection operation."),
    "Select:Face+Z": _unsupported("Select:Face+Z", "No general selection operation."),
    "Select:Face-Z": _unsupported("Select:Face-Z", "No general selection operation."),
    "Select:Face+X": _unsupported("Select:Face+X", "No general selection operation."),
    "Select:Face-X": _unsupported("Select:Face-X", "No general selection operation."),
    "Select:Face+Y": _unsupported("Select:Face+Y", "No general selection operation."),
    "Select:Face-Y": _unsupported("Select:Face-Y", "No general selection operation."),
    "Select:Edges@Face+Z": _unsupported(
        "Select:Edges@Face+Z", "No general selection operation."
    ),
    "Select:Edges|Z": _unsupported("Select:Edges|Z", "No general selection operation."),
    "Select:Tip": _unsupported("Select:Tip", "No general selection operation."),
    "Select:Clear": _unsupported("Select:Clear", "No general selection operation."),
    # --- PartDesign ---
    "PartDesign_Body": _direct(
        "PartDesign_Body", "create_body", required=("document_id",)
    ),
    "PartDesign_NewSketch": _direct(
        "PartDesign_NewSketch", "create_sketch", required=("document_id",)
    ),
    "PartDesign_Pad": _conditional(
        "PartDesign_Pad",
        "sketch_pad",
        required=("document_id", "sketch_name", "length"),
        refs=_PROFILE_REFS,
        sources={"sketch_name": ReferenceRequirement.PROFILE_SKETCH},
    ),
    "PartDesign_Revolution": _conditional(
        "PartDesign_Revolution",
        "sketch_revolution",
        required=("document_id", "sketch_name"),
        refs=_PROFILE_REFS,
        sources={"sketch_name": ReferenceRequirement.PROFILE_SKETCH},
        notes="axis/angle use MCP defaults unless supplied.",
    ),
    "PartDesign_Pocket": _conditional(
        "PartDesign_Pocket",
        "sketch_pocket",
        required=("document_id", "sketch_name", "length"),
        refs=_PROFILE_REFS,
        sources={"sketch_name": ReferenceRequirement.PROFILE_SKETCH},
    ),
    "PartDesign_Groove": _conditional(
        "PartDesign_Groove",
        "sketch_groove",
        required=("document_id", "sketch_name"),
        refs=_PROFILE_REFS,
        sources={"sketch_name": ReferenceRequirement.PROFILE_SKETCH},
        notes="axis/angle use MCP defaults unless supplied.",
    ),
    "PartDesign_Hole": _conditional(
        "PartDesign_Hole",
        "sketch_hole",
        required=("document_id", "sketch_name", "base_object_name", "base_subname", "diameter", "depth"),
        refs=(
            ReferenceRequirement.PROFILE_SKETCH,
            ReferenceRequirement.SOLID_FEATURE,
            ReferenceRequirement.BASE_FACE,
            ReferenceRequirement.BODY,
        ),
        sources={
            "sketch_name": ReferenceRequirement.PROFILE_SKETCH,
            "base_object_name": ReferenceRequirement.SOLID_FEATURE,
            "base_subname": ReferenceRequirement.BASE_FACE,
        },
    ),
    "PartDesign_Fillet": _conditional(
        "PartDesign_Fillet",
        "partdesign_fillet",
        required=("document_id", "object_name", "edge_indices", "radius"),
        refs=(
            ReferenceRequirement.SOLID_FEATURE,
            ReferenceRequirement.SELECTED_EDGES,
            ReferenceRequirement.BODY,
        ),
        sources={
            "object_name": ReferenceRequirement.SOLID_FEATURE,
            "edge_indices": ReferenceRequirement.SELECTED_EDGES,
        },
    ),
    "PartDesign_Chamfer": _conditional(
        "PartDesign_Chamfer",
        "partdesign_chamfer",
        required=("document_id", "object_name", "edge_indices", "size"),
        refs=(
            ReferenceRequirement.SOLID_FEATURE,
            ReferenceRequirement.SELECTED_EDGES,
            ReferenceRequirement.BODY,
        ),
        sources={
            "object_name": ReferenceRequirement.SOLID_FEATURE,
            "edge_indices": ReferenceRequirement.SELECTED_EDGES,
        },
    ),
    "PartDesign_Draft": _unsupported(
        "PartDesign_Draft", "No MCP draft operation."
    ),
    "PartDesign_Thickness": _conditional(
        "PartDesign_Thickness",
        "partdesign_thickness",
        required=("document_id", "object_name", "thickness"),
        refs=_SOLID_REFS,
        sources={"object_name": ReferenceRequirement.SOLID_FEATURE},
        notes=(
            "faces is not required; MCP's default faces=[] is the explicit "
            "all-faces (hollow) behavior, not a missing value."
        ),
    ),
    "PartDesign_Mirrored": _conditional(
        "PartDesign_Mirrored",
        "partdesign_mirrored",
        required=("document_id", "object_name", "plane"),
        refs=_SOLID_REFS,
        sources={"object_name": ReferenceRequirement.SOLID_FEATURE},
    ),
    "PartDesign_LinearPattern": _conditional(
        "PartDesign_LinearPattern",
        "partdesign_linear_pattern",
        required=("document_id", "object_name", "direction", "length", "occurrences"),
        refs=_SOLID_REFS,
        sources={"object_name": ReferenceRequirement.SOLID_FEATURE},
    ),
    "PartDesign_PolarPattern": _conditional(
        "PartDesign_PolarPattern",
        "partdesign_polar_pattern",
        required=("document_id", "object_name", "axis", "angle", "occurrences"),
        refs=_SOLID_REFS,
        sources={"object_name": ReferenceRequirement.SOLID_FEATURE},
    ),
    # --- Part primitives ---
    "Part_Box": _direct(
        "Part_Box",
        "create_box",
        required=("document_id", "length", "width", "height"),
        notes="Dimensions are required explicitly; MCP defaults are not relied upon.",
    ),
    "Part_Cylinder": _direct(
        "Part_Cylinder",
        "create_cylinder",
        required=("document_id", "radius", "height"),
        notes="Dimensions are required explicitly; MCP defaults are not relied upon.",
    ),
    # --- Sketcher geometry ---
    "Sketcher_CreateRectangle": _direct(
        "Sketcher_CreateRectangle",
        "sketch_add_rectangle",
        required=("document_id", "sketch_name", "width", "height"),
    ),
    "Sketcher_CreateCircle": _direct(
        "Sketcher_CreateCircle",
        "sketch_add_circle",
        required=("document_id", "sketch_name", "radius"),
    ),
    "Sketcher_CreateHexagon": _partial(
        "Sketcher_CreateHexagon",
        "sketch_add_regular_polygon",
        required=("document_id", "sketch_name", "radius"),
        injected={"sides": 6},
        notes="Hexagon => regular polygon with sides=6 injected; covers a subset of the polygon action.",
    ),
    "Sketcher_CreateLine": _unsupported(
        "Sketcher_CreateLine", "No MCP single-line creation operation."
    ),
    "Sketcher_CreatePoint": _unsupported(
        "Sketcher_CreatePoint", "No MCP point creation operation."
    ),
    # --- Sketcher constraints ---
    "Sketcher_ConstrainHorizontal": _direct(
        "Sketcher_ConstrainHorizontal",
        "sketch_add_constraint",
        required=("document_id", "sketch_name", "first_geometry_index"),
        injected={"constraint_type": "Horizontal"},
        constraint_type="Horizontal",
    ),
    "Sketcher_ConstrainVertical": _direct(
        "Sketcher_ConstrainVertical",
        "sketch_add_constraint",
        required=("document_id", "sketch_name", "first_geometry_index"),
        injected={"constraint_type": "Vertical"},
        constraint_type="Vertical",
    ),
    "Sketcher_ConstrainDistance": _direct(
        "Sketcher_ConstrainDistance",
        "sketch_add_constraint",
        required=("document_id", "sketch_name", "first_geometry_index", "value"),
        injected={"constraint_type": "Distance"},
        constraint_type="Distance",
        notes="A second_geometry_index may be required for two-geometry distance.",
    ),
    "Sketcher_ConstrainRadius": _direct(
        "Sketcher_ConstrainRadius",
        "sketch_add_constraint",
        required=("document_id", "sketch_name", "first_geometry_index", "value"),
        injected={"constraint_type": "Radius"},
        constraint_type="Radius",
    ),
    "Sketcher_ConstrainDiameter": _direct(
        "Sketcher_ConstrainDiameter",
        "sketch_add_constraint",
        required=("document_id", "sketch_name", "first_geometry_index", "value"),
        injected={"constraint_type": "Diameter"},
        constraint_type="Diameter",
    ),
    "Sketcher_ConstrainDistanceX": _unsupported(
        "Sketcher_ConstrainDistanceX",
        "MCP sketch_add_constraint does not support DistanceX.",
    ),
    "Sketcher_ConstrainDistanceY": _unsupported(
        "Sketcher_ConstrainDistanceY",
        "MCP sketch_add_constraint does not support DistanceY.",
    ),
    "Sketcher_ConstrainLock": _unsupported(
        "Sketcher_ConstrainLock",
        "MCP sketch_add_constraint does not support Lock.",
    ),
    "Sketcher_ToggleConstruction": _unsupported(
        "Sketcher_ToggleConstruction", "No MCP toggle-construction operation."
    ),
    "Sketcher_LeaveSketch": _unsupported(
        "Sketcher_LeaveSketch", "No MCP leave-sketch operation (headless)."
    ),
}

#: All verified action ids known to the registry, in declaration order.
VERIFIED_ACTION_IDS: tuple[str, ...] = tuple(ACTION_REGISTRY.keys())


def get_action_mapping(action_id: str) -> ActionMapping:
    """Return the mapping for ``action_id``.

    Raises:
        UnknownActionError: if the action id is not in the verified registry.
    """
    try:
        return ACTION_REGISTRY[action_id]
    except KeyError:
        raise UnknownActionError(action_id) from None


def is_terminal(action_id: str) -> bool:
    """Whether ``action_id`` terminates the loop (never executed)."""
    return action_id in TERMINAL_ACTIONS


def is_executable(action_id: str) -> bool:
    """Whether ``action_id`` maps to a concrete allowlisted operation.

    Unknown and unsupported actions are never executable (fail closed).
    """
    mapping = ACTION_REGISTRY.get(action_id)
    return mapping is not None and mapping.executable


# ---------------------------------------------------------------------------
# 3. Parameter-translation contract
# ---------------------------------------------------------------------------

#: Parameter names that must be numeric when present. Used to reject booleans,
#: non-numbers, and non-finite values before execution.
NUMERIC_PARAMS: frozenset[str] = frozenset(
    {
        "length",
        "width",
        "height",
        "radius",
        "x",
        "y",
        "z",
        "depth",
        "angle",
        "size",
        "t",
        "thickness",
        "diameter",
        "value",
        "sides",
        "occurrences",
        "start_x",
        "start_y",
        "end_x",
        "end_y",
        "center_x",
        "center_y",
        "major_radius",
        "minor_radius",
        "rotation_deg",
    }
)

_DIMENSIONAL_CONSTRAINTS = frozenset({"Distance", "Radius", "Diameter", "Angle"})


class ReferenceContext(BaseModel):
    """Live-state references available to parameter translation.

    Singular fields (``sketch_name``, ``solid_name``, ``base_subname``) are
    resolved references. ``*_candidates`` allow the translator to detect
    ambiguity when a single reference is required but several are present.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: Optional[str] = None
    body_name: Optional[str] = None
    body_candidates: list[str] = Field(default_factory=list)
    sketch_name: Optional[str] = None
    sketch_candidates: list[str] = Field(default_factory=list)
    solid_name: Optional[str] = None
    solid_candidates: list[str] = Field(default_factory=list)
    base_object_name: Optional[str] = None
    base_subname: Optional[str] = None
    face_candidates: list[str] = Field(default_factory=list)
    edge_indices: Optional[list[int]] = None


class ParameterIssue(BaseModel):
    """One structured parameter-translation problem."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "unknown_action",
        "unsupported_action",
        "missing_parameter",
        "missing_reference",
        "ambiguous_reference",
        "invalid_parameter",
        "unsupported_constraint",
    ] = Field(description="Machine-readable failure category.")
    message: str = Field(description="Human-readable explanation.")
    parameter: Optional[str] = Field(
        default=None, description="The parameter or reference involved, if any."
    )


class ParameterTranslationResult(BaseModel):
    """Outcome of translating a selected action into MCP operation parameters."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="True only when translation produced parameters.")
    action_id: str = Field(description="The Taiga action id that was translated.")
    operation: Optional[str] = Field(
        default=None, description="Allowlisted MCP operation name, when resolved."
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Translated MCP operation parameters."
    )
    issues: list[ParameterIssue] = Field(
        default_factory=list, description="Structured problems when ok is False."
    )

    def raise_for_errors(self) -> "ParameterTranslationResult":
        """Raise :class:`ParameterTranslationError` if translation failed."""
        if not self.ok:
            raise ParameterTranslationError(self.issues)
        return self


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _resolve_single(
    explicit: Optional[str],
    candidates: list[str],
    label: str,
    issues: list[ParameterIssue],
) -> Optional[str]:
    """Resolve one reference, recording missing/ambiguous issues."""
    if explicit is not None and explicit.strip():
        return explicit
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        issues.append(
            ParameterIssue(
                code="ambiguous_reference",
                message=(
                    f"{label} is ambiguous: {len(candidates)} candidates "
                    f"({sorted(candidates)}) and none was selected."
                ),
                parameter=label,
            )
        )
        return None
    issues.append(
        ParameterIssue(
            code="missing_reference",
            message=f"Required {label} reference is missing.",
            parameter=label,
        )
    )
    return None


def _resolve_param_from_reference(
    requirement: ReferenceRequirement,
    references: ReferenceContext,
    issues: list[ParameterIssue],
) -> Any:
    """Resolve a required parameter value from a reference requirement."""
    if requirement is ReferenceRequirement.PROFILE_SKETCH:
        return _resolve_single(
            references.sketch_name, references.sketch_candidates, "profile_sketch", issues
        )
    if requirement is ReferenceRequirement.SOLID_FEATURE:
        return _resolve_single(
            references.solid_name, references.solid_candidates, "solid_feature", issues
        )
    if requirement is ReferenceRequirement.BASE_FACE:
        return _resolve_single(
            references.base_subname, references.face_candidates, "base_face", issues
        )
    if requirement is ReferenceRequirement.SELECTED_EDGES:
        if references.edge_indices:
            return list(references.edge_indices)
        issues.append(
            ParameterIssue(
                code="missing_reference",
                message="Required selected-edges reference is missing.",
                parameter="edge_indices",
            )
        )
        return None
    if requirement is ReferenceRequirement.DOCUMENT:
        if references.document_id:
            return references.document_id
        issues.append(
            ParameterIssue(
                code="missing_reference",
                message="Required document reference is missing.",
                parameter="document_id",
            )
        )
        return None
    if requirement is ReferenceRequirement.BODY:
        return _resolve_single(
            references.body_name, references.body_candidates, "body_name", issues
        )
    return references.body_name


def translate_parameters(
    action_id: str,
    parameters: Optional[dict[str, Any]] = None,
    references: Optional[ReferenceContext] = None,
) -> ParameterTranslationResult:
    """Translate a selected Taiga action into allowlisted MCP parameters.

    The function is fail-closed and pure: it never executes an operation, never
    imports Taiga, and never applies Taiga's permissive numeric defaults. A
    missing or ambiguous required parameter/reference yields a structured
    :class:`ParameterIssue`; the result's ``ok`` is ``False`` in that case.

    Args:
        action_id: A verified Taiga action id.
        parameters: Caller-supplied MCP parameters (numeric values from the goal
            stage). ``None`` values are treated as absent. The special key
            ``two_geometry`` (bool) selects the two-geometry form of a
            dimensional ``Distance`` constraint; it is consumed by the
            translator and never forwarded to MCP.
        references: Live-state references used to resolve profile/solid/face/
            edge parameters and to detect ambiguity.

    Returns:
        A :class:`ParameterTranslationResult`.
    """
    references = references or ReferenceContext()
    params: dict[str, Any] = {
        key: value for key, value in (parameters or {}).items() if value is not None
    }

    try:
        mapping = get_action_mapping(action_id)
    except UnknownActionError:
        return ParameterTranslationResult(
            ok=False,
            action_id=action_id,
            issues=[
                ParameterIssue(
                    code="unknown_action",
                    message=f"Unknown Taiga action id: {action_id!r}.",
                    parameter=action_id,
                )
            ],
        )

    if mapping.mapping_kind is MappingKind.UNSUPPORTED or mapping.operation is None:
        return ParameterTranslationResult(
            ok=False,
            action_id=action_id,
            issues=[
                ParameterIssue(
                    code="unsupported_action",
                    message=(
                        f"Action {action_id!r} has no allowlisted MCP operation. "
                        f"{mapping.notes}".strip()
                    ),
                    parameter=action_id,
                )
            ],
        )

    issues: list[ParameterIssue] = []
    # ``two_geometry`` is a translation directive for dimensional Distance
    # constraints, not an MCP parameter: it is consumed here and never emitted.
    two_geometry = bool(params.pop("two_geometry", False))
    resolved: dict[str, Any] = dict(params)

    for name in mapping.required_params:
        if name in resolved:
            value = resolved[name]
            if name in NUMERIC_PARAMS and not _is_number(value):
                issues.append(
                    ParameterIssue(
                        code="invalid_parameter",
                        message=f"Parameter {name!r} must be a finite number.",
                        parameter=name,
                    )
                )
            continue

        requirement = mapping.param_sources.get(name)
        if requirement is not None:
            value = _resolve_param_from_reference(requirement, references, issues)
            if value is not None:
                resolved[name] = value
            continue

        issues.append(
            ParameterIssue(
                code="missing_parameter",
                message=f"Required parameter {name!r} is missing.",
                parameter=name,
            )
        )

    # Body resolution: mappings that declare BODY must resolve an explicit or
    # unambiguous body_name. Never fall back to MCP's implicit "Body" default,
    # which can target the wrong Body in a multi-Body document.
    if ReferenceRequirement.BODY in mapping.reference_requirements:
        if not resolved.get("body_name"):
            body = _resolve_param_from_reference(
                ReferenceRequirement.BODY, references, issues
            )
            if body is not None:
                resolved["body_name"] = body

    # Dimensional constraints require an explicit numeric value.
    if mapping.constraint_type in _DIMENSIONAL_CONSTRAINTS and "value" not in resolved:
        issues.append(
            ParameterIssue(
                code="missing_parameter",
                message=(
                    f"Constraint {mapping.constraint_type!r} requires a numeric 'value'."
                ),
                parameter="value",
            )
        )

    # Sketcher Distance: one geometry = distance/length of that geometry (no
    # second_geometry_index); two geometries = distance between them (requires
    # second_geometry_index). The action id alone cannot distinguish intent, so
    # the caller declares a two-geometry distance with two_geometry=True; without
    # it the one-geometry form is used and second_geometry_index stays optional.
    if mapping.constraint_type == "Distance" and two_geometry:
        if "second_geometry_index" not in resolved:
            issues.append(
                ParameterIssue(
                    code="missing_parameter",
                    message=(
                        "A two-geometry Distance constraint requires "
                        "'second_geometry_index'."
                    ),
                    parameter="second_geometry_index",
                )
            )
    if "second_geometry_index" in resolved and not _is_number(
        resolved["second_geometry_index"]
    ):
        issues.append(
            ParameterIssue(
                code="invalid_parameter",
                message="Parameter 'second_geometry_index' must be a finite number.",
                parameter="second_geometry_index",
            )
        )

    # Constant parameters implied by the action id (never Taiga defaults).
    for name, value in mapping.injected_params.items():
        resolved.setdefault(name, value)

    if issues:
        return ParameterTranslationResult(
            ok=False, action_id=action_id, operation=mapping.operation, issues=issues
        )

    return ParameterTranslationResult(
        ok=True,
        action_id=action_id,
        operation=mapping.operation,
        parameters=resolved,
    )