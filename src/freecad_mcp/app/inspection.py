"""Document and model inspection contracts shared by interface adapters.

The contracts here are independent of MCP, Qt/GUI, LLMs, and Taiga.  They
describe what can be inspected about a FreeCAD document and its objects,
without exposing raw FreeCAD objects outside the FreeCAD execution boundary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from freecad_mcp.freecad_bridge import FreeCadBridge, FreeCadOperationError
from freecad_mcp.freecad_bridge import workspace as ws_mod

from .contracts import ApplicationError, BackendStatus


@dataclass(frozen=True)
class ShapeInfo:
    """Geometry and validity of a FreeCAD shape, when one exists.

    Every field is optional.  An object without a Shape attribute, a null
    Shape, or an unreadable Shape leaves the corresponding fields unset
    instead of fabricating values.
    """

    shape_type: str | None = None
    valid: bool | None = None
    volume: float | None = None
    area: float | None = None
    length: float | None = None
    width: float | None = None
    height: float | None = None
    solid_count: int | None = None
    face_count: int | None = None
    edge_count: int | None = None
    vertex_count: int | None = None


@dataclass(frozen=True)
class ObjectInspection:
    """Inspection of a single document object.

    ``has_shape`` is False when the object has no Shape attribute (for
    example sketches or origin objects).  ``shape_null`` is True when the
    object has a Shape attribute but it is None.  ``shape`` is populated
    only when a shape exists and could be read.
    """

    name: str
    type: str
    label: str | None = None
    has_shape: bool = False
    shape_null: bool = False
    shape: ShapeInfo | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentInspection:
    """Inspection of an entire document."""

    document_id: str
    document_name: str | None = None
    objects: tuple[ObjectInspection, ...] = ()
    object_count: int = 0
    all_valid: bool | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InspectionRequest:
    document_id: str
    workspace: str | None = None


@dataclass(frozen=True)
class InspectionResult:
    ok: bool
    document_id: str
    document: DocumentInspection | None = None
    error: ApplicationError | None = None


class InspectionBackend(Protocol):
    """Contract for document and model inspection backends."""

    def inspect(self, request: InspectionRequest) -> InspectionResult:
        """Inspect *request.document_id* and return structured results."""

    def status(self) -> BackendStatus:
        """Report the backend's current availability."""


def _build_shape_info(info: Mapping[str, Any]) -> ShapeInfo:
    """Build a :class:`ShapeInfo` from a FreeCAD inspect-object info mapping."""
    shape_type = info.get("shape_type")
    if shape_type is None:
        return ShapeInfo()

    valid = info.get("shape_valid")
    volume = info.get("volume")
    length = info.get("length")
    width = info.get("width")
    height = info.get("height")

    return ShapeInfo(
        shape_type=str(shape_type),
        valid=bool(valid) if valid is not None else None,
        volume=float(volume) if volume is not None else None,
        area=None,
        length=float(length) if length is not None else None,
        width=float(width) if width is not None else None,
        height=float(height) if height is not None else None,
        solid_count=None,
        face_count=None,
        edge_count=None,
        vertex_count=None,
    )


class DirectInspectionBackend:
    """Inspection delegated to an injected FreeCAD execution adapter."""

    def __init__(
        self,
        executor: Any,
        workspace: ws_mod.Workspace | None = None,
    ) -> None:
        self._executor = executor
        self._workspace = workspace

    def status(self) -> BackendStatus:
        return BackendStatus(
            available=None,
            capabilities=(
                "document_inspection",
                "object_inspection",
                "shape_inspection",
            ),
            detail="Executor configured; availability has not been checked.",
        )

    def inspect(self, request: InspectionRequest) -> InspectionResult:
        document_id = request.document_id
        workspace_str = request.workspace or ""

        try:
            normalized = ws_mod.validate_document_id(document_id)
        except ValueError as exc:
            return InspectionResult(
                ok=False,
                document_id=document_id,
                error=ApplicationError(str(exc), "ValidationError"),
            )

        wks = self._workspace or ws_mod.Workspace.from_env()
        if workspace_str:
            wks = ws_mod.Workspace(root=os.path.normpath(workspace_str))

        meta_before = ws_mod.read_meta(wks.meta_path(normalized))

        try:
            list_result = self._executor.run(
                "list_objects",
                {"document_id": normalized, "workspace": workspace_str},
            )
            if not isinstance(list_result, dict):
                raise FreeCadOperationError(
                    f"Unexpected bridge response format: {list_result!r}",
                    error_type="BridgeError",
                )
            objects_raw = list_result.get("objects", [])
            if not isinstance(objects_raw, list):
                objects_raw = []
        except FreeCadOperationError as exc:
            return InspectionResult(
                ok=False,
                document_id=normalized,
                error=ApplicationError(str(exc), exc.error_type),
            )

        objects: list[ObjectInspection] = []
        for obj_raw in objects_raw:
            if not isinstance(obj_raw, dict):
                continue
            obj_name = obj_raw.get("name", "")
            objects.append(
                self._inspect_single_object(
                    normalized, workspace_str, obj_name, obj_raw
                )
            )

        all_valid: bool | None = None
        if objects:
            all_valid = all(
                o.has_shape
                and not o.shape_null
                and o.shape is not None
                and o.shape.valid is True
                for o in objects
            )

        document = DocumentInspection(
            document_id=normalized,
            document_name=None,
            objects=tuple(objects),
            object_count=len(objects),
            all_valid=all_valid,
            meta=meta_before,
        )

        return InspectionResult(ok=True, document_id=normalized, document=document)

    def _inspect_single_object(
        self,
        document_id: str,
        workspace_str: str,
        object_name: str,
        fallback: Mapping[str, Any],
    ) -> ObjectInspection:
        try:
            inspect_result = self._executor.run(
                "inspect_object",
                {
                    "document_id": document_id,
                    "object_name": object_name,
                    "workspace": workspace_str,
                },
            )
            if not isinstance(inspect_result, dict):
                raise FreeCadOperationError(
                    f"Unexpected bridge response format: {inspect_result!r}",
                    error_type="BridgeError",
                )
            info = inspect_result.get("info", {})
            if not isinstance(info, dict):
                info = {}
        except FreeCadOperationError:
            info = {}

        shape_type = info.get("shape_type")
        has_shape = shape_type is not None
        shape_null = not has_shape

        shape = _build_shape_info(info) if has_shape else None

        properties: dict[str, Any] = {}
        for key in ("Length", "Width", "Height", "Radius", "Diameter", "material", "color"):
            if key in info:
                properties[key] = info[key]

        return ObjectInspection(
            name=str(info.get("name", fallback.get("name", object_name))),
            type=str(info.get("type", fallback.get("type", ""))),
            label=info.get("label"),
            has_shape=has_shape,
            shape_null=shape_null,
            shape=shape,
            properties=properties,
        )
