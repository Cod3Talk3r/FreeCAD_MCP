"""Read-only CAD state collection adapter (M20 Phase 3A).

The parent process never imports ``FreeCAD`` or ``freecad_s1``.  Native data is
collected inside the FreeCAD child by the read-only ``collect_cad_state``
operation and returned as JSON-serializable data, which this module normalizes
through the Taiga-agnostic :class:`~freecad_mcp.app.taiga_contracts.CADState`
contracts.

Fields the child could not observe (headless FreeCAD has no workbench, edit
mode, undo stack, or GUI selection) stay ``None`` and are listed in
``unavailable_fields``; they are never fabricated.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from freecad_mcp.freecad_bridge import FreeCadOperationError

from .taiga_contracts import (
    CADNode,
    CADSelectionItem,
    CADShapeDiagnostics,
    CADState,
)

#: Name of the read-only bridge operation that collects native CAD state.
COLLECT_CAD_STATE_OP = "collect_cad_state"


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _as_str_list(value: Any) -> Optional[list[str]]:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


def _as_float_dict(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, float] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        number = _as_float(item)
        if number is not None:
            out[key] = number
    return out


def _as_int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        number = _as_int(item)
        if number is not None:
            out[key] = number
    return out


def _as_vec3(value: Any) -> Optional[tuple[float, float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    components = [_as_float(item) for item in value]
    if any(component is None for component in components):
        return None
    return (components[0], components[1], components[2])  # type: ignore[arg-type]


def _normalize_shape(raw: Any) -> CADShapeDiagnostics:
    data: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    face_dirs = _as_str_list(data.get("face_dirs"))
    bbox = _as_vec3(data.get("bbox"))
    valid = _as_bool(data.get("valid"))
    volume = _as_float(data.get("volume"))
    area = _as_float(data.get("area"))
    n_faces = _as_int(data.get("n_faces"))
    n_edges = _as_int(data.get("n_edges"))
    n_solids = _as_int(data.get("n_solids"))
    available = any(
        value is not None
        for value in (valid, volume, area, bbox, n_faces, n_edges, n_solids, face_dirs)
    )
    return CADShapeDiagnostics(
        available=available,
        valid=valid,
        volume=volume,
        area=area,
        bbox=bbox,
        n_faces=n_faces,
        n_edges=n_edges,
        n_solids=n_solids,
        face_dirs=face_dirs,
    )


def _normalize_node(raw: Any) -> Optional[CADNode]:
    if not isinstance(raw, Mapping):
        return None
    name = _as_str(raw.get("name"))
    type_id = _as_str(raw.get("type"))
    if name is None or type_id is None:
        return None
    parent = _as_int(raw.get("parent"))
    depth = _as_int(raw.get("depth"))
    return CADNode(
        name=name,
        type=type_id,
        parent=parent if parent is not None else -1,
        depth=depth if depth is not None else 0,
        num=_as_float_dict(raw.get("num")),
        geo=_as_int_dict(raw.get("geo")),
        cons=_as_int_dict(raw.get("cons")),
    )


def _normalize_selection(raw: Any) -> Optional[CADSelectionItem]:
    if not isinstance(raw, Mapping):
        return None
    kind = _as_str(raw.get("kind"))
    object_name = _as_str(raw.get("object"))
    object_type = _as_str(raw.get("object_type"))
    if kind is None or object_name is None or object_type is None:
        return None
    return CADSelectionItem(
        kind=kind,
        object=object_name,
        object_type=object_type,
        subs=_as_str_list(raw.get("subs")) or [],
        normal=_as_vec3(raw.get("normal")),
        offset=_as_float(raw.get("offset")),
        count=_as_int(raw.get("count")),
    )


def normalize_cad_state(raw: Mapping[str, Any], *, source: str = "freecad_live") -> CADState:
    """Normalize a child ``collect_cad_state`` payload into a :class:`CADState`.

    The payload is treated as untrusted input: only well-typed values are
    accepted, and anything absent or malformed stays ``None``/empty.  No
    defaults are invented for unavailable CAD data.
    """
    tree: list[CADNode] = []
    for node_raw in raw.get("tree") or []:
        node = _normalize_node(node_raw)
        if node is not None:
            tree.append(node)

    selection: list[CADSelectionItem] = []
    for item_raw in raw.get("selection") or []:
        item = _normalize_selection(item_raw)
        if item is not None:
            selection.append(item)

    recent_flags_raw = raw.get("recent_flags") or []
    recent_flags = [
        value
        for value in (_as_float(item) for item in recent_flags_raw)
        if value is not None
    ]

    return CADState(
        source=source,
        doc_open=_as_bool(raw.get("doc_open")),
        workbench=_as_str(raw.get("workbench")),
        edit=_as_str(raw.get("edit")),
        has_body=_as_bool(raw.get("has_body")),
        undo_available=_as_bool(raw.get("undo_available")),
        tree=tree,
        selection=selection,
        recent=[item for item in (raw.get("recent") or []) if isinstance(item, str)],
        events=[item for item in (raw.get("events") or []) if isinstance(item, str)],
        shape=_normalize_shape(raw.get("shape")),
        recent_flags=recent_flags,
        unavailable_fields=[
            item for item in (raw.get("unavailable_fields") or []) if isinstance(item, str)
        ],
        collected_at=_as_str(raw.get("collected_at")),
    )


class CadStateCollector:
    """Collect a :class:`CADState` from a real FreeCAD document via the bridge.

    ``executor`` is any object with a ``run(op, params)`` method (the same
    contract used by :class:`~freecad_mcp.app.direct.DirectBackend`), typically
    a :class:`~freecad_mcp.freecad_bridge.FreeCadBridge`.
    """

    def __init__(self, executor: Any, *, source: str = "freecad_live") -> None:
        self._executor = executor
        self._source = source

    def collect_raw(
        self,
        document_id: str,
        workspace: str | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Run the read-only child operation and return its JSON payload."""
        params: dict[str, Any] = {
            "document_id": document_id,
            "workspace": workspace or "",
        }
        extra: dict[str, Any] = {}
        if timeout is not None:
            extra["timeout"] = timeout
        result = self._executor.run(COLLECT_CAD_STATE_OP, params, **extra)
        if not isinstance(result, dict):
            raise FreeCadOperationError(
                f"Unexpected bridge response format: {result!r}",
                error_type="BridgeError",
            )
        return result

    def collect(
        self,
        document_id: str,
        workspace: str | None = None,
        *,
        timeout: float | None = None,
    ) -> CADState:
        """Collect and normalize the document's CAD state."""
        return normalize_cad_state(
            self.collect_raw(document_id, workspace, timeout=timeout),
            source=self._source,
        )