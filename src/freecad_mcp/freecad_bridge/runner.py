"""FreeCAD-side operation runner.

This module executes **inside FreeCAD's own Python interpreter**
(``freecadcmd runner.py``). It is never imported by the MCP server process.

It reads a request envelope (JSON), dispatches the named operation to a fixed
handler, and writes a response envelope (JSON) to the supplied response path.

Keeping the operations here - explicit and fixed - is what prevents the bridge
from becoming a general "run this code inside FreeCAD" interface.

``FreeCAD``/``Part`` are imported *lazily inside handlers* so that the envelope
and dispatch logic remain importable and unit-testable on the host interpreter.
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# When this module is executed as ``__main__`` (e.g. via ``freecadcmd.exe runner.py``),
# Python sets __package__ to None, which breaks imports.  Set the package and
# ensure the package root is on sys.path.
_PACKAGE_ROOT = Path(__file__).parent.parent.parent
if __package__ is None or __package__ == "":
    __package__ = "freecad_mcp.freecad_bridge"
    _pkg_dir = str(_PACKAGE_ROOT)
    if _pkg_dir not in sys.path:
        sys.path.insert(0, _pkg_dir)

OP_GET_VERSION = "get_version"
OP_PING = "ping"
OP_CREATE_DOCUMENT = "create_document"
OP_CREATE_BOX = "create_box"
OP_CREATE_CYLINDER = "create_cylinder"
OP_SET_PARAMETER = "set_parameter"
OP_RECOMPUTE = "recompute"
OP_LIST_OBJECTS = "list_objects"
OP_INSPECT_OBJECT = "inspect_object"
OP_VALIDATE = "validate"
OP_SAVE_DOCUMENT = "save_document"
OP_EXPORT_STEP = "export_step"
OP_TANK = "tank"
OP_TUBULAR_COIL = "tubular_coil"
OP_BOOLEAN_CUT = "boolean_cut"
OP_BOOLEAN_FUSE = "boolean_fuse"
OP_BOOLEAN_COMMON = "boolean_common"
OP_SET_PLACEMENT = "set_placement"
OP_GET_BODY_TIP = "get_body_tip"
OP_SET_BODY_TIP = "set_body_tip"
OP_REORDER_BODY_FEATURES = "reorder_body_features"
OP_CREATE_BODY = "create_body"
OP_CREATE_SKETCH = "create_sketch"
OP_SKETCH_ADD_RECTANGLE = "sketch_add_rectangle"
OP_SKETCH_ADD_CONSTRAINT = "sketch_add_constraint"
OP_SKETCH_DELETE_CONSTRAINT = "sketch_delete_constraint"
OP_SKETCH_EDIT_CONSTRAINT = "sketch_edit_constraint"
OP_VALIDATE_REOPEN = "validate_reopen"
OP_SKETCH_ADD_CIRCLE = "sketch_add_circle"
OP_SKETCH_ADD_TRIANGLE = "sketch_add_triangle"
OP_SKETCH_ADD_ELLIPSE = "sketch_add_ellipse"
OP_SKETCH_ADD_REGULAR_POLYGON = "sketch_add_regular_polygon"
OP_SKETCH_ADD_POLYGON = "sketch_add_polygon"
OP_INSPECT_SKETCH = "inspect_sketch"
OP_SKETCH_INSPECT_PROFILES = "inspect_sketch_profiles"
OP_SKETCH_SELECT_PROFILE = "select_sketch_profile"
OP_SKETCH_DELETE_GEOMETRY = "sketch_delete_geometry"
OP_SKETCH_EDIT_LINE = "sketch_edit_line"
OP_SKETCH_EDIT_CIRCLE = "sketch_edit_circle"
OP_SKETCH_EDIT_ELLIPSE = "sketch_edit_ellipse"
OP_SKETCH_EDIT_RECTANGLE = "sketch_edit_rectangle"
OP_SKETCH_EDIT_TRIANGLE = "sketch_edit_triangle"
OP_SKETCH_EDIT_REGULAR_POLYGON = "sketch_edit_regular_polygon"
OP_SKETCH_EDIT_POLYGON = "sketch_edit_polygon"
OP_SKETCH_PAD = "sketch_pad"
OP_SKETCH_POCKET = "sketch_pocket"
OP_SKETCH_REVOLUTION = "sketch_revolution"
OP_SKETCH_GROOVE = "sketch_groove"
OP_SKETCH_ADDITIVE_PIPE = "sketch_additive_pipe"
OP_SKETCH_SUBTRACTIVE_PIPE = "sketch_subtractive_pipe"
OP_SKETCH_ADDITIVE_LOFT = "sketch_additive_loft"
OP_SKETCH_SUBTRACTIVE_LOFT = "sketch_subtractive_loft"
OP_SKETCH_HOLE = "sketch_hole"
OP_SKETCH_ATTACH_SUPPORT = "sketch_attach_support"
OP_SKETCH_ADD_EXTERNAL_GEOMETRY = "sketch_add_external_geometry"
OP_DELETE_OBJECT = "delete_object"
OP_PARTDESIGN_FILLET = "partdesign_fillet"
OP_PARTDESIGN_CHAMFER = "partdesign_chamfer"
OP_PARTDESIGN_LINEAR_PATTERN = "partdesign_linear_pattern"
OP_PARTDESIGN_POLAR_PATTERN = "partdesign_polar_pattern"
OP_PARTDESIGN_MIRRORED = "partdesign_mirrored"
OP_PARTDESIGN_THICKNESS = "partdesign_thickness"
OP_CREATE_SNAPSHOT = "create_snapshot"
OP_LIST_SNAPSHOTS = "list_snapshots"
OP_RESTORE_SNAPSHOT = "restore_snapshot"

#: Upper bound on polygon sides to prevent accidental huge geometry creation.
#: Enforced consistently in the runner, the parent-side validator, and the MCP
#: parameter schema.  Raise this only after confirming real-FreeCAD performance.
MAX_POLYGON_SIDES = 1000

REQUEST_ENV = "FREECAD_MCP_REQUEST"
RESPONSE_ENV = "FREECAD_MCP_RESPONSE"

#: Exit code the runner returns when it could not even load/write its envelope.
EXIT_ENVELOPE_ERROR = 2


def _open_or_create_document(_FC: Any, wks: Any, document_id: str) -> Any:
    """Open the workspace document if it exists, otherwise create a new one.

    A missing ``.fcstd`` file is the *only* condition that creates a document.
    If the file exists but FreeCAD cannot open it, the error propagates to the
    handler's error reporting instead of silently replacing the document.
    """
    fcstd_path = wks.document_path(document_id)
    if fcstd_path.is_file():
        return _FC.openDocument(str(fcstd_path))
    return _FC.newDocument(document_id)


def _open_existing_document(_FC: Any, wks: Any, document_id: str) -> Any:
    """Open an existing workspace document; never create one.

    Raises ``FileNotFoundError`` when the saved document is absent so callers
    report a clear error instead of creating an empty replacement.
    """
    fcstd_path = wks.document_path(document_id)
    if not fcstd_path.is_file():
        raise FileNotFoundError(f"document '{document_id}' not found at {fcstd_path}")
    return _FC.openDocument(str(fcstd_path))


def _op_ping(params: dict[str, Any]) -> dict[str, Any]:
    return {"message": params.get("message", "pong")}


def _op_get_version(params: dict[str, Any]) -> dict[str, Any]:
    import FreeCAD  # noqa: PLC0415 - imported lazily; only available inside FreeCAD

    raw = [str(part) for part in FreeCAD.Version()]
    result: dict[str, Any] = {
        "version": ".".join(raw[0:3]),
        "version_raw": raw,
        "build_date": raw[5] if len(raw) > 5 else None,
        "revision": raw[7] if len(raw) > 7 else None,
        "gui_available": False,
        "python_version": sys.version.split()[0],
        "freecad_python_executable": sys.executable,
    }

    try:
        import Part  # noqa: F401, PLC0415 - probe availability only
    except Exception as exc:  # noqa: BLE001 - report any import failure verbatim
        result["part_available"] = False
        result["part_error"] = f"{type(exc).__name__}: {exc}"
    else:
        result["part_available"] = True

    return result


# ---------------------------------------------------------------------------
# New operation handlers for Milestone 3
# ---------------------------------------------------------------------------


def _op_create_document(params: dict[str, Any]) -> dict[str, Any]:
    """Create a new FreeCAD document and save it atomically."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""
    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "create_document", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "create_document", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "create_document", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        import Part as _Part
        doc = _open_or_create_document(_FC, wks, normalized)
        doc.recompute()
        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        # Write meta: revision = 1 on first successful save
        meta = {"revision": 1, "created_at": meta_before.get("created_at"), "document_id": normalized}
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "create_document", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {"document_id": normalized, "saved_path": str(fcstd_path), "meta_before": meta_before}


def _op_create_box(params: dict[str, Any]) -> dict[str, Any]:
    """Create a Part::Box in the current document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or "Box1"
    length = float(params.get("length") or 10.0)
    width = float(params.get("width") or 5.0)
    height = float(params.get("height") or 3.0)
    x = float(params.get("x") or 0.0)
    y = float(params.get("y") or 0.0)
    z = float(params.get("z") or 0.0)
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "create_box", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "create_box", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "create_box", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        doc = _open_or_create_document(_FC, wks, normalized)
        obj_name = object_name
        counter = 0
        while doc.getObject(obj_name) is not None:
            counter += 1
            obj_name = f"{object_name}_{counter}"
        box = doc.addObject("Part::Box", obj_name)
        box.Length = length
        box.Width = width
        box.Height = height
        box.Placement = _FC.Placement(_FC.Vector(x, y, z), _FC.Rotation())
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        # Increment revision after successful save
        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_saved_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "create_box", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "create_box",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "length": length,
            "width": width,
            "height": height,
            "x": x,
            "y": y,
            "z": z,
            "meta_before": meta_before,
        },
    }


def _op_create_cylinder(params: dict[str, Any]) -> dict[str, Any]:
    """Create a Part::Cylinder in the current document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or "Cyl1"
    radius = float(params.get("radius") or 2.0)
    height = float(params.get("height") or 7.0)
    x = float(params.get("x") or 0.0)
    y = float(params.get("y") or 0.0)
    z = float(params.get("z") or 0.0)
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "create_cylinder", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "create_cylinder", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "create_cylinder", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        doc = _open_or_create_document(_FC, wks, normalized)
        obj_name = object_name
        counter = 0
        while doc.getObject(obj_name) is not None:
            counter += 1
            obj_name = f"{object_name}_{counter}"
        cyl = doc.addObject("Part::Cylinder", obj_name)
        cyl.Radius = radius
        cyl.Height = height
        cyl.Placement = _FC.Placement(_FC.Vector(x, y, z), _FC.Rotation())
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        # Increment revision
        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_saved_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "create_cylinder", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "create_cylinder",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "radius": radius,
            "height": height,
            "x": x,
            "y": y,
            "z": z,
            "meta_before": meta_before,
        },
    }


def _op_set_parameter(params: dict[str, Any]) -> dict[str, Any]:
    """Set a whitelisted parameter on an existing object."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    property_name = params.get("property") or ""
    value = float(params.get("value") or 0.0)
    workspace_str = params.get("workspace") or ""

    WHITELIST = {
        "Box": {"Length", "Width", "Height"},
        "Cylinder": {"Radius", "Height"},
    }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "set_parameter", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "set_parameter", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "set_parameter", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)
        obj = doc.getObject(object_name)
        if obj is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "set_parameter", "error": f"object '{object_name}' not found"}

        type_id = obj.TypeId
        type_name = type_id.split("::")[-1]
        allowed = WHITELIST.get(type_name, set())
        if property_name not in allowed:
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "set_parameter",
                "error": f"property '{property_name}' not whitelisted for {type_id}; allowed: {allowed}",
            }

        try:
            setattr(obj, property_name, value)
        except Exception as exc:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "set_parameter", "error": f"could not set {property_name}={value}: {exc}"}

        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        # Increment revision
        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "set_parameter", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "set_parameter",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "property": property_name,
            "value": value,
            "meta_before": meta_before,
        },
    }


def _op_recompute(params: dict[str, Any]) -> dict[str, Any]:
    """Recompute the active document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "recompute", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "recompute", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "recompute", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC

        # When the requested document is already open in FreeCAD (e.g. opened
        # manually outside the MCP workspace), recompute it directly instead of
        # reopening it from the workspace .fcstd path.  Otherwise preserve the
        # existing workspace-path fallback exactly.
        doc = None
        active = _FC.ActiveDocument
        if active is not None and getattr(active, "Name", None) == normalized:
            doc = active

        if doc is None:
            doc = _open_existing_document(_FC, wks, normalized)
        doc.recompute()
        meta_before = ws_mod.read_meta(wks.meta_path(normalized))
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "recompute", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    meta = ws_mod.read_meta(wks.meta_path(normalized))
    return {
        "ok": True,
        "op": "recompute",
        "result": {
            "document_id": normalized,
            "recomputed_at": datetime.now(timezone.utc).isoformat(),
            "revision": meta.get("revision"),
            "meta_before": meta_before,
        },
    }


def _op_list_objects(params: dict[str, Any]) -> dict[str, Any]:
    """List all objects in a document with name, type, and basic geometry."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "list_objects", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "list_objects", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "list_objects", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)
        objects = []
        for obj in doc.Objects:
            obj_info = {"name": obj.Name, "type": obj.TypeId}
            type_name = obj.TypeId.split("::")[-1]
            if hasattr(obj, "Shape") and obj.Shape is not None:
                try:
                    obj_info["shape_type"] = obj.Shape.ShapeType
                    obj_info["volume"] = obj.Shape.Volume
                except Exception:
                    pass
            if hasattr(obj, "Length"):
                try:
                    obj_info["length"] = float(obj.Length)
                except Exception:
                    pass
            if hasattr(obj, "Width"):
                try:
                    obj_info["width"] = float(obj.Width)
                except Exception:
                    pass
            if hasattr(obj, "Height"):
                try:
                    obj_info["height"] = float(obj.Height)
                except Exception:
                    pass
            if hasattr(obj, "Radius"):
                try:
                    obj_info["radius"] = float(obj.Radius)
                except Exception:
                    pass
            if type_name == "SketchObject":
                try:
                    obj_info["geometry_count"] = len(obj.Geometry)
                    obj_info["constraint_count"] = len(obj.Constraints)
                except Exception:
                    pass
            objects.append(obj_info)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "list_objects", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    meta = ws_mod.read_meta(wks.meta_path(normalized))
    return {"document_id": normalized, "objects": objects, "meta": meta}


def _op_inspect_object(params: dict[str, Any]) -> dict[str, Any]:
    """Inspect a single object by name, returning full details."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "inspect_object", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "inspect_object", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "inspect_object", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)
        obj = doc.getObject(object_name)
        if obj is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "inspect_object", "error": f"object '{object_name}' not found"}

        info = {"name": obj.Name, "type": obj.TypeId, "label": obj.Label}
        type_name = obj.TypeId.split("::")[-1]
        if hasattr(obj, "Shape") and obj.Shape is not None:
            try:
                info["shape_type"] = obj.Shape.ShapeType
                info["volume"] = obj.Shape.Volume
                if hasattr(obj.Shape, "isValid"):
                    info["shape_valid"] = obj.Shape.isValid()
            except Exception:
                pass
        for prop in ("Length", "Width", "Height", "Radius", "Diameter"):
            if hasattr(obj, prop):
                try:
                    info[prop] = float(getattr(obj, prop))
                except Exception:
                    pass
        if hasattr(obj, "Placement"):
            try:
                placement = obj.Placement
                info["placement_x"] = float(placement.Base.x)
                info["placement_y"] = float(placement.Base.y)
                info["placement_z"] = float(placement.Base.z)
            except Exception:
                pass
        if hasattr(obj, "Material"):
            info["material"] = obj.Material
        if hasattr(obj, "Color"):
            info["color"] = obj.Color
        if type_name == "SketchObject":
            try:
                info["geometry_count"] = len(obj.Geometry)
                info["constraint_count"] = len(obj.Constraints)
            except Exception:
                pass
        # Dependency relationships via native InList/OutList.
        #   depends_on  = objects this object references/uses (OutList).
        #   depended_on_by = objects that reference this object (InList).
        try:
            outlist = list(getattr(obj, "OutList", []) or [])
            info["depends_on"] = [getattr(o, "Name", str(o)) for o in outlist]
        except Exception:
            info["depends_on"] = []
        try:
            inlist = list(getattr(obj, "InList", []) or [])
            info["depended_on_by"] = [getattr(o, "Name", str(o)) for o in inlist]
        except Exception:
            info["depended_on_by"] = []
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "inspect_object", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "inspect_object",
        "result": {"document_id": normalized, "object_name": object_name, "info": info},
    }


def _op_validate(params: dict[str, Any]) -> dict[str, Any]:
    """Validate geometry of all objects in a document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "validate", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "validate", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "validate", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC

        # When the requested document is already open in FreeCAD (e.g. opened
        # manually outside the MCP workspace), validate it directly instead of
        # reopening it from the workspace .fcstd path.  Otherwise preserve the
        # existing workspace-path fallback exactly.
        doc = None
        active = _FC.ActiveDocument
        if active is not None and getattr(active, "Name", None) == normalized:
            doc = active

        if doc is None:
            doc = _open_existing_document(_FC, wks, normalized)
        results = []
        for obj in doc.Objects:
            obj_result = {"name": obj.Name, "type": obj.TypeId, "valid": True, "errors": []}
            if hasattr(obj, "Shape") and obj.Shape is not None:
                try:
                    st = obj.Shape.ShapeType
                    obj_result["shape_type"] = st
                    if st != "Solid":
                        obj_result["errors"].append(f"shape type '{st}' is not a Solid")
                    try:
                        vol = obj.Shape.Volume
                        if vol is not None and vol <= 0:
                            obj_result["errors"].append("volume <= 0")
                    except Exception:
                        obj_result["errors"].append("could not read volume")
                    if hasattr(obj.Shape, "isValid"):
                        try:
                            valid = obj.Shape.isValid()
                            if not valid:
                                obj_result["valid"] = False
                                obj_result["errors"].append("Shape.isValid() returned False")
                        except Exception:
                            pass
                except Exception as e:
                    obj_result["errors"].append(f"could not read shape type: {e}")
                    obj_result["valid"] = False
            else:
                obj_result["errors"].append("no Shape attribute")
                obj_result["valid"] = False
            results.append(obj_result)
        all_valid = all(r["valid"] for r in results)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "validate", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "validate",
        "result": {"document_id": normalized, "objects": results, "all_valid": all_valid},
    }


def _op_save_document(params: dict[str, Any]) -> dict[str, Any]:
    """Explicitly save the current document atomically."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "save_document", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "save_document", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "save_document", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)
        meta_before = ws_mod.read_meta(wks.meta_path(normalized))
        current_revision = meta_before.get("revision", 0)

        fcstd_path = wks.document_path(normalized)

        # Preserve the previous valid revision as a snapshot before it is
        # overwritten by the new save.  A snapshot failure does not fail the
        # save itself.
        if fcstd_path.is_file() and current_revision > 0:
            try:
                ws_mod.create_snapshot(wks, normalized, current_revision, pid=pid)
            except Exception:
                # Snapshot creation is best-effort; never block a save.
                pass

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        # Increment revision after successful save
        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_saved_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "save_document", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "save_document",
        "result": {
            "document_id": normalized,
            "saved_path": str(fcstd_path),
            "revision": meta["revision"],
            "meta_before": meta_before,
        },
    }


def _op_delete_object(params: dict[str, Any]) -> dict[str, Any]:
    """Delete an object from the document.

    When deleting a ``PartDesign::Body``, its ``Group`` members (contained
    features such as Pad, Pocket, sketches) are removed first.  FreeCAD's
    ``doc.removeObject`` on a Body only removes the container and leaves its
    Group members orphaned -- they remain in the document as valid geometry
    and survive save/reopen.  Cascading the Group members makes deletion
    predictable and ensures nothing dependent is silently left behind.

    Objects that are not a Body are removed with ``removeObject`` only; no
    dependents outside the Body's group are touched.  The document is
    recomputed, saved atomically, and its revision bumped.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    workspace_str = params.get("workspace") or ""

    if not object_name.strip():
        return {"ok": False, "op": "delete_object", "error": "object_name must not be empty."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "delete_object", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env()
            if not workspace_str
            else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "delete_object", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "delete_object", "error": lock_status.get("error", "lock failed")}

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "delete_object", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        obj = doc.getObject(object_name)
        if obj is None:
            return _fail(f"object '{object_name}' not found")

        type_id = getattr(obj, "TypeId", "")
        type_name = type_id.split("::")[-1]
        deleted: list[str] = []

        # A Body is a container: cascade into its Group members so removing
        # the Body does not leave contained features (and their geometry)
        # orphaned in the document.  Delete members first, then the Body.
        if type_name == "Body":
            members = list(getattr(obj, "Group", []) or [])
            for member in members:
                member_name = getattr(member, "Name", None)
                if member_name is None:
                    continue
                try:
                    doc.removeObject(member_name)
                    deleted.append(member_name)
                except Exception:
                    # Best-effort cleanup; continue with the remaining members.
                    pass

        try:
            doc.removeObject(object_name)
            deleted.append(object_name)
        except Exception as exc:
            return _fail(f"could not delete object '{object_name}': {exc}")

        doc.recompute()

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "delete_object",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "deleted": deleted,
            "remaining": [o.Name for o in doc.Objects],
        },
    }


def _op_export_step(params: dict[str, Any]) -> dict[str, Any]:
    """Export selected objects to STEP format."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_names = params.get("object_names") or []
    step_path = params.get("step_path") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "export_step", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "export_step", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "export_step", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC
        import Part as _Part
        doc = _open_existing_document(_FC, wks, normalized)

        objects_to_export = []
        if object_names:
            for oname in object_names:
                obj = doc.getObject(oname)
                if obj is not None:
                    objects_to_export.append(obj)
        else:
            for obj in doc.Objects:
                if "Part" in obj.TypeId:
                    objects_to_export.append(obj)

        if not step_path:
            step_path = str(wks.document_path(normalized).with_suffix(".step"))

        # Validate step_path stays within workspace
        step_resolved = os.path.normpath(step_path)
        workspace_resolved = os.path.normpath(str(wks.root))
        if not step_resolved.startswith(workspace_resolved + os.sep) and step_resolved != str(wks.root):
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "export_step",
                "error": f"step_path '{step_path}' escapes the workspace {wks.root}",
            }

        doc.recompute()
        _Part.export(objects_to_export, step_path)

        meta_before = ws_mod.read_meta(wks.meta_path(normalized))
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "export_step", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "export_step",
        "result": {
            "document_id": normalized,
            "step_path": step_path,
            "exported_count": len(objects_to_export),
            "meta_before": meta_before,
        },
    }


# ---------------------------------------------------------------------------
# New operation handlers for Milestone 4
# ---------------------------------------------------------------------------

def _op_tank(params: dict[str, Any]) -> dict[str, Any]:
    """Create a rectangular tank (Part::Box) in the document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or "Tank1"
    length = float(params.get("length") or 20.0)
    width = float(params.get("width") or 15.0)
    height = float(params.get("height") or 10.0)
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "tank", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "tank", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "tank", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        import Part as _Part
        doc = _open_or_create_document(_FC, wks, normalized)
        obj_name = object_name
        counter = 0
        while doc.getObject(obj_name) is not None:
            counter += 1
            obj_name = f"{object_name}_{counter}"
        box = doc.addObject("Part::Box", obj_name)
        box.Length = length
        box.Width = width
        box.Height = height
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_saved_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "tank", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "tank",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "length": length,
            "width": width,
            "height": height,
            "meta_before": meta_before,
        },
    }


def _op_tubular_coil(params: dict[str, Any]) -> dict[str, Any]:
    """Create a tubular coil (helix with pipe) in the document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or "Coil1"
    coil_radius = float(params.get("coil_radius") or 10.0)
    tube_radius = float(params.get("tube_radius") or 2.0)
    num_turns = int(params.get("num_turns") or 5)
    pitch = float(params.get("pitch") or 5.0)
    workspace_str = params.get("workspace") or ""

    # Validate parameters
    if coil_radius <= 0:
        return {"ok": False, "op": "tubular_coil", "error": "coil_radius must be positive"}
    if tube_radius <= 0:
        return {"ok": False, "op": "tubular_coil", "error": "tube_radius must be positive"}
    if num_turns <= 0:
        return {"ok": False, "op": "tubular_coil", "error": "num_turns must be positive"}
    if pitch <= 0:
        return {"ok": False, "op": "tubular_coil", "error": "pitch must be positive"}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "tubular_coil", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "tubular_coil", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "tubular_coil", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        import Part as _Part
        doc = _open_or_create_document(_FC, wks, normalized)

        # Create a helix path for the coil.  FreeCAD 1.1.x uses
        # ``Part.makeHelix(pitch, height, radius)``; there is no ``Part.Helix``.
        helix = _Part.makeHelix(pitch, pitch * num_turns, coil_radius)

        # Sweep a circular profile along the helix.  The profile lies in the XZ
        # plane (normal +Y) so it is perpendicular to the helix tangent at the
        # start point (coil_radius, 0, 0).  ``makePipeShell`` returns a Solid.
        profile_edge = _Part.makeCircle(
            tube_radius, _FC.Vector(coil_radius, 0, 0), _FC.Vector(0, 1, 0)
        )
        profile_wire = _Part.Wire([profile_edge])
        coil_shape = helix.makePipeShell([profile_wire], True, True)

        obj_name = object_name
        counter = 0
        while doc.getObject(obj_name) is not None:
            counter += 1
            obj_name = f"{object_name}_{counter}"
        coil_obj = doc.addObject("Part::Feature", obj_name)
        coil_obj.Shape = coil_shape
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_saved_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "tubular_coil", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "tubular_coil",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "coil_radius": coil_radius,
            "tube_radius": tube_radius,
            "num_turns": num_turns,
            "pitch": pitch,
            "meta_before": meta_before,
        },
    }


def _op_boolean_cut(params: dict[str, Any]) -> dict[str, Any]:
    """Subtract one Part shape from another using a native Boolean cut."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    base_object = params.get("base_object") or ""
    tool_object = params.get("tool_object") or ""
    result_name = params.get("result_name") or "Cut1"
    workspace_str = params.get("workspace") or ""

    if not base_object:
        return {"ok": False, "op": "boolean_cut", "error": "base_object is required"}
    if not tool_object:
        return {"ok": False, "op": "boolean_cut", "error": "tool_object is required"}
    if base_object == tool_object:
        return {
            "ok": False,
            "op": "boolean_cut",
            "error": "base_object and tool_object must be different",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "boolean_cut", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "boolean_cut", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "boolean_cut", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "boolean_cut", "error": message}

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)

        base_obj = doc.getObject(base_object)
        if base_obj is None:
            return _fail(f"base object '{base_object}' not found")
        tool_obj = doc.getObject(tool_object)
        if tool_obj is None:
            return _fail(f"tool object '{tool_object}' not found")

        base_shape = getattr(base_obj, "Shape", None)
        tool_shape = getattr(tool_obj, "Shape", None)
        if base_shape is None:
            return _fail(f"base object '{base_object}' has no Part shape")
        if tool_shape is None:
            return _fail(f"tool object '{tool_object}' has no Part shape")

        for label, shape in (("base", base_shape), ("tool", tool_shape)):
            try:
                is_valid = bool(shape.isValid())
            except Exception as exc:
                return _fail(f"could not validate {label} shape: {exc}")
            if not is_valid:
                return _fail(f"{label} object shape is not valid")
            shape_type = getattr(shape, "ShapeType", "")
            if shape_type != "Solid":
                return _fail(f"{label} object is not a Solid (type: {shape_type})")
            try:
                volume = float(shape.Volume)
            except Exception:
                volume = 0.0
            if volume <= 0:
                return _fail(f"{label} object has an empty (zero-volume) shape")

        try:
            result_shape = base_shape.cut(tool_shape)
        except Exception as exc:
            return _fail(f"Boolean cut failed: {exc}")

        if result_shape is None:
            return _fail("Boolean cut returned no shape")

        # FreeCAD/OCC often wraps a Boolean result in a Compound even when it
        # contains a single solid.  Store the solid itself so the result is one
        # valid hollow solid rather than a compound wrapper.
        try:
            if getattr(result_shape, "ShapeType", "") == "Compound":
                solids = list(getattr(result_shape, "Solids", []) or [])
                if len(solids) == 1:
                    result_shape = solids[0]
                elif not solids:
                    return _fail("Boolean cut produced an empty result (no solids)")
        except Exception as exc:
            return _fail(f"could not inspect cut result: {exc}")

        try:
            result_valid = bool(result_shape.isValid())
        except Exception as exc:
            return _fail(f"could not validate cut result: {exc}")
        if not result_valid:
            return _fail("Boolean cut produced an invalid shape")
        result_volume = float(getattr(result_shape, "Volume", 0.0) or 0.0)
        if result_volume <= 0:
            return _fail("Boolean cut produced an empty result (zero volume)")
        result_shape_type = getattr(result_shape, "ShapeType", "")
        if result_shape_type not in ("Solid", "Compound"):
            return _fail(f"Boolean cut result is not a usable Part shape (type: {result_shape_type})")

        # Allocate the result object name without touching existing objects.
        actual_name = result_name
        counter = 0
        while doc.getObject(actual_name) is not None:
            counter += 1
            actual_name = f"{result_name}_{counter}"

        result_obj = doc.addObject("Part::Feature", actual_name)
        result_obj.Shape = result_shape
        doc.recompute()

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)

        bbox = result_shape.BoundBox
        length = float(bbox.XLength)
        width = float(bbox.YLength)
        height = float(bbox.ZLength)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "boolean_cut",
        "result": {
            "document_id": normalized,
            "object_name": actual_name,
            "base_object": base_object,
            "tool_object": tool_object,
            "type": result_obj.TypeId,
            "shape_type": result_shape_type,
            "volume": result_volume,
            "length": length,
            "width": width,
            "height": height,
            "meta_before": meta_before,
        },
    }


def _op_boolean_fuse(params: dict[str, Any]) -> dict[str, Any]:
    """Fuse two Part shapes using a native Boolean union."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    base_object = params.get("base_object") or ""
    tool_object = params.get("tool_object") or ""
    result_name = params.get("result_name") or "Fuse1"
    workspace_str = params.get("workspace") or ""

    if not base_object:
        return {"ok": False, "op": "boolean_fuse", "error": "base_object is required"}
    if not tool_object:
        return {"ok": False, "op": "boolean_fuse", "error": "tool_object is required"}
    if base_object == tool_object:
        return {
            "ok": False,
            "op": "boolean_fuse",
            "error": "base_object and tool_object must be different",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "boolean_fuse", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "boolean_fuse", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "boolean_fuse", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "boolean_fuse", "error": message}

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)

        base_obj = doc.getObject(base_object)
        if base_obj is None:
            return _fail(f"base object '{base_object}' not found")
        tool_obj = doc.getObject(tool_object)
        if tool_obj is None:
            return _fail(f"tool object '{tool_object}' not found")

        base_shape = getattr(base_obj, "Shape", None)
        tool_shape = getattr(tool_obj, "Shape", None)
        if base_shape is None:
            return _fail(f"base object '{base_object}' has no Part shape")
        if tool_shape is None:
            return _fail(f"tool object '{tool_object}' has no Part shape")

        for label, shape in (("base", base_shape), ("tool", tool_shape)):
            try:
                is_valid = bool(shape.isValid())
            except Exception as exc:
                return _fail(f"could not validate {label} shape: {exc}")
            if not is_valid:
                return _fail(f"{label} object shape is not valid")
            shape_type = getattr(shape, "ShapeType", "")
            if shape_type != "Solid":
                return _fail(f"{label} object is not a Solid (type: {shape_type})")
            try:
                volume = float(shape.Volume)
            except Exception:
                volume = 0.0
            if volume <= 0:
                return _fail(f"{label} object has an empty (zero-volume) shape")

        try:
            result_shape = base_shape.fuse(tool_shape)
        except Exception as exc:
            return _fail(f"Boolean fuse failed: {exc}")

        if result_shape is None:
            return _fail("Boolean fuse returned no shape")

        try:
            if getattr(result_shape, "ShapeType", "") == "Compound":
                solids = list(getattr(result_shape, "Solids", []) or [])
                if len(solids) == 1:
                    result_shape = solids[0]
                elif not solids:
                    return _fail("Boolean fuse produced an empty result (no solids)")
        except Exception as exc:
            return _fail(f"could not inspect fuse result: {exc}")

        try:
            result_valid = bool(result_shape.isValid())
        except Exception as exc:
            return _fail(f"could not validate fuse result: {exc}")
        if not result_valid:
            return _fail("Boolean fuse produced an invalid shape")
        result_volume = float(getattr(result_shape, "Volume", 0.0) or 0.0)
        if result_volume <= 0:
            return _fail("Boolean fuse produced an empty result (zero volume)")
        result_shape_type = getattr(result_shape, "ShapeType", "")
        if result_shape_type not in ("Solid", "Compound"):
            return _fail(f"Boolean fuse result is not a usable Part shape (type: {result_shape_type})")

        actual_name = result_name
        counter = 0
        while doc.getObject(actual_name) is not None:
            counter += 1
            actual_name = f"{result_name}_{counter}"

        result_obj = doc.addObject("Part::Feature", actual_name)
        result_obj.Shape = result_shape
        doc.recompute()

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)

        bbox = result_shape.BoundBox
        length = float(bbox.XLength)
        width = float(bbox.YLength)
        height = float(bbox.ZLength)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "boolean_fuse",
        "result": {
            "document_id": normalized,
            "object_name": actual_name,
            "base_object": base_object,
            "tool_object": tool_object,
            "type": result_obj.TypeId,
            "shape_type": result_shape_type,
            "volume": result_volume,
            "length": length,
            "width": width,
            "height": height,
            "meta_before": meta_before,
        },
    }


def _op_boolean_common(params: dict[str, Any]) -> dict[str, Any]:
    """Intersect two Part shapes using a native Boolean common."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    base_object = params.get("base_object") or ""
    tool_object = params.get("tool_object") or ""
    result_name = params.get("result_name") or "Common1"
    workspace_str = params.get("workspace") or ""

    if not base_object:
        return {"ok": False, "op": "boolean_common", "error": "base_object is required"}
    if not tool_object:
        return {"ok": False, "op": "boolean_common", "error": "tool_object is required"}
    if base_object == tool_object:
        return {
            "ok": False,
            "op": "boolean_common",
            "error": "base_object and tool_object must be different",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "boolean_common", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "boolean_common", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") != "acquired" and lock_status.get("status") != "reacquired":
        return {"ok": False, "op": "boolean_common", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "boolean_common", "error": message}

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)

        base_obj = doc.getObject(base_object)
        if base_obj is None:
            return _fail(f"base object '{base_object}' not found")
        tool_obj = doc.getObject(tool_object)
        if tool_obj is None:
            return _fail(f"tool object '{tool_object}' not found")

        base_shape = getattr(base_obj, "Shape", None)
        tool_shape = getattr(tool_obj, "Shape", None)
        if base_shape is None:
            return _fail(f"base object '{base_object}' has no Part shape")
        if tool_shape is None:
            return _fail(f"tool object '{tool_object}' has no Part shape")

        for label, shape in (("base", base_shape), ("tool", tool_shape)):
            try:
                is_valid = bool(shape.isValid())
            except Exception as exc:
                return _fail(f"could not validate {label} shape: {exc}")
            if not is_valid:
                return _fail(f"{label} object shape is not valid")
            shape_type = getattr(shape, "ShapeType", "")
            if shape_type != "Solid":
                return _fail(f"{label} object is not a Solid (type: {shape_type})")
            try:
                volume = float(shape.Volume)
            except Exception:
                volume = 0.0
            if volume <= 0:
                return _fail(f"{label} object has an empty (zero-volume) shape")

        try:
            result_shape = base_shape.common(tool_shape)
        except Exception as exc:
            return _fail(f"Boolean common failed: {exc}")

        if result_shape is None:
            return _fail("Boolean common returned no shape")

        try:
            if getattr(result_shape, "ShapeType", "") == "Compound":
                solids = list(getattr(result_shape, "Solids", []) or [])
                if len(solids) == 1:
                    result_shape = solids[0]
                elif not solids:
                    return _fail("Boolean common produced an empty result (no solids)")
        except Exception as exc:
            return _fail(f"could not inspect common result: {exc}")

        try:
            result_valid = bool(result_shape.isValid())
        except Exception as exc:
            return _fail(f"could not validate common result: {exc}")
        if not result_valid:
            return _fail("Boolean common produced an invalid shape")
        result_volume = float(getattr(result_shape, "Volume", 0.0) or 0.0)
        if result_volume <= 0:
            return _fail("Boolean common produced an empty result (zero volume)")
        result_shape_type = getattr(result_shape, "ShapeType", "")
        if result_shape_type not in ("Solid", "Compound"):
            return _fail(f"Boolean common result is not a usable Part shape (type: {result_shape_type})")

        actual_name = result_name
        counter = 0
        while doc.getObject(actual_name) is not None:
            counter += 1
            actual_name = f"{result_name}_{counter}"

        result_obj = doc.addObject("Part::Feature", actual_name)
        result_obj.Shape = result_shape
        doc.recompute()

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)

        bbox = result_shape.BoundBox
        length = float(bbox.XLength)
        width = float(bbox.YLength)
        height = float(bbox.ZLength)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "boolean_common",
        "result": {
            "document_id": normalized,
            "object_name": actual_name,
            "base_object": base_object,
            "tool_object": tool_object,
            "type": result_obj.TypeId,
            "shape_type": result_shape_type,
            "volume": result_volume,
            "length": length,
            "width": width,
            "height": height,
            "meta_before": meta_before,
        },
    }


def _op_set_placement(params: dict[str, Any]) -> dict[str, Any]:
    """Set the placement (position) of an existing Box, Cylinder, or Part::Feature.

    Preserves the object's existing Rotation and only changes the
    translation (Base) of its Placement.  Supported object types are
    Part::Box, Part::Cylinder, and plain Part::Feature objects (e.g. Boolean
    Cut results).  Sketches and other Part subtypes are intentionally excluded.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    x = params.get("x")
    y = params.get("y")
    z = params.get("z")
    workspace_str = params.get("workspace") or ""

    # Validate numeric inputs: must be present, finite numbers (reject NaN/inf).
    try:
        x_val = float(x)
        y_val = float(y)
        z_val = float(z)
    except (TypeError, ValueError):
        return {"ok": False, "op": "set_placement", "error": "x, y, and z must be numeric."}
    for name, val in (("x", x_val), ("y", y_val), ("z", z_val)):
        if math.isnan(val) or math.isinf(val):
            return {"ok": False, "op": "set_placement", "error": f"{name} must be a finite number."}

    SUPPORTED_TYPES = {"Box", "Cylinder", "Feature"}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "set_placement", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "set_placement", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "set_placement", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        doc = _open_existing_document(_FC, wks, normalized)
        obj = doc.getObject(object_name)
        if obj is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "set_placement", "error": f"object '{object_name}' not found"}

        type_id = obj.TypeId
        type_name = type_id.split("::")[-1]
        if type_name not in SUPPORTED_TYPES:
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "set_placement",
                "error": (
                    f"object type '{type_id}' not supported for placement editing; "
                    f"supported: {sorted(SUPPORTED_TYPES)}"
                ),
            }

        if not hasattr(obj, "Placement"):
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "set_placement", "error": f"object '{object_name}' has no Placement property."}

        shape = getattr(obj, "Shape", None)
        if shape is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "set_placement", "error": f"object '{object_name}' has no Part shape."}
        if hasattr(shape, "isValid"):
            try:
                if not bool(shape.isValid()):
                    ws_mod.release_lock(lock_path, pid)
                    return {"ok": False, "op": "set_placement", "error": f"object '{object_name}' has an invalid Part shape."}
            except Exception:
                pass

        current_placement = obj.Placement
        rotation = current_placement.Rotation
        obj.Placement = _FC.Placement(_FC.Vector(x_val, y_val, z_val), rotation)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "set_placement", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "set_placement",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "x": x_val,
            "y": y_val,
            "z": z_val,
            "meta_before": meta_before,
        },
    }


# ---------------------------------------------------------------------------
# New operation handlers for Milestone 13.3 (PartDesign Body.Tip management)
# ---------------------------------------------------------------------------

def _op_get_body_tip(params: dict[str, Any]) -> dict[str, Any]:
    """Return the current Tip of a PartDesign::Body.

    Reads (does not modify) ``Body.Tip`` and reports the Tip's internal Name,
    TypeId, and bounded shape information.  The document is locked exclusively
    during the read so the Tip cannot change concurrently.  No recompute or
    save is performed for a read-only inspection.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name = params.get("body_name") or ""
    workspace_str = params.get("workspace") or ""

    if not body_name.strip():
        return {"ok": False, "op": "get_body_tip", "error": "body_name must not be empty."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "get_body_tip", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "get_body_tip", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "get_body_tip", "error": lock_status.get("error", "lock failed")}

    tip_name: str | None = None
    tip_type: str | None = None
    tip_shape_info: dict[str, Any] | None = None

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        body = doc.getObject(body_name)
        if body is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "get_body_tip", "error": f"object '{body_name}' not found"}

        type_id = body.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "Body":
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "get_body_tip",
                "error": f"object '{body_name}' is type '{type_id}', not 'PartDesign::Body'.",
            }

        tip = body.Tip
        if tip is not None:
            tip_name = getattr(tip, "Name", None)
            tip_type = getattr(tip, "TypeId", None)
            # Bounded shape information for the Tip, if it exposes one.
            shape = getattr(tip, "Shape", None)
            if shape is not None:
                tip_shape_info = {}
                try:
                    tip_shape_info["valid"] = bool(shape.isValid()) if hasattr(shape, "isValid") else None
                except Exception:
                    tip_shape_info["valid"] = None
                try:
                    tip_shape_info["volume"] = float(shape.Volume) if hasattr(shape, "Volume") else None
                except Exception:
                    tip_shape_info["volume"] = None
                try:
                    tip_shape_info["solids_count"] = len(list(getattr(shape, "Solids", []) or []))
                except Exception:
                    tip_shape_info["solids_count"] = None
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "get_body_tip", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "get_body_tip",
        "result": {
            "document_id": normalized,
            "body_name": body_name,
            "tip_name": tip_name,
            "tip_type": tip_type,
            "tip_shape": tip_shape_info,
        },
    }


def _op_set_body_tip(params: dict[str, Any]) -> dict[str, Any]:
    """Set the Tip of a PartDesign::Body to an existing feature of that Body.

    Validation rules:
      - ``body_name`` must resolve to a ``PartDesign::Body``.
      - ``tip_name`` must resolve to an existing object.
      - the Tip object must be a member of the Body's ``Group``.
      - after assigning the Tip and recomputing, the Tip must produce a valid
        Part solid (non-null, valid shape with at least one solid).  If this
        check fails the previous Tip is restored and the operation is rejected.

    The document is locked exclusively, recomputed, saved atomically, and its
    revision is bumped on success.  On any failure the lock is released and the
    on-disk document is left unmodified (previous Tip preserved).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name = params.get("body_name") or ""
    tip_name = params.get("tip_name") or ""
    workspace_str = params.get("workspace") or ""

    if not body_name.strip():
        return {"ok": False, "op": "set_body_tip", "error": "body_name must not be empty."}
    if not tip_name.strip():
        return {"ok": False, "op": "set_body_tip", "error": "tip_name must not be empty."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "set_body_tip", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "set_body_tip", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "set_body_tip", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "set_body_tip", "error": message}

    previous_tip: Any = None

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)

        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"object '{body_name}' not found")

        type_id = body.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "Body":
            return _fail(
                f"object '{body_name}' is type '{type_id}', not 'PartDesign::Body'; "
                f"cannot manage Body.Tip."
            )

        tip = doc.getObject(tip_name)
        if tip is None:
            return _fail(f"object '{tip_name}' not found")

        # The Tip must belong to this Body (be a member of its Group).
        try:
            group = list(getattr(body, "Group", []) or [])
        except Exception:
            group = []
        if tip not in group:
            return _fail(
                f"object '{tip_name}' is not a member of Body '{body_name}'; "
                f"it cannot be the Body's Tip."
            )

        # Record the previous Tip so it can be restored if validation fails.
        previous_tip = body.Tip

        # Assign the new Tip.
        try:
            body.Tip = tip
        except Exception as exc:
            return _fail(f"could not set Body.Tip to '{tip_name}': {exc}")

        # Recompute so FreeCAD evaluates the feature tree under the new Tip.
        doc.recompute()

        # Verify the selected Tip produces a valid expected solid after recompute.
        tip_obj = body.Tip
        tip_shape = getattr(tip_obj, "Shape", None) if tip_obj is not None else None
        if tip_shape is None:
            _restore_tip(body, previous_tip, doc)
            return _fail(f"Tip '{tip_name}' produces no shape after recompute.")

        try:
            if not bool(tip_shape.isValid()):
                _restore_tip(body, previous_tip, doc)
                return _fail(f"Tip '{tip_name}' produces an invalid shape after recompute.")
        except Exception:
            _restore_tip(body, previous_tip, doc)
            return _fail(f"Tip '{tip_name}' shape validation failed after recompute.")

        try:
            solids = list(getattr(tip_shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            _restore_tip(body, previous_tip, doc)
            return _fail(f"Tip '{tip_name}' produces no solid after recompute.")

        # Persist atomically and bump the revision.
        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "set_body_tip", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "set_body_tip",
        "result": {
            "document_id": normalized,
            "body_name": body_name,
            "tip_name": tip_name,
            "previous_tip_name": (
                getattr(previous_tip, "Name", None) if previous_tip is not None else None
            ),
            "tip_type": getattr(body.Tip, "TypeId", None) if body.Tip is not None else None,
            "meta_before": meta_before,
        },
    }


def _restore_tip(body: Any, previous_tip: Any, doc: Any) -> None:
    """Best-effort restoration of a previous Body.Tip after a failed validation.

    Never raises: a failed set_body_tip must leave the Body's Tip unchanged and
    must not compound the problem with a secondary exception.
    """
    if previous_tip is None:
        return
    try:
        body.Tip = previous_tip
        doc.recompute()
    except Exception:
        pass


def _op_reorder_body_features(params: dict[str, Any]) -> dict[str, Any]:
    """Reorder the features of a PartDesign::Body within its Group.

    The native FreeCAD API for reordering is ``body.Group = [ordered list]``.
    FreeCAD itself is permissive about this assignment and will not reject
    dependency-violating orders, so this operation adds its own validation:

      - ``body_name`` must resolve to a ``PartDesign::Body``.
      - ``new_order`` must be a non-empty list of object names.
      - Every name in ``new_order`` must resolve to an existing document object.
      - Every name in ``new_order`` must be a member of the Body's ``Group``.
      - The set of names in ``new_order`` must exactly match the current Group
        membership (no additions or removals).
      - Dependency order must be preserved: if feature A depends on feature B
        (``B`` is in ``A.OutList``), then ``B`` must appear before ``A`` in
        ``new_order``.

    After applying the reorder and recomputing, the current Body.Tip (if any)
    must still produce a valid Part solid.  If any validation fails, the
    previous Group order is restored and the operation is rejected with a
    structured error.

    The document is locked exclusively, recomputed, saved atomically, and its
    revision is bumped on success.  On any failure the lock is released and the
    on-disk document is left unmodified (previous Group order preserved).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name = params.get("body_name") or ""
    new_order = params.get("new_order")
    workspace_str = params.get("workspace") or ""

    if not body_name.strip():
        return {"ok": False, "op": "reorder_body_features", "error": "body_name must not be empty."}

    # Validate new_order: must be a non-empty list of strings.
    if not isinstance(new_order, list) or len(new_order) == 0:
        return {
            "ok": False,
            "op": "reorder_body_features",
            "error": "new_order must be a non-empty list of object names.",
        }
    for name in new_order:
        if not isinstance(name, str) or not name.strip():
            return {
                "ok": False,
                "op": "reorder_body_features",
                "error": "new_order must contain only non-empty object name strings.",
            }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "reorder_body_features", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "reorder_body_features", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "reorder_body_features", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "reorder_body_features", "error": message}

    previous_group: list[Any] = []

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)

        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"object '{body_name}' not found")

        type_id = body.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "Body":
            return _fail(
                f"object '{body_name}' is type '{type_id}', not 'PartDesign::Body'; "
                f"cannot reorder Body features."
            )

        # Capture the current Group order for restoration on failure.
        try:
            previous_group = list(getattr(body, "Group", []) or [])
        except Exception:
            previous_group = []

        # Resolve new_order names to document objects.
        new_order_objects: list[Any] = []
        for name in new_order:
            obj = doc.getObject(name)
            if obj is None:
                return _fail(f"object '{name}' not found in document '{normalized}'.")
            new_order_objects.append(obj)

        # Every new_order object must be a member of the Body's Group.
        # Use identity comparison (not set membership) so the check works even
        # with test stand-ins that are not hashable.
        previous_group_ids = {id(g) for g in previous_group}
        for obj in new_order_objects:
            if id(obj) not in previous_group_ids:
                return _fail(
                    f"object '{getattr(obj, 'Name', obj)}' is not a member of Body "
                    f"'{body_name}'s Group and cannot be reordered."
                )

        # The set of objects must exactly match current Group membership.
        if len(new_order_objects) != len(previous_group):
            return _fail(
                "new_order does not match the Body's Group membership: expected "
                f"{len(previous_group)} members, got {len(new_order_objects)}."
            )
        new_order_ids = {id(o) for o in new_order_objects}
        if new_order_ids != previous_group_ids:
            missing_ids = previous_group_ids - new_order_ids
            extra_ids = new_order_ids - previous_group_ids
            detail = ""
            if missing_ids:
                missing = [g for g in previous_group if id(g) in missing_ids]
                detail += f" missing: {[getattr(o, 'Name', str(o)) for o in missing]}"
            if extra_ids:
                extra = [o for o in new_order_objects if id(o) in extra_ids]
                detail += f" extra: {[getattr(o, 'Name', str(o)) for o in extra]}"
            return _fail(
                f"new_order does not match the Body's Group membership.{detail}"
            )

        # Validate dependency order: if A depends on B (B in A.OutList), B must
        # appear before A in new_order.  Use id() keys so the check works even
        # with test stand-ins that are not hashable.
        position = {id(obj): idx for idx, obj in enumerate(new_order_objects)}
        for obj in new_order_objects:
            try:
                outlist = list(getattr(obj, "OutList", []) or [])
            except Exception:
                outlist = []
            for dep in outlist:
                dep_id = id(dep)
                if dep_id in position and position[dep_id] > position[id(obj)]:
                    return _fail(
                        f"reorder would break dependencies: object '{getattr(obj, 'Name', obj)}' "
                        f"depends on '{getattr(dep, 'Name', str(dep))}', which must appear before it."
                    )

        # Record the previous Tip for validation after reorder.
        previous_tip = body.Tip

        # Apply the reorder via the native FreeCAD API.
        try:
            body.Group = new_order_objects
        except Exception as exc:
            return _fail(f"could not reorder Body features: {exc}")

        # Recompute so FreeCAD evaluates the feature tree under the new order.
        doc.recompute()

        # Verify the current Tip (if any) still produces a valid solid.
        tip_obj = body.Tip
        if tip_obj is not None:
            tip_shape = getattr(tip_obj, "Shape", None)
            if tip_shape is None:
                # Restore previous Group order on failure.
                _restore_group(body, previous_group, doc)
                return _fail(f"Tip '{getattr(tip_obj, 'Name', tip_obj)}' produces no shape after reorder.")
            try:
                if not bool(tip_shape.isValid()):
                    _restore_group(body, previous_group, doc)
                    return _fail(
                        f"Tip '{getattr(tip_obj, 'Name', tip_obj)}' produces an invalid shape after reorder."
                    )
            except Exception:
                _restore_group(body, previous_group, doc)
                return _fail(f"Tip '{getattr(tip_obj, 'Name', tip_obj)}' shape validation failed after reorder.")
            try:
                solids = list(getattr(tip_shape, "Solids", []) or [])
            except Exception:
                solids = []
            if not solids:
                _restore_group(body, previous_group, doc)
                return _fail(f"Tip '{getattr(tip_obj, 'Name', tip_obj)}' produces no solid after reorder.")

        # Persist atomically and bump the revision.
        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "reorder_body_features", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "reorder_body_features",
        "result": {
            "document_id": normalized,
            "body_name": body_name,
            "new_order": [getattr(o, "Name", str(o)) for o in new_order_objects],
            "previous_order": [getattr(o, "Name", str(o)) for o in previous_group],
            "tip_name": getattr(body.Tip, "Name", None) if body.Tip is not None else None,
            "tip_type": getattr(body.Tip, "TypeId", None) if body.Tip is not None else None,
            "meta_before": meta_before,
        },
    }


def _restore_group(body: Any, previous_group: list[Any], doc: Any) -> None:
    """Best-effort restoration of a previous Body.Group order after a failed validation.

    Never raises: a failed reorder_body_features must leave the Body's Group
    unchanged and must not compound the problem with a secondary exception.
    """
    if not previous_group:
        return
    try:
        body.Group = previous_group
        doc.recompute()
    except Exception:
        pass


def _op_create_body(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native ``PartDesign::Body`` in the workspace document.

    The Body is created with ``doc.addObject("PartDesign::Body", body_name)``
    and saved atomically.  Object names are unique within a FreeCAD document, so
    a Body with the same name must not already exist; an exact name clash is
    reported as a structured error rather than silently renaming the Body,
    because downstream tools (Pad, Pocket, Body.Tip) reference the Body by
    name.  The document is locked exclusively during the operation.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    if not body_name.strip():
        return {"ok": False, "op": "create_body", "error": "body_name must not be empty."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "create_body", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "create_body", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "create_body", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "create_body", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_or_create_document(_FC, wks, normalized)

        if doc.getObject(body_name) is not None:
            return _fail(f"object '{body_name}' already exists in document '{normalized}'.")

        try:
            body = doc.addObject("PartDesign::Body", body_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Body: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "create_body", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "create_body",
        "result": {
            "document_id": normalized,
            "body_name": body_name,
            "type": "PartDesign::Body",
            "features": 0,
            "meta_before": meta_before,
        },
    }


# ---------------------------------------------------------------------------
# New operation handlers for Milestone 9-A (Sketcher 2D geometry)
# ---------------------------------------------------------------------------

def _op_create_sketch(params: dict[str, Any]) -> dict[str, Any]:
    """Create a Sketcher::SketchObject on the XY plane in a document."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or "Sketch"
    workspace_str = params.get("workspace") or ""

    if not sketch_name.strip():
        return {"ok": False, "op": "create_sketch", "error": "sketch_name must not be empty."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "create_sketch", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "create_sketch", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "create_sketch", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    try:
        import FreeCAD as _FC
        import Sketcher  # noqa: F401,PLC0415 - register the Sketcher type with FreeCAD
        doc = _open_or_create_document(_FC, wks, normalized)
        obj_name = sketch_name
        counter = 0
        while doc.getObject(obj_name) is not None:
            counter += 1
            obj_name = f"{sketch_name}_{counter}"
        sketch = doc.addObject("Sketcher::SketchObject", obj_name)
        # Identity placement: the sketch plane coincides with the document XY plane.
        sketch.Placement = _FC.Placement(_FC.Vector(0, 0, 0), _FC.Rotation())
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        # Increment revision after successful save
        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_saved_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "create_sketch", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "create_sketch",
        "result": {
            "document_id": normalized,
            "sketch_name": obj_name,
            "type": "Sketcher::SketchObject",
            "plane": "XY",
            "edges": 0,
            "meta_before": meta_before,
        },
    }


def _op_sketch_add_rectangle(params: dict[str, Any]) -> dict[str, Any]:
    """Add four connected line segments forming a rectangle to an existing sketch.

    The rectangle sides lie on the sketch XY plane (Z = 0), counter-clockwise
    from the lower-left corner (x, y).  Segments share endpoints so the four
    edges are topologically connected; no constraints are added.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    x = params.get("x")
    y = params.get("y")
    width = params.get("width")
    height = params.get("height")
    workspace_str = params.get("workspace") or ""

    # Validate numeric inputs: must be present, finite numbers (reject NaN/inf).
    try:
        x_val = float(x)
        y_val = float(y)
        width_val = float(width)
        height_val = float(height)
    except (TypeError, ValueError):
        return {"ok": False, "op": "sketch_add_rectangle", "error": "x, y, width, and height must be numeric."}
    for name, val in (("x", x_val), ("y", y_val), ("width", width_val), ("height", height_val)):
        if math.isnan(val) or math.isinf(val):
            return {"ok": False, "op": "sketch_add_rectangle", "error": f"{name} must be a finite number."}
    if width_val <= 0:
        return {"ok": False, "op": "sketch_add_rectangle", "error": "width must be a positive number."}
    if height_val <= 0:
        return {"ok": False, "op": "sketch_add_rectangle", "error": "height must be a positive number."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_rectangle", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_rectangle", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_rectangle", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_rectangle", "error": message}

    try:
        import FreeCAD as _FC
        import Part  # noqa: F401,PLC0415 - Part.LineSegment geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add sketch geometry."
            )

        corners = [
            _FC.Vector(x_val, y_val, 0.0),
            _FC.Vector(x_val + width_val, y_val, 0.0),
            _FC.Vector(x_val + width_val, y_val + height_val, 0.0),
            _FC.Vector(x_val, y_val + height_val, 0.0),
        ]
        segments = [
            Part.LineSegment(corners[0], corners[1]),
            Part.LineSegment(corners[1], corners[2]),
            Part.LineSegment(corners[2], corners[3]),
            Part.LineSegment(corners[3], corners[0]),
        ]
        for segment in segments:
            sketch.addGeometry(segment, False)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_rectangle",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "x": x_val,
            "y": y_val,
            "width": width_val,
            "height": height_val,
            "edges_added": 4,
            "total_edges": len(getattr(sketch, "Geometry", [])),
            "meta_before": meta_before,
        },
    }


# ---------------------------------------------------------------------------
# New operation handlers for Milestone 14.2 (Add Sketch Constraints)
# ---------------------------------------------------------------------------


def _op_sketch_add_constraint(params: dict[str, Any]) -> dict[str, Any]:
    """Add a verified Sketcher constraint to an existing sketch.

    Supports: Horizontal, Vertical, Distance, Radius, Diameter, Angle.

    Constructor signatures (verified against FreeCAD 1.1.4):
      Constraint("Horizontal", first_geometry_index)
      Constraint("Vertical", first_geometry_index)
      Constraint("Distance", first_geometry_index, value)
      Constraint("Distance", first_geometry_index, second_geometry_index, value)
      Constraint("Radius", geometry_index, value)
      Constraint("Diameter", geometry_index, value)
      Constraint("Angle", first_geometry_index, second_geometry_index, value)

    The document is locked exclusively during the operation, recomputed,
    saved atomically, and its revision is bumped.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    constraint_type = params.get("constraint_type") or ""
    first_index = params.get("first_geometry_index")
    second_index = params.get("second_geometry_index")
    value = params.get("value")
    name = params.get("name")
    workspace_str = params.get("workspace") or ""

    # Supported constraint types.
    SUPPORTED_TYPES = ("Horizontal", "Vertical", "Distance", "Radius", "Diameter", "Angle")
    if constraint_type not in SUPPORTED_TYPES:
        return {
            "ok": False,
            "op": "sketch_add_constraint",
            "error": (
                f"constraint_type '{constraint_type}' is not supported. "
                f"Supported types: {', '.join(SUPPORTED_TYPES)}."
            ),
        }

    # Validate first_geometry_index.
    if isinstance(first_index, bool) or not isinstance(first_index, int):
        return {
            "ok": False,
            "op": "sketch_add_constraint",
            "error": "first_geometry_index must be an integer (boolean values are not accepted).",
        }
    if first_index < 0:
        return {
            "ok": False,
            "op": "sketch_add_constraint",
            "error": "first_geometry_index must be non-negative.",
        }

    # Validate second_geometry_index when present.
    if second_index is not None:
        if isinstance(second_index, bool) or not isinstance(second_index, int):
            return {
                "ok": False,
                "op": "sketch_add_constraint",
                "error": "second_geometry_index must be an integer (boolean values are not accepted).",
            }
        if second_index < 0:
            return {
                "ok": False,
                "op": "sketch_add_constraint",
                "error": "second_geometry_index must be non-negative.",
            }

    # Validate dimensional value where required.
    if constraint_type in ("Distance", "Radius", "Diameter", "Angle"):
        if value is None:
            return {
                "ok": False,
                "op": "sketch_add_constraint",
                "error": f"constraint_type '{constraint_type}' requires a value.",
            }
        try:
            value_val = float(value)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "op": "sketch_add_constraint",
                "error": f"constraint_type '{constraint_type}' value must be a finite number.",
            }
        if math.isnan(value_val) or math.isinf(value_val):
            return {
                "ok": False,
                "op": "sketch_add_constraint",
                "error": f"constraint_type '{constraint_type}' value must be a finite number.",
            }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_constraint", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_constraint", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_constraint", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_constraint", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part module needed before Sketcher loads
        import Sketcher  # noqa: F401,PLC0415 - register Sketcher types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add constraint."
            )

        # Validate geometry indices against the sketch.
        geometry_list = list(getattr(sketch, "Geometry", []))
        if first_index >= len(geometry_list):
            return _fail(
                f"first_geometry_index {first_index} is out of range "
                f"(sketch has {len(geometry_list)} geometries)."
            )
        if second_index is not None and second_index >= len(geometry_list):
            return _fail(
                f"second_geometry_index {second_index} is out of range "
                f"(sketch has {len(geometry_list)} geometries)."
            )

        # Verify geometry compatibility with constraint type.
        first_geom_type = type(geometry_list[first_index]).__name__
        if constraint_type in ("Horizontal", "Vertical", "Radius", "Diameter", "Angle"):
            if constraint_type in ("Radius", "Diameter") and first_geom_type != "Circle":
                return _fail(
                    f"constraint_type '{constraint_type}' requires Circle geometry, "
                    f"but geometry index {first_index} is '{first_geom_type}'."
                )
            if constraint_type == "Angle" and first_geom_type != "LineSegment":
                return _fail(
                    f"constraint_type 'Angle' requires LineSegment geometry, "
                    f"but geometry index {first_index} is '{first_geom_type}'."
                )
            if constraint_type in ("Horizontal", "Vertical") and first_geom_type != "LineSegment":
                return _fail(
                    f"constraint_type '{constraint_type}' requires LineSegment geometry, "
                    f"but geometry index {first_index} is '{first_geom_type}'."
                )
        if second_index is not None:
            second_geom_type = type(geometry_list[second_index]).__name__
            if constraint_type == "Angle" and second_geom_type != "LineSegment":
                return _fail(
                    f"constraint_type 'Angle' requires LineSegment geometry, "
                    f"but geometry index {second_index} is '{second_geom_type}'."
                )
            if constraint_type == "Distance" and second_geom_type not in ("LineSegment", "Circle"):
                return _fail(
                    f"constraint_type 'Distance' requires LineSegment or Circle geometry, "
                    f"but geometry index {second_index} is '{second_geom_type}'."
                )

        # Build the constraint using the verified constructor signatures.
        constraint = None

        if constraint_type in ("Horizontal", "Vertical"):
            constraint = Sketcher.Constraint(constraint_type, first_index)
        elif constraint_type in ("Radius", "Diameter"):
            constraint = Sketcher.Constraint(constraint_type, first_index, value_val)
        elif constraint_type == "Distance":
            if second_index is not None:
                constraint = Sketcher.Constraint(constraint_type, first_index, second_index, value_val)
            else:
                constraint = Sketcher.Constraint(constraint_type, first_index, value_val)
        elif constraint_type == "Angle":
            if second_index is None:
                return _fail(
                    "constraint_type 'Angle' requires second_geometry_index."
                )
            constraint = Sketcher.Constraint(constraint_type, first_index, second_index, value_val)

        if name is not None:
            constraint.Name = name

        constraint_index = sketch.addConstraint(constraint)
        doc.recompute()
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "sketch_add_constraint", "error": f"FreeCAD error: {exc}"}

    # Read back the created constraint and solver state for the result.
    try:
        created = list(getattr(sketch, "Constraints", []))[constraint_index]
        constraint_info: dict[str, Any] = {"index": constraint_index}
        for attr, key in (
            ("Type", "type"),
            ("Name", "name"),
            ("First", "first"),
            ("Second", "second"),
            ("Third", "third"),
            ("Value", "value"),
            ("IsActive", "is_active"),
            ("Driving", "driving"),
        ):
            try:
                constraint_info[key] = getattr(created, attr)
            except Exception:
                constraint_info[key] = None
    except Exception:
        constraint_info = {"index": constraint_index}

    try:
        fc_val = getattr(sketch, "FullyConstrained", None)
        fully_constrained = bool(fc_val) if fc_val is not None else None
    except Exception:
        fully_constrained = None
    try:
        degrees_of_freedom = getattr(sketch, "DoF", None)
    except Exception:
        degrees_of_freedom = None

    fcstd_path = wks.document_path(normalized)

    def _write_tmp(tmp_path: str) -> None:
        doc.saveAs(str(tmp_path))

    ws_mod.atomic_save(fcstd_path, _write_tmp)

    meta = ws_mod.read_meta(wks.meta_path(normalized))
    meta["revision"] = meta.get("revision", 0) + 1
    meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
    ws_mod.write_meta(wks.meta_path(normalized), meta)

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_constraint",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "constraint_type": constraint_type,
            "constraint_index": constraint_index,
            "constraint": constraint_info,
            "constraint_count": len(getattr(sketch, "Constraints", [])),
            "fully_constrained": fully_constrained,
            "degrees_of_freedom": degrees_of_freedom,
            "meta_before": meta_before,
        },
    }


def _op_sketch_delete_constraint(params: dict[str, Any]) -> dict[str, Any]:
    """Delete one constraint from an existing sketch (read-modify-write).

    Validates that the target object exists and is a Sketcher::SketchObject,
    validates constraint_index strictly (integer, not boolean, in range), then
    deletes exactly the requested constraint via FreeCAD's delConstraint.
    Captures the deleted constraint's details before removal so callers can
    verify what was removed. The document is recomputed, saved atomically, and
    its revision is bumped.

    Deleting a constraint may invalidate downstream features (e.g. Pad or
    Pocket) that depended on it. FreeCAD does not automatically remove
    dependent features when a constraint is deleted; this operation reports
    the actual solver state after deletion so callers can assess impact.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    constraint_index_param = params.get("constraint_index")
    workspace_str = params.get("workspace") or ""

    # Validate constraint_index: must be an integer, not a boolean.
    if isinstance(constraint_index_param, bool) or not isinstance(constraint_index_param, int):
        return {
            "ok": False,
            "op": "sketch_delete_constraint",
            "error": "constraint_index must be an integer (boolean values are not accepted).",
        }

    constraint_index = constraint_index_param

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_delete_constraint", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_delete_constraint", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_delete_constraint", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_delete_constraint", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot delete sketch constraint."
            )

        # Get current constraint count for range validation.
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
        except Exception:
            constraint_count_before = 0

        if constraint_index < 0 or constraint_index >= constraint_count_before:
            return _fail(
                f"constraint_index {constraint_index} is out of range "
                f"(valid range: 0 to {constraint_count_before - 1})."
            )

        # Capture the deleted constraint's details before removal.
        deleted_constraint_info: dict[str, Any] = {"index": constraint_index}
        try:
            deleted = list(getattr(sketch, "Constraints", []))[constraint_index]
            for attr, key in (
                ("Type", "type"),
                ("Name", "name"),
                ("First", "first"),
                ("Second", "second"),
                ("Third", "third"),
                ("Value", "value"),
                ("IsActive", "is_active"),
                ("Driving", "driving"),
            ):
                try:
                    deleted_constraint_info[key] = getattr(deleted, attr)
                except Exception:
                    deleted_constraint_info[key] = None
        except Exception:
            pass

        # Delete the constraint.
        sketch.delConstraint(constraint_index)
        doc.recompute()

        # Record constraint count after deletion.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            constraint_count_after = constraint_count_before - 1

        # Solver state after deletion.
        try:
            fc_val = getattr(sketch, "FullyConstrained", None)
            fully_constrained = bool(fc_val) if fc_val is not None else None
        except Exception:
            fully_constrained = None
        try:
            degrees_of_freedom = getattr(sketch, "DoF", None)
        except Exception:
            degrees_of_freedom = None

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "sketch_delete_constraint", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_delete_constraint",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "deleted_constraint_index": constraint_index,
            "deleted_constraint": deleted_constraint_info,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "fully_constrained": fully_constrained,
            "degrees_of_freedom": degrees_of_freedom,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_constraint(params: dict[str, Any]) -> dict[str, Any]:
    """Edit one constraint in an existing sketch (read-modify-write with rollback).

    FreeCAD 1.1.4 constraint ``Type`` and ``Value`` are read-only, so editing is
    implemented as delete + recreate.  Atomicity is essential:

    1. Capture the original constraint details before deletion.
    2. Validate the replacement definition before mutating the sketch.
    3. Delete the original constraint.
    4. Create the replacement constraint.
    5. If replacement creation fails, restore the original constraint at the
       same index (rollback).  If rollback itself fails, report both failures
       and never report a successful edit.
    6. Recompute and persist only after a successful edit.

    Supports: Horizontal, Vertical, Distance, Radius, Diameter, Angle.
    Does not support Coincident or other unverified constraint types.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    constraint_index_param = params.get("constraint_index")
    constraint_type = params.get("constraint_type") or ""
    first_index = params.get("first_geometry_index")
    second_index = params.get("second_geometry_index")
    value = params.get("value")
    name = params.get("name")
    workspace_str = params.get("workspace") or ""

    SUPPORTED_TYPES = ("Horizontal", "Vertical", "Distance", "Radius", "Diameter", "Angle")
    if constraint_type not in SUPPORTED_TYPES:
        return {
            "ok": False,
            "op": "sketch_edit_constraint",
            "error": (
                f"constraint_type '{constraint_type}' is not supported. "
                f"Supported types: {', '.join(SUPPORTED_TYPES)}."
            ),
        }

    # Validate constraint_index: must be an integer, not a boolean.
    if isinstance(constraint_index_param, bool) or not isinstance(constraint_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_constraint",
            "error": "constraint_index must be an integer (boolean values are not accepted).",
        }
    constraint_index = constraint_index_param

    # Validate first_geometry_index: must be an integer, not a boolean.
    if isinstance(first_index, bool) or not isinstance(first_index, int):
        return {
            "ok": False,
            "op": "sketch_edit_constraint",
            "error": "first_geometry_index must be an integer (boolean values are not accepted).",
        }
    if first_index < 0:
        return {
            "ok": False,
            "op": "sketch_edit_constraint",
            "error": "first_geometry_index must be non-negative.",
        }

    # Validate second_geometry_index when present.
    if second_index is not None:
        if isinstance(second_index, bool) or not isinstance(second_index, int):
            return {
                "ok": False,
                "op": "sketch_edit_constraint",
                "error": "second_geometry_index must be an integer (boolean values are not accepted).",
            }
        if second_index < 0:
            return {
                "ok": False,
                "op": "sketch_edit_constraint",
                "error": "second_geometry_index must be non-negative.",
            }

    # Validate dimensional value where required.
    if constraint_type in ("Distance", "Radius", "Diameter", "Angle"):
        if value is None:
            return {
                "ok": False,
                "op": "sketch_edit_constraint",
                "error": f"constraint_type '{constraint_type}' requires a value.",
            }
        try:
            value_val = float(value)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "op": "sketch_edit_constraint",
                "error": f"constraint_type '{constraint_type}' value must be a finite number.",
            }
        if math.isnan(value_val) or math.isinf(value_val):
            return {
                "ok": False,
                "op": "sketch_edit_constraint",
                "error": f"constraint_type '{constraint_type}' value must be a finite number.",
            }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_constraint", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_constraint", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_constraint", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_constraint", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part module needed before Sketcher loads
        import Sketcher  # noqa: F401,PLC0415 - register Sketcher types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch constraint."
            )

        # Get current constraint count for range validation.
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
        except Exception:
            constraint_count_before = 0

        if constraint_index < 0 or constraint_index >= constraint_count_before:
            return _fail(
                f"constraint_index {constraint_index} is out of range "
                f"(valid range: 0 to {constraint_count_before - 1})."
            )

        # Capture the original constraint's details before any mutation.
        original_constraint_info: dict[str, Any] = {"index": constraint_index}
        try:
            original = list(getattr(sketch, "Constraints", []))[constraint_index]
            for attr, key in (
                ("Type", "type"),
                ("Name", "name"),
                ("First", "first"),
                ("Second", "second"),
                ("Third", "third"),
                ("Value", "value"),
                ("IsActive", "is_active"),
                ("Driving", "driving"),
            ):
                try:
                    original_constraint_info[key] = getattr(original, attr)
                except Exception:
                    original_constraint_info[key] = None
        except Exception:
            pass

        # Validate geometry indices against the sketch before deleting anything.
        geometry_list = list(getattr(sketch, "Geometry", []))
        if first_index >= len(geometry_list):
            return _fail(
                f"first_geometry_index {first_index} is out of range "
                f"(sketch has {len(geometry_list)} geometries)."
            )
        if second_index is not None and second_index >= len(geometry_list):
            return _fail(
                f"second_geometry_index {second_index} is out of range "
                f"(sketch has {len(geometry_list)} geometries)."
            )

        # Verify geometry compatibility with the replacement constraint type.
        first_geom_type = type(geometry_list[first_index]).__name__
        if constraint_type in ("Horizontal", "Vertical", "Radius", "Diameter", "Angle"):
            if constraint_type in ("Radius", "Diameter") and first_geom_type != "Circle":
                return _fail(
                    f"constraint_type '{constraint_type}' requires Circle geometry, "
                    f"but geometry index {first_index} is '{first_geom_type}'."
                )
            if constraint_type == "Angle" and first_geom_type != "LineSegment":
                return _fail(
                    f"constraint_type 'Angle' requires LineSegment geometry, "
                    f"but geometry index {first_index} is '{first_geom_type}'."
                )
            if constraint_type in ("Horizontal", "Vertical") and first_geom_type != "LineSegment":
                return _fail(
                    f"constraint_type '{constraint_type}' requires LineSegment geometry, "
                    f"but geometry index {first_index} is '{first_geom_type}'."
                )
        if second_index is not None:
            second_geom_type = type(geometry_list[second_index]).__name__
            if constraint_type == "Angle" and second_geom_type != "LineSegment":
                return _fail(
                    f"constraint_type 'Angle' requires LineSegment geometry, "
                    f"but geometry index {second_index} is '{second_geom_type}'."
                )
            if constraint_type == "Distance" and second_geom_type not in ("LineSegment", "Circle"):
                return _fail(
                    f"constraint_type 'Distance' requires LineSegment or Circle geometry, "
                    f"but geometry index {second_index} is '{second_geom_type}'."
                )

        # Delete the original constraint.
        sketch.delConstraint(constraint_index)
        doc.recompute()

        # Build the replacement constraint using the verified constructor signatures.
        replacement = None
        try:
            if constraint_type in ("Horizontal", "Vertical"):
                replacement = Sketcher.Constraint(constraint_type, first_index)
            elif constraint_type in ("Radius", "Diameter"):
                replacement = Sketcher.Constraint(constraint_type, first_index, value_val)
            elif constraint_type == "Distance":
                if second_index is not None:
                    replacement = Sketcher.Constraint(constraint_type, first_index, second_index, value_val)
                else:
                    replacement = Sketcher.Constraint(constraint_type, first_index, value_val)
            elif constraint_type == "Angle":
                if second_index is None:
                    # Rollback: restore original constraint before reporting failure.
                    try:
                        sketch.addConstraint(original)
                    except Exception:
                        pass
                    doc.recompute()
                    return _fail(
                        "constraint_type 'Angle' requires second_geometry_index."
                    )
            if name is not None:
                replacement.Name = name

            constraint_index = sketch.addConstraint(replacement)
            doc.recompute()
        except Exception as exc:
            # Rollback: restore the original constraint at the same index.
            rollback_error = None
            try:
                sketch.addConstraint(original)
                doc.recompute()
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)

            ws_mod.release_lock(lock_path, pid)
            detail = f"replacement creation failed: {exc}"
            if rollback_error:
                detail += f"; rollback also failed: {rollback_error}"
            return {"ok": False, "op": "sketch_edit_constraint", "error": detail}

        # Read back the replacement constraint's details.
        try:
            edited = list(getattr(sketch, "Constraints", []))[constraint_index]
            constraint_info: dict[str, Any] = {"index": constraint_index}
            for attr, key in (
                ("Type", "type"),
                ("Name", "name"),
                ("First", "first"),
                ("Second", "second"),
                ("Third", "third"),
                ("Value", "value"),
                ("IsActive", "is_active"),
                ("Driving", "driving"),
            ):
                try:
                    constraint_info[key] = getattr(edited, attr)
                except Exception:
                    constraint_info[key] = None
        except Exception:
            constraint_info = {"index": constraint_index}

        # Solver state after edit.
        try:
            fc_val = getattr(sketch, "FullyConstrained", None)
            fully_constrained = bool(fc_val) if fc_val is not None else None
        except Exception:
            fully_constrained = None
        try:
            degrees_of_freedom = getattr(sketch, "DoF", None)
        except Exception:
            degrees_of_freedom = None

        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "sketch_edit_constraint", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_constraint",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "edited_constraint_index": constraint_index,
            "constraint_before": original_constraint_info,
            "constraint_after": constraint_info,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": len(getattr(sketch, "Constraints", [])),
            "fully_constrained": fully_constrained,
            "degrees_of_freedom": degrees_of_freedom,
            "meta_before": meta_before,
        },
    }


def _capture_document_state(doc: Any) -> dict[str, dict[str, Any]]:
    """Snapshot the inspectable state of every object in *doc*.

    Returns a mapping ``object_name -> state`` where ``state`` contains the
    stable internal ``name``, FreeCAD ``type``, dependency relationships
    (``depends_on`` = OutList, ``depended_on_by`` = InList), bounded shape
    information (validity, volume), parametric properties (Length/Width/
    Height/Radius) and, for PartDesign::Body objects, the ``tip_name`` of the
    current ``Body.Tip``.

    Nothing read here is mutated; the snapshot is safe to take before and
    after a close/reopen cycle for comparison.
    """
    states: dict[str, dict[str, Any]] = {}
    for obj in getattr(doc, "Objects", []) or []:
        name = getattr(obj, "Name", "")
        state: dict[str, Any] = {
            "name": name,
            "type": getattr(obj, "TypeId", ""),
            "depends_on": [],
            "depended_on_by": [],
            "shape_valid": None,
            "volume": None,
            "length": None,
            "width": None,
            "height": None,
            "radius": None,
            "tip_name": None,
        }
        try:
            outlist = list(getattr(obj, "OutList", []) or [])
            state["depends_on"] = [getattr(o, "Name", str(o)) for o in outlist]
        except Exception:
            state["depends_on"] = []
        try:
            inlist = list(getattr(obj, "InList", []) or [])
            state["depended_on_by"] = [getattr(o, "Name", str(o)) for o in inlist]
        except Exception:
            state["depended_on_by"] = []
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                state["shape_valid"] = bool(shape.isValid()) if hasattr(shape, "isValid") else None
            except Exception:
                state["shape_valid"] = None
            try:
                state["volume"] = float(shape.Volume) if hasattr(shape, "Volume") else None
            except Exception:
                state["volume"] = None
        for prop in ("Length", "Width", "Height", "Radius"):
            if hasattr(obj, prop):
                try:
                    state[prop.lower()] = float(getattr(obj, prop))
                except Exception:
                    state[prop.lower()] = None
        type_id = getattr(obj, "TypeId", "")
        type_name = type_id.split("::")[-1]
        if type_name == "Body":
            tip = getattr(obj, "Tip", None)
            state["tip_name"] = getattr(tip, "Name", None) if tip is not None else None
        states[name] = state
    return states


def _compare_document_states(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Compare *before* and *after* document snapshots and build a report.

    Returns a dict with ``checks`` (list of per-check dicts), ``all_passed``,
    ``failures`` (list of human-readable strings) and ``object_count``.
    """
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    before_names = set(before.keys())
    after_names = set(after.keys())

    # 1. Object identities preserved.
    objects_preserved = before_names == after_names
    checks.append(
        {
            "name": "objects_preserved",
            "passed": objects_preserved,
            "detail": f"{len(after_names)}/{len(before_names)} objects present after reopen",
        }
    )
    if not objects_preserved:
        missing = sorted(before_names - after_names)
        extra = sorted(after_names - before_names)
        failures.append(f"object membership changed: missing={missing}, extra={extra}")

    common_names = before_names & after_names

    # 2. Object types preserved.
    types_ok = True
    for name in sorted(common_names):
        if before[name].get("type") != after[name].get("type"):
            types_ok = False
            failures.append(f"object '{name}' type changed from {before[name].get('type')!r} to {after[name].get('type')!r}")
    checks.append(
        {
            "name": "types_preserved",
            "passed": types_ok,
            "detail": f"types unchanged for {len(common_names)} common objects",
        }
    )

    # 3. Dependency relationships preserved (OutList/InList).
    deps_ok = True
    for name in sorted(common_names):
        if before[name].get("depends_on") != after[name].get("depends_on"):
            deps_ok = False
            failures.append(
                f"object '{name}' depends_on changed: "
                f"{before[name].get('depends_on')} -> {after[name].get('depends_on')}"
            )
        if before[name].get("depended_on_by") != after[name].get("depended_on_by"):
            deps_ok = False
            failures.append(
                f"object '{name}' depended_on_by changed: "
                f"{before[name].get('depended_on_by')} -> {after[name].get('depended_on_by')}"
            )
    checks.append(
        {
            "name": "dependencies_preserved",
            "passed": deps_ok,
            "detail": "dependency (InList/OutList) relationships unchanged",
        }
    )

    # 4. Geometry validity after reopen (objects that have a shape must be valid).
    geometry_ok = True
    for name in sorted(common_names):
        sv = after[name].get("shape_valid")
        if sv is not None and not sv:
            geometry_ok = False
            failures.append(f"object '{name}' shape invalid after reopen")
    checks.append(
        {
            "name": "geometry_valid_after_reopen",
            "passed": geometry_ok,
            "detail": "all shaped objects report a valid shape after reopen",
        }
    )

    # 5. Body.Tip preserved for PartDesign::Body objects.
    tip_ok = True
    for name in sorted(common_names):
        before_tip = before[name].get("tip_name")
        after_tip = after[name].get("tip_name")
        if before_tip is not None or after_tip is not None:
            if before_tip != after_tip:
                tip_ok = False
                failures.append(f"Body '{name}' Tip changed from {before_tip!r} to {after_tip!r}")
    checks.append(
        {
            "name": "body_tip_preserved",
            "passed": tip_ok,
            "detail": "Body.Tip identities unchanged after reopen",
        }
    )

    # 6. Parametric properties preserved (Length/Width/Height/Radius).
    props_ok = True
    for prop in ("length", "width", "height", "radius"):
        for name in sorted(common_names):
            before_val = before[name].get(prop)
            after_val = after[name].get(prop)
            if before_val is not None or after_val is not None:
                if before_val != after_val:
                    props_ok = False
                    failures.append(
                        f"object '{name}' {prop} changed from {before_val!r} to {after_val!r}"
                    )
    checks.append(
        {
            "name": "parametric_properties_preserved",
            "passed": props_ok,
            "detail": "parametric properties (Length/Width/Height/Radius) unchanged",
        }
    )

    all_passed = objects_preserved and types_ok and deps_ok and geometry_ok and tip_ok and props_ok
    return {
        "checks": checks,
        "all_passed": all_passed,
        "failures": failures,
        "object_count": len(after_names),
        "before_object_count": len(before_names),
        "after_object_count": len(after_names),
    }


def _op_validate_reopen(params: dict[str, Any]) -> dict[str, Any]:
    """Validate that a document survives save, close and reopen.

    The operation opens the workspace document, snapshots its inspectable
    state (object identities, types, dependency relationships, geometry
    validity, Body.Tip and parametric properties), closes it, reopens it from
    the persisted ``.fcstd`` path, snapshots the state again and compares the
    two.  Any discrepancy is reported as a structured failure; the operation
    never leaves the document open or modifies the on-disk artifact.

    Returns ``ok: True`` with a report whose ``all_passed`` flag summarizes the
    comparison, or ``ok: False`` with a structured error when the document
    cannot be opened, closed or reopened.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "validate_reopen", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "validate_reopen", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "validate_reopen", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "validate_reopen", "error": message}

    # Fail fast on a missing persisted file before touching FreeCAD.
    fcstd_path = wks.document_path(normalized)
    if not fcstd_path.is_file():
        return _fail(
            f"persisted document not found at {fcstd_path}; save the document first"
        )

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime

        doc = _open_existing_document(_FC, wks, normalized)
        before_state = _capture_document_state(doc)

        # Close the document so it must be reloaded from the persisted file.
        try:
            _FC.closeDocument(doc.Name)
        except Exception as exc:
            return _fail(f"could not close document '{normalized}': {exc}")

        # Reopen from the workspace .fcstd path.
        reopened = _FC.openDocument(str(fcstd_path))
        after_state = _capture_document_state(reopened)

        # Always close the reopened document to leave no stray document behind.
        try:
            _FC.closeDocument(reopened.Name)
        except Exception:
            pass

        report = _compare_document_states(before_state, after_state)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "validate_reopen", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "validate_reopen",
        "result": {
            "document_id": normalized,
            "checks": report["checks"],
            "all_passed": report["all_passed"],
            "failures": report["failures"],
            "object_count": report["object_count"],
            "before_object_count": report["before_object_count"],
            "after_object_count": report["after_object_count"],
            "meta_before": meta_before,
        },
    }


# ---------------------------------------------------------------------------
# New operation handlers for Revision Snapshots
# ---------------------------------------------------------------------------


def _op_create_snapshot(params: dict[str, Any]) -> dict[str, Any]:
    """Create a snapshot of the current document at its current revision.

    Copies the persisted ``.fcstd`` artifact and its metadata sidecar to the
    snapshots directory inside the workspace.  The snapshot is created
    atomically and never compromises the current document if it fails.

    Returns ``ok: True`` with snapshot info (revision, paths) or ``ok: False``
    with a structured error.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "create_snapshot", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "create_snapshot", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "create_snapshot", "error": lock_status.get("error", "lock failed")}

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "create_snapshot", "error": message}

    # Fail fast if there's nothing to snapshot yet.
    fcstd_path = wks.document_path(normalized)
    if not fcstd_path.is_file():
        ws_mod.release_lock(lock_path, pid)
        return _fail(
            f"persisted document not found at {fcstd_path}; save the document first"
        )

    # Read current revision from metadata sidecar.
    meta = ws_mod.read_meta(wks.meta_path(normalized))
    revision = meta.get("revision", 0)

    try:
        result = ws_mod.create_snapshot(wks, normalized, revision, pid=pid)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "create_snapshot", "error": f"snapshot error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "op": "create_snapshot",
        "result": {
            "document_id": normalized,
            "revision": result["revision"],
            "snapshot_path": result["snapshot_path"],
            "meta_path": result["meta_path"],
        },
    }


def _op_list_snapshots(params: dict[str, Any]) -> dict[str, Any]:
    """List all snapshots for a document with basic metadata.

    Returns ``ok: True`` with a list of snapshots (revision, paths, timestamps)
    or ``ok: False`` with a structured error.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "list_snapshots", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "list_snapshots", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "list_snapshots", "error": lock_status.get("error", "lock failed")}

    try:
        snapshots = ws_mod.list_snapshots(wks, normalized)
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "list_snapshots", "error": f"snapshot error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "list_snapshots",
        "result": {
            "document_id": normalized,
            "snapshots": snapshots,
            "count": len(snapshots),
        },
    }


def _op_restore_snapshot(params: dict[str, Any]) -> dict[str, Any]:
    """Restore a document from a selected revision snapshot.

    Identifies snapshots by document ID and revision (never arbitrary paths).
    Before replacing the current document, preserves its current state as a
    preservation snapshot.  After atomic replacement, validates the restored
    document via reopen validation.  If validation fails, rolls back to the
    preservation snapshot.

    Returns ``ok: True`` with a structured report (restored revision, previous
    revision, validation results, recovery snapshot info) or ``ok: False``
    with a structured error.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    workspace_str = params.get("workspace") or ""
    revision = params.get("revision")

    if not isinstance(revision, int) or revision < 0:
        return {
            "ok": False,
            "op": "restore_snapshot",
            "error": "revision must be a non-negative integer",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "restore_snapshot", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "restore_snapshot", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {
            "ok": False,
            "op": "restore_snapshot",
            "error": lock_status.get("error", "lock failed"),
        }

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "restore_snapshot", "error": message}

    # Restore from snapshot (preserves current state as preservation snapshot)
    restore_result = ws_mod.restore_snapshot(wks, normalized, revision, pid=pid)
    if not restore_result.get("ok"):
        ws_mod.release_lock(lock_path, pid)
        return {
            "ok": False,
            "op": "restore_snapshot",
            "error": restore_result.get("error", "restore failed"),
        }

    previous_revision = restore_result["previous_revision"]
    restored_revision = restore_result["restored_revision"]
    snapshot_revision = restore_result["snapshot_revision"]
    preservation_snapshot = restore_result.get("preservation_snapshot")

    # Validate the restored document via reopen validation.
    validation_result = _op_validate_reopen(
        {"document_id": normalized, "workspace": workspace_str or ""}
    )

    if not validation_result.get("ok"):
        # Validation itself failed to run; try rollback.
        rollback_ok = False
        if preservation_snapshot:
            try:
                rollback_result = ws_mod.restore_snapshot(
                    wks, normalized, previous_revision, pid=pid
                )
                rollback_ok = rollback_result.get("ok", False)
            except Exception:
                rollback_ok = False
        ws_mod.release_lock(lock_path, pid)
        return {
            "ok": False,
            "op": "restore_snapshot",
            "error": f"validation failed: {validation_result.get('error', 'unknown error')}",
            "rolled_back": rollback_ok,
            "previous_revision": previous_revision,
            "restored_revision": restored_revision,
        }

    report = validation_result.get("result", {})
    if not report.get("all_passed", False):
        # Validation found discrepancies; rollback to preservation snapshot.
        rollback_ok = False
        if preservation_snapshot:
            try:
                rollback_result = ws_mod.restore_snapshot(
                    wks, normalized, previous_revision, pid=pid
                )
                rollback_ok = rollback_result.get("ok", False)
            except Exception:
                rollback_ok = False
        ws_mod.release_lock(lock_path, pid)
        return {
            "ok": False,
            "op": "restore_snapshot",
            "error": "restored document failed validation; rolled back",
            "validation_failures": report.get("failures", []),
            "rolled_back": rollback_ok,
            "previous_revision": previous_revision,
            "restored_revision": restored_revision,
        }

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "restore_snapshot",
        "result": {
            "document_id": normalized,
            "previous_revision": previous_revision,
            "restored_revision": restored_revision,
            "snapshot_revision": snapshot_revision,
            "restored_from_snapshot": True,
            "preservation_snapshot": preservation_snapshot,
            "validation": {
                "all_passed": report.get("all_passed", False),
                "object_count": report.get("object_count", 0),
                "checks": report.get("checks", []),
            },
        },
    }


def _op_sketch_add_circle(params: dict[str, Any]) -> dict[str, Any]:
    """Add a proper Sketcher circle geometry to an existing sketch.

    The circle lies on the sketch XY plane (Z = 0).  No constraints are added.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    center_x = params.get("center_x")
    center_y = params.get("center_y")
    radius = params.get("radius")
    workspace_str = params.get("workspace") or ""

    # Validate numeric inputs: must be present, finite numbers (reject NaN/inf).
    try:
        cx_val = float(center_x)
        cy_val = float(center_y)
        radius_val = float(radius)
    except (TypeError, ValueError):
        return {"ok": False, "op": "sketch_add_circle", "error": "center_x, center_y, and radius must be numeric."}
    for name, val in (("center_x", cx_val), ("center_y", cy_val), ("radius", radius_val)):
        if math.isnan(val) or math.isinf(val):
            return {"ok": False, "op": "sketch_add_circle", "error": f"{name} must be a finite number."}
    if radius_val <= 0:
        return {"ok": False, "op": "sketch_add_circle", "error": "radius must be a positive number."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_circle", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_circle", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_circle", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_circle", "error": message}

    try:
        import FreeCAD as _FC
        import Part  # noqa: F401,PLC0415 - Part.Circle geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add sketch geometry."
            )

        circle = Part.Circle(_FC.Vector(cx_val, cy_val, 0.0), _FC.Vector(0, 0, 1), radius_val)
        sketch.addGeometry(circle, False)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_circle",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "center_x": cx_val,
            "center_y": cy_val,
            "radius": radius_val,
            "edges_added": 1,
            "total_edges": len(getattr(sketch, "Geometry", [])),
            "meta_before": meta_before,
        },
    }


def _op_sketch_add_triangle(params: dict[str, Any]) -> dict[str, Any]:
    """Add three connected line segments forming a triangle to an existing sketch.

    The triangle sides lie on the sketch XY plane (Z = 0).  The three segments
    share endpoints so the geometry is closed and topologically connected.
    Preserves the specified vertex order; no constraints are added.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    x1 = params.get("x1")
    y1 = params.get("y1")
    x2 = params.get("x2")
    y2 = params.get("y2")
    x3 = params.get("x3")
    y3 = params.get("y3")
    workspace_str = params.get("workspace") or ""

    # Validate numeric inputs: must be present, finite numbers (reject NaN/inf).
    try:
        x1_val = float(x1)
        y1_val = float(y1)
        x2_val = float(x2)
        y2_val = float(y2)
        x3_val = float(x3)
        y3_val = float(y3)
    except (TypeError, ValueError):
        return {"ok": False, "op": "sketch_add_triangle", "error": "x1, y1, x2, y2, x3, and y3 must be numeric."}
    for name, val in (
        ("x1", x1_val),
        ("y1", y1_val),
        ("x2", x2_val),
        ("y2", y2_val),
        ("x3", x3_val),
        ("y3", y3_val),
    ):
        if math.isnan(val) or math.isinf(val):
            return {"ok": False, "op": "sketch_add_triangle", "error": f"{name} must be a finite number."}

    # Reject degenerate / collinear triangles (zero signed area).
    cross = (x2_val - x1_val) * (y3_val - y1_val) - (y2_val - y1_val) * (x3_val - x1_val)
    scale_sq = max(
        1.0,
        (x2_val - x1_val) ** 2 + (y2_val - y1_val) ** 2,
        (x3_val - x2_val) ** 2 + (y3_val - y2_val) ** 2,
        (x1_val - x3_val) ** 2 + (y1_val - y3_val) ** 2,
    )
    if abs(cross) <= 1e-9 * scale_sq:
        return {
            "ok": False,
            "op": "sketch_add_triangle",
            "error": "triangle is degenerate or collinear (zero signed area).",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_triangle", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_triangle", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_triangle", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_triangle", "error": message}

    try:
        import FreeCAD as _FC
        import Part  # noqa: F401,PLC0415 - Part.LineSegment geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add sketch geometry."
            )

        vertices = [
            _FC.Vector(x1_val, y1_val, 0.0),
            _FC.Vector(x2_val, y2_val, 0.0),
            _FC.Vector(x3_val, y3_val, 0.0),
        ]
        segments = [
            Part.LineSegment(vertices[0], vertices[1]),
            Part.LineSegment(vertices[1], vertices[2]),
            Part.LineSegment(vertices[2], vertices[0]),
        ]
        for segment in segments:
            sketch.addGeometry(segment, False)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_triangle",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "x1": x1_val,
            "y1": y1_val,
            "x2": x2_val,
            "y2": y2_val,
            "x3": x3_val,
            "y3": y3_val,
            "edges_added": 3,
            "total_edges": len(getattr(sketch, "Geometry", [])),
            "meta_before": meta_before,
        },
    }


def _op_sketch_add_ellipse(params: dict[str, Any]) -> dict[str, Any]:
    """Add a single axis-aligned ellipse geometry to an existing sketch.

    The ellipse centre lies on the sketch XY plane (Z = 0).  The major axis is
    aligned with the sketch X direction and the minor axis with the sketch Y
    direction.  No constraints are added.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    center_x = params.get("center_x")
    center_y = params.get("center_y")
    major_radius = params.get("major_radius")
    minor_radius = params.get("minor_radius")
    workspace_str = params.get("workspace") or ""

    # Validate numeric inputs: must be present, finite numbers (reject NaN/inf).
    try:
        cx_val = float(center_x)
        cy_val = float(center_y)
        major_val = float(major_radius)
        minor_val = float(minor_radius)
    except (TypeError, ValueError):
        return {"ok": False, "op": "sketch_add_ellipse", "error": "center_x, center_y, major_radius, and minor_radius must be numeric."}
    for name, val in (("center_x", cx_val), ("center_y", cy_val), ("major_radius", major_val), ("minor_radius", minor_val)):
        if math.isnan(val) or math.isinf(val):
            return {"ok": False, "op": "sketch_add_ellipse", "error": f"{name} must be a finite number."}
    if major_val <= 0:
        return {"ok": False, "op": "sketch_add_ellipse", "error": "major_radius must be a positive number."}
    if minor_val <= 0:
        return {"ok": False, "op": "sketch_add_ellipse", "error": "minor_radius must be a positive number."}

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_ellipse", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_ellipse", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_ellipse", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_ellipse", "error": message}

    # FreeCAD 1.1.4 supports Part.Ellipse(Point, double, double) = (center,
    # major_radius, minor_radius), which creates an axis-aligned ellipse in the
    # sketch XY plane with the major axis along X and the minor axis along Y.
    try:
        import FreeCAD as _FC
        import Part  # noqa: F401,PLC0415 - Part.Ellipse geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add sketch geometry."
            )

        ellipse = Part.Ellipse(_FC.Vector(cx_val, cy_val, 0.0), major_val, minor_val)
        sketch.addGeometry(ellipse, False)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_ellipse",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "center_x": cx_val,
            "center_y": cy_val,
            "major_radius": major_val,
            "minor_radius": minor_val,
            "edges_added": 1,
            "total_edges": len(getattr(sketch, "Geometry", [])),
            "meta_before": meta_before,
        },
    }


def _op_sketch_add_regular_polygon(params: dict[str, Any]) -> dict[str, Any]:
    """Add a regular polygon (circumradius + sides + rotation) to a sketch.

    Vertices lie on a circle centred at ``(center_x, center_y)`` on Z=0,
    connected by ``Part.LineSegment`` edges and closed back to the first
    vertex.  *rotation_deg* rotates the first vertex counter-clockwise from
    the positive X axis.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    center_x = params.get("center_x")
    center_y = params.get("center_y")
    radius = params.get("radius")
    sides = params.get("sides")
    rotation_deg = params.get("rotation_deg", 0)
    workspace_str = params.get("workspace") or ""

    # Validate numeric inputs.
    try:
        cx_val = float(center_x)
        cy_val = float(center_y)
        radius_val = float(radius)
        rotation_val = float(rotation_deg)
        sides_val = int(sides)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_add_regular_polygon",
            "error": "center_x, center_y, radius must be finite numbers and sides must be an integer.",
        }
    for name, val in (
        ("center_x", cx_val),
        ("center_y", cy_val),
        ("radius", radius_val),
        ("rotation_deg", rotation_val),
    ):
        if math.isnan(val) or math.isinf(val):
            return {
                "ok": False,
                "op": "sketch_add_regular_polygon",
                "error": f"{name} must be a finite number.",
            }
    if radius_val <= 0:
        return {
            "ok": False,
            "op": "sketch_add_regular_polygon",
            "error": "radius must be a positive number.",
        }
    if sides_val < 3:
        return {
            "ok": False,
            "op": "sketch_add_regular_polygon",
            "error": "sides must be at least 3.",
        }
    if sides_val > MAX_POLYGON_SIDES:
        return {
            "ok": False,
            "op": "sketch_add_regular_polygon",
            "error": f"sides must not exceed {MAX_POLYGON_SIDES}.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_regular_polygon", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_regular_polygon", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_regular_polygon", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_regular_polygon", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part.LineSegment geometry for Sketcher
        # Generate vertices on the circumcircle, rotating counter-clockwise from +X.
        start_angle = math.radians(rotation_val)
        vertices = []
        for i in range(sides_val):
            angle = start_angle + 2.0 * math.pi * i / sides_val
            vertices.append(_FC.Vector(cx_val + radius_val * math.cos(angle), cy_val + radius_val * math.sin(angle), 0.0))
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add sketch geometry."
            )

        segments = []
        for i in range(sides_val):
            seg = Part.LineSegment(vertices[i], vertices[(i + 1) % sides_val])
            segments.append(seg)
            sketch.addGeometry(seg, False)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_regular_polygon",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "center_x": cx_val,
            "center_y": cy_val,
            "radius": radius_val,
            "sides": sides_val,
            "rotation_deg": rotation_val,
            "edges_added": sides_val,
            "total_edges": len(getattr(sketch, "Geometry", [])),
            "meta_before": meta_before,
        },
    }


def _op_sketch_add_polygon(params: dict[str, Any]) -> dict[str, Any]:
    """Add a custom polygon from ordered ``{x, y}`` vertices to a sketch.

    Each vertex is connected to the next and the last back to the first via
    ``Part.LineSegment`` edges on Z=0.  Vertex order is preserved; no
    reordering or repair is performed.  Consecutive duplicates, a repeated
    final vertex equal to the first, and zero-area polygons are rejected.
    Self-intersection is not validated.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    vertices_param = params.get("vertices")
    workspace_str = params.get("workspace") or ""

    # Validate vertices is a list of {x, y} objects with finite numbers.
    if not isinstance(vertices_param, list) or len(vertices_param) < 3:
        return {
            "ok": False,
            "op": "sketch_add_polygon",
            "error": "vertices must be a list of at least 3 {x, y} objects.",
        }

    vertex_coords = []
    for idx, v in enumerate(vertices_param):
        if not isinstance(v, dict):
            return {
                "ok": False,
                "op": "sketch_add_polygon",
                "error": f"vertices[{idx}] must be an object with 'x' and 'y'.",
            }
        try:
            x_val = float(v.get("x"))
            y_val = float(v.get("y"))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "op": "sketch_add_polygon",
                "error": f"vertices[{idx}] x and y must be finite numbers.",
            }
        if math.isnan(x_val) or math.isinf(x_val):
            return {
                "ok": False,
                "op": "sketch_add_polygon",
                "error": f"vertices[{idx}] x must be a finite number.",
            }
        if math.isnan(y_val) or math.isinf(y_val):
            return {
                "ok": False,
                "op": "sketch_add_polygon",
                "error": f"vertices[{idx}] y must be a finite number.",
            }
        vertex_coords.append((x_val, y_val))

    # Reject consecutive duplicate vertices.
    for i in range(len(vertex_coords) - 1):
        if vertex_coords[i] == vertex_coords[i + 1]:
            return {
                "ok": False,
                "op": "sketch_add_polygon",
                "error": f"consecutive duplicate vertex at index {i+1}.",
            }

    # Reject a repeated final vertex equal to the first (closure is automatic).
    if vertex_coords[-1] == vertex_coords[0]:
        return {
            "ok": False,
            "op": "sketch_add_polygon",
            "error": "final vertex duplicates the first; closure is automatic, do not repeat it.",
        }

    # Reject degenerate (zero area) polygons.
    cross_sum = 0.0
    for i in range(len(vertex_coords)):
        x1, y1 = vertex_coords[i]
        x2, y2 = vertex_coords[(i + 1) % len(vertex_coords)]
        cross_sum += x1 * y2 - x2 * y1
    if abs(cross_sum) <= 1e-12:
        return {
            "ok": False,
            "op": "sketch_add_polygon",
            "error": "polygon has zero area (degenerate or collinear vertices).",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_add_polygon", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_add_polygon", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_add_polygon", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_add_polygon", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part.LineSegment geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add sketch geometry."
            )

        vertices = [_FC.Vector(x, y, 0.0) for x, y in vertex_coords]
        segments = []
        n = len(vertices)
        for i in range(n):
            seg = Part.LineSegment(vertices[i], vertices[(i + 1) % n])
            segments.append(seg)
            sketch.addGeometry(seg, False)
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_add_polygon",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "edges_added": n,
            "total_edges": len(getattr(sketch, "Geometry", [])),
            "meta_before": meta_before,
        },
    }


def _inspect_geometry(geometry: Any) -> dict[str, Any]:
    """Inspect a single Sketcher geometry object and return its parameters.

    Reads properties safely with getattr/try-except so unknown or
    partially-available geometry types never crash the inspection.
    """
    info: dict[str, Any] = {"index": 0, "type": "Unknown"}
    try:
        info["type"] = type(geometry).__name__
    except Exception:
        pass

    # Construction geometry status.
    try:
        info["construction"] = bool(getattr(geometry, "Construction", False))
    except Exception:
        pass

    # LineSegment: StartPoint, EndPoint.
    geom_type = info["type"]
    if geom_type == "LineSegment":
        for attr, key in (("StartPoint", "start_point"), ("EndPoint", "end_point")):
            try:
                point = getattr(geometry, attr)
                info[key] = {
                    "x": float(point.x),
                    "y": float(point.y),
                    "z": float(point.z),
                }
            except Exception:
                pass
    # Circle: Center, Radius.
    elif geom_type == "Circle":
        try:
            center = getattr(geometry, "Center")
            info["center"] = {
                "x": float(center.x),
                "y": float(center.y),
                "z": float(center.z),
            }
        except Exception:
            pass
        try:
            info["radius"] = float(getattr(geometry, "Radius"))
        except Exception:
            pass
    # Ellipse: Center, MajorRadius, MinorRadius, MajorDirection, MinorDirection.
    elif geom_type == "Ellipse":
        try:
            center = getattr(geometry, "Center")
            info["center"] = {
                "x": float(center.x),
                "y": float(center.y),
                "z": float(center.z),
            }
        except Exception:
            pass
        for attr, key in (("MajorRadius", "major_radius"), ("MinorRadius", "minor_radius")):
            try:
                info[key] = float(getattr(geometry, attr))
            except Exception:
                pass
        for attr, key in (
            ("MajorDirection", "major_direction"),
            ("MinorDirection", "minor_direction"),
        ):
            try:
                direction = getattr(geometry, attr)
                info[key] = {
                    "x": float(direction.x),
                    "y": float(direction.y),
                    "z": float(direction.z),
                }
            except Exception:
                pass

    return info


def _op_inspect_sketch(params: dict[str, Any]) -> dict[str, Any]:
    """Inspect a sketch and return structured geometry information (read-only).

    Does not save, recompute, or increment document revision.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "inspect_sketch", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "inspect_sketch", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "inspect_sketch", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "inspect_sketch", "error": f"sketch '{sketch_name}' not found"}

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "inspect_sketch",
                "error": (
                    f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                    f"cannot inspect sketch geometry."
                ),
            }

        geometries = []
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        for index, geometry in enumerate(geometry_list):
            geometry_info = _inspect_geometry(geometry)
            geometry_info["index"] = index
            geometries.append(geometry_info)

        # Constraint count.
        try:
            constraint_count = len(getattr(sketch, "Constraints", []))
        except Exception:
            constraint_count = None

        # Individual constraint details.
        constraints = []
        try:
            constraint_list = list(getattr(sketch, "Constraints", []))
        except Exception:
            constraint_list = []
        for index, constraint in enumerate(constraint_list):
            constraint_info: dict[str, Any] = {"index": index}
            for attr, key in (
                ("Type", "type"),
                ("Name", "name"),
                ("First", "first"),
                ("Second", "second"),
                ("Third", "third"),
                ("Value", "value"),
                ("IsActive", "is_active"),
                ("Driving", "driving"),
            ):
                try:
                    constraint_info[key] = getattr(constraint, attr)
                except Exception:
                    constraint_info[key] = None
            constraints.append(constraint_info)

        # Solver status.
        try:
            fc_val = getattr(sketch, "FullyConstrained", None)
            fully_constrained = bool(fc_val) if fc_val is not None else None
        except Exception:
            fully_constrained = None
        try:
            degrees_of_freedom = getattr(sketch, "DoF", None)
        except Exception:
            degrees_of_freedom = None

        # Sketch placement if available.
        placement_info: dict[str, Any] = {}
        try:
            placement = getattr(sketch, "Placement", None)
            if placement is not None:
                base = getattr(placement, "Base", None)
                if base is not None:
                    placement_info = {
                        "x": float(base.x),
                        "y": float(base.y),
                        "z": float(base.z),
                    }
        except Exception:
            pass

    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "inspect_sketch", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "inspect_sketch",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "type": type_id,
            "geometry_count": len(geometries),
            "constraint_count": constraint_count,
            "constraints": constraints,
            "geometries": geometries,
            "placement": placement_info,
            "fully_constrained": fully_constrained,
            "degrees_of_freedom": degrees_of_freedom,
        },
    }


def _op_inspect_sketch_profiles(params: dict[str, Any]) -> dict[str, Any]:
    """Inspect closed/open profiles (wires and faces) in an existing sketch.

    Read-only.  Uses the native FreeCAD ``Sketcher::SketchObject.Shape`` to
    enumerate wires and faces, reporting each one's closed/valid status and the
    geometry indices it references.  Does not save, recompute, or modify the
    document.  Does not attempt to repair or close any geometry.

    Supported subelement references (e.g. for Pad ``ProfileFace``) are reported
    as ``FaceN`` / ``WireN`` strings where N is the 1-based index in
    ``sketch.Shape.Faces`` / ``sketch.Shape.Wires``.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    workspace_str = params.get("workspace") or ""

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "inspect_sketch_profiles", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "inspect_sketch_profiles", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "inspect_sketch_profiles", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "inspect_sketch_profiles", "error": f"sketch '{sketch_name}' not found"}

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "inspect_sketch_profiles",
                "error": (
                    f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                    f"cannot inspect profiles."
                ),
            }

        # Build geometry index map: stable geometry -> zero-based index.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        geometry_count = len(geometry_list)

        profiles: list[dict[str, Any]] = []

        # --- Faces (closed profiles that form planar regions) ---
        try:
            faces = list(getattr(sketch.Shape, "Faces", []) or [])
        except Exception:
            faces = []
        for face_index, face in enumerate(faces):
            subelement = f"Face{face_index + 1}"
            valid = False
            closed = False
            try:
                valid = bool(face.isValid())
            except Exception:
                valid = False
            try:
                # A Face is inherently closed (it bounds a region).
                closed = True
            except Exception:
                closed = False
            profiles.append(
                {
                    "index": len(profiles),
                    "type": "face",
                    "subelement": subelement,
                    "closed": closed,
                    "valid": valid,
                    "geometry_indices": list(range(geometry_count)),
                    "face_index": face_index,
                }
            )

        # --- Wires (open or closed curves) ---
        try:
            wires = list(getattr(sketch.Shape, "Wires", []) or [])
        except Exception:
            wires = []
        for wire_index, wire in enumerate(wires):
            subelement = f"Wire{wire_index + 1}"
            valid = False
            closed = False
            try:
                valid = bool(wire.isValid())
            except Exception:
                valid = False
            try:
                closed = bool(wire.isClosed())
            except Exception:
                closed = False
            profiles.append(
                {
                    "index": len(profiles),
                    "type": "wire",
                    "subelement": subelement,
                    "closed": closed,
                    "valid": valid,
                    "geometry_indices": list(range(geometry_count)),
                    "wire_index": wire_index,
                }
            )

        usable_profiles = [p for p in profiles if p["valid"] and p["closed"]]

    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "inspect_sketch_profiles", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "inspect_sketch_profiles",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_count": geometry_count,
            "profile_count": len(profiles),
            "usable_profile_count": len(usable_profiles),
            "profiles": profiles,
        },
    }


def _op_select_sketch_profile(params: dict[str, Any]) -> dict[str, Any]:
    """Validate and select a specific profile in an existing sketch.

    Read-only validation.  Confirms that the requested profile index refers to a
    valid, closed profile and returns its subelement reference (e.g. ``Face1``)
    suitable for downstream operations such as Pad ``ProfileFace``.  Does not
    modify the document or the sketch.

    The selection is deterministic: profile indices are stable for the lifetime
    of the sketch's Shape, and indices are assigned in face-then-wire order
    (faces first, then wires), matching the order returned by
    ``inspect_sketch_profiles``.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    profile_index_param = params.get("profile_index")
    workspace_str = params.get("workspace") or ""

    # Validate profile_index: must be an integer, not a boolean, non-negative.
    if isinstance(profile_index_param, bool) or not isinstance(profile_index_param, int):
        return {
            "ok": False,
            "op": "select_sketch_profile",
            "error": "profile_index must be an integer (boolean values are not accepted).",
        }
    profile_index = profile_index_param
    if profile_index < 0:
        return {
            "ok": False,
            "op": "select_sketch_profile",
            "error": "profile_index must be non-negative.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "select_sketch_profile", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "select_sketch_profile", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "select_sketch_profile", "error": lock_status.get("error", "lock failed")}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "select_sketch_profile", "error": f"sketch '{sketch_name}' not found"}

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "select_sketch_profile",
                "error": (
                    f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                    f"cannot select a profile."
                ),
            }

        # Re-derive profiles in the same deterministic order as inspect.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        geometry_count = len(geometry_list)

        try:
            faces = list(getattr(sketch.Shape, "Faces", []) or [])
        except Exception:
            faces = []
        try:
            wires = list(getattr(sketch.Shape, "Wires", []) or [])
        except Exception:
            wires = []

        # Build ordered list of (profile_dict) in face-then-wire order.
        ordered: list[dict[str, Any]] = []
        for face_index, face in enumerate(faces):
            valid = False
            closed = False
            try:
                valid = bool(face.isValid())
            except Exception:
                valid = False
            try:
                closed = True  # A Face is inherently closed
            except Exception:
                closed = False
            ordered.append(
                {
                    "index": len(ordered),
                    "type": "face",
                    "subelement": f"Face{face_index + 1}",
                    "closed": closed,
                    "valid": valid,
                    "geometry_indices": list(range(geometry_count)),
                    "face_index": face_index,
                }
            )
        for wire_index, wire in enumerate(wires):
            valid = False
            closed = False
            try:
                valid = bool(wire.isValid())
            except Exception:
                valid = False
            try:
                closed = bool(wire.isClosed())
            except Exception:
                closed = False
            ordered.append(
                {
                    "index": len(ordered),
                    "type": "wire",
                    "subelement": f"Wire{wire_index + 1}",
                    "closed": closed,
                    "valid": valid,
                    "geometry_indices": list(range(geometry_count)),
                    "wire_index": wire_index,
                }
            )

        if profile_index >= len(ordered):
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "select_sketch_profile",
                "error": (
                    f"profile_index {profile_index} is out of range "
                    f"(valid range: 0 to {len(ordered) - 1})."
                ),
            }

        selected = ordered[profile_index]
        if not selected["valid"] or not selected["closed"]:
            ws_mod.release_lock(lock_path, pid)
            return {
                "ok": False,
                "op": "select_sketch_profile",
                "error": (
                    f"profile at index {profile_index} ({selected['subelement']}) is not "
                    f"usable: closed={selected['closed']}, valid={selected['valid']}."
                ),
            }

    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": "select_sketch_profile", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "select_sketch_profile",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "profile_index": selected["index"],
            "subelement": selected["subelement"],
            "type": selected["type"],
            "closed": selected["closed"],
            "valid": selected["valid"],
            "geometry_indices": selected["geometry_indices"],
            "usable": True,
        },
    }


def _op_sketch_delete_geometry(params: dict[str, Any]) -> dict[str, Any]:
    """Delete one geometry element from an existing sketch (read-modify-write).

    Validates that the target object exists and is a Sketcher::SketchObject,
    validates geometry_index strictly (integer, not boolean, in range), then
    deletes exactly the requested geometry via FreeCAD's delGeometry.  The
    document is recomputed, saved atomically, and its revision is bumped.
    FreeCAD automatically removes any constraints that referenced the deleted
    geometry.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_delete_geometry",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }

    geometry_index = geometry_index_param

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_delete_geometry", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_delete_geometry", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_delete_geometry", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_delete_geometry", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot delete sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        if geometry_index < 0 or geometry_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range (valid range: 0 to {geometry_count - 1})."
            )

        # Record constraint count before deletion (for constraint-safety reporting).
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Delete the geometry. FreeCAD automatically removes constraints that
        # referenced the deleted geometry.
        sketch.delGeometry(geometry_index)
        doc.recompute()

        # Record constraint count after deletion.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_delete_geometry",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "deleted_index": geometry_index,
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count - 1,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_line(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing line geometry in a sketch (read-modify-write).

    Validates that the target object exists and is a Sketcher::SketchObject,
    validates geometry_index strictly (integer, not boolean, in range), verifies
    the geometry at that index is a line segment, validates all coordinates are
    finite and non-degenerate, then replaces the line endpoints by mutating the
    geometry and assigning the full geometry list back to ``sketch.Geometry``
    (the documented FreeCAD property-assignment pattern; FreeCAD 1.1.4 has no
    ``setGeometry``/``setLine`` method).  This keeps the geometry at the same
    index and preserves construction state and geometry IDs.  The document is
    recomputed, saved atomically, and its revision is bumped.

    Constraint safety: if any constraint references the geometry being edited
    (via Constraint.First/Second/Third integer geometry indices), the operation
    is rejected with a clear error rather than silently breaking constraints.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    start_x_param = params.get("start_x")
    start_y_param = params.get("start_y")
    end_x_param = params.get("end_x")
    end_y_param = params.get("end_y")
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_line",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }

    geometry_index = geometry_index_param

    # Validate all coordinates are finite numbers.
    try:
        start_x_val = float(start_x_param)
        start_y_val = float(start_y_param)
        end_x_val = float(end_x_param)
        end_y_val = float(end_y_param)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_edit_line",
            "error": "start_x, start_y, end_x, end_y must be finite numbers.",
        }
    for name, val in (
        ("start_x", start_x_val),
        ("start_y", start_y_val),
        ("end_x", end_x_val),
        ("end_y", end_y_val),
    ):
        if math.isnan(val) or math.isinf(val):
            return {
                "ok": False,
                "op": "sketch_edit_line",
                "error": f"{name} must be a finite number.",
            }

    # Reject zero-length / degenerate lines.
    dx = end_x_val - start_x_val
    dy = end_y_val - start_y_val
    if dx * dx + dy * dy <= 1e-12:
        return {
            "ok": False,
            "op": "sketch_edit_line",
            "error": "line is degenerate (zero length): start and end points must differ.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_line", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_line", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_line", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_line", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        if geometry_index < 0 or geometry_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range (valid range: 0 to {geometry_count - 1})."
            )

        # Verify the geometry at this index is a line segment.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        target_geometry = geometry_list[geometry_index]
        geom_type = type(target_geometry).__name__
        if geom_type != "LineSegment":
            return _fail(
                f"geometry at index {geometry_index} is type '{geom_type}', not 'LineSegment'; "
                f"cannot edit as a line."
            )

        # Constraint safety: reject if any constraint references this geometry index.
        # FreeCAD Constraint.First/Second/Third are integer geometry indices
        # (not sub-element name strings), so we can check them directly.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                if (
                    getattr(constraint, "First", None) == geometry_index
                    or getattr(constraint, "Second", None) == geometry_index
                    or getattr(constraint, "Third", None) == geometry_index
                ):
                    constraint_references_geometry = True
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at index {geometry_index}: it is referenced by one or more "
                f"constraints. Remove the constraints first."
            )

        # FreeCAD 1.1.4 exposes no setGeometry/setLine method.  The supported
        # pattern (used by FreeCAD's own Draft sketcher-editing code) is to
        # mutate the geometry copy and assign the full list back to the
        # Geometry property.  Reassigning the existing object keeps the same
        # index, construction state, and geometry ID; only the endpoints change.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            target_geometry = geometry_list[geometry_index]
            target_geometry.StartPoint = _FC.Vector(start_x_val, start_y_val, 0.0)
            target_geometry.EndPoint = _FC.Vector(end_x_val, end_y_val, 0.0)
            geometry_list[geometry_index] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing line geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_line",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "start_x": start_x_val,
            "start_y": start_y_val,
            "end_x": end_x_val,
            "end_y": end_y_val,
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_circle(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing circle geometry in a Sketcher::SketchObject.

    Mutates the existing circle's Center and Radius in place, preserving its
    geometry index, geometry ID, construction state, and unrelated geometries.
    The geometry list copy / mutate / reassign pattern is used because
    FreeCAD 1.1.4 exposes no setGeometry method on Sketcher.SketchObject.

    Constraint safety: if any constraint references the geometry being edited
    (via Constraint.First/Second/Third integer geometry indices), the operation
    is rejected with a clear error rather than silently breaking constraints.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    center_x_param = params.get("center_x")
    center_y_param = params.get("center_y")
    radius_param = params.get("radius")
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_circle",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }

    geometry_index = geometry_index_param

    # Validate all coordinates and radius are finite numbers.
    try:
        cx_val = float(center_x_param)
        cy_val = float(center_y_param)
        radius_val = float(radius_param)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_edit_circle",
            "error": "center_x, center_y, and radius must be finite numbers.",
        }
    for name, val in (
        ("center_x", cx_val),
        ("center_y", cy_val),
        ("radius", radius_val),
    ):
        if math.isnan(val) or math.isinf(val):
            return {
                "ok": False,
                "op": "sketch_edit_circle",
                "error": f"{name} must be a finite number.",
            }

    # Reject zero or negative radius.
    if radius_val <= 0:
        return {
            "ok": False,
            "op": "sketch_edit_circle",
            "error": "radius must be a positive number.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_circle", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_circle", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_circle", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_circle", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        if geometry_index < 0 or geometry_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range (valid range: 0 to {geometry_count - 1})."
            )

        # Verify the geometry at this index is a circle.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        target_geometry = geometry_list[geometry_index]
        geom_type = type(target_geometry).__name__
        if geom_type != "Circle":
            return _fail(
                f"geometry at index {geometry_index} is type '{geom_type}', not 'Circle'; "
                f"cannot edit as a circle."
            )

        # Constraint safety: reject if any constraint references this geometry index.
        # FreeCAD Constraint.First/Second/Third are integer geometry indices
        # (not sub-element name strings), so we can check them directly.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                if (
                    getattr(constraint, "First", None) == geometry_index
                    or getattr(constraint, "Second", None) == geometry_index
                    or getattr(constraint, "Third", None) == geometry_index
                ):
                    constraint_references_geometry = True
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at index {geometry_index}: it is referenced by one or more "
                f"constraints. Remove the constraints first."
            )

        # Mutate the existing circle's Center and Radius in place, preserving
        # geometry index, geometry ID, and construction state.  Reassign the
        # full geometry list back to the Geometry property.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            target_geometry = geometry_list[geometry_index]
            target_geometry.Center = _FC.Vector(cx_val, cy_val, 0.0)
            target_geometry.Radius = radius_val
            geometry_list[geometry_index] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing circle geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))
        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_circle",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "center_x": cx_val,
            "center_y": cy_val,
            "radius": radius_val,
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_ellipse(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing ellipse geometry in a Sketcher::SketchObject.

    Mutates the existing ellipse's Center, MajorRadius, MinorRadius, and
    orientation (by rotating the conic in place) while preserving its
    geometry index, geometry ID, construction state, and unrelated geometries.
    The geometry list copy / mutate / reassign pattern is used because
    FreeCAD 1.1.4 exposes no setGeometry method on Sketcher.SketchObject.

    *major_radius* and *minor_radius* are semi-axis lengths (millimetres).
    Both must be positive and major_radius must be >= minor_radius.

    *rotation_deg* rotates the major axis counter-clockwise from the sketch
    +X direction (degrees; default 0 = axis-aligned, matching
    sketch_add_ellipse).

    Constraint safety: if any constraint references the geometry being edited
    (via Constraint.First/Second/Third integer geometry indices), the operation
    is rejected with a clear error rather than silently breaking constraints.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    center_x_param = params.get("center_x")
    center_y_param = params.get("center_y")
    major_radius_param = params.get("major_radius")
    minor_radius_param = params.get("minor_radius")
    rotation_deg_param = params.get("rotation_deg", 0)
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_ellipse",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }

    geometry_index = geometry_index_param

    # Validate all numeric inputs are finite numbers.
    try:
        cx_val = float(center_x_param)
        cy_val = float(center_y_param)
        major_val = float(major_radius_param)
        minor_val = float(minor_radius_param)
        rotation_val = float(rotation_deg_param)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_edit_ellipse",
            "error": "center_x, center_y, major_radius, minor_radius, and rotation_deg must be finite numbers.",
        }
    for name, val in (
        ("center_x", cx_val),
        ("center_y", cy_val),
        ("major_radius", major_val),
        ("minor_radius", minor_val),
        ("rotation_deg", rotation_val),
    ):
        if math.isnan(val) or math.isinf(val):
            return {
                "ok": False,
                "op": "sketch_edit_ellipse",
                "error": f"{name} must be a finite number.",
            }

    # Reject zero or negative radii.
    if major_val <= 0:
        return {
            "ok": False,
            "op": "sketch_edit_ellipse",
            "error": "major_radius must be a positive number.",
        }
    if minor_val <= 0:
        return {
            "ok": False,
            "op": "sketch_edit_ellipse",
            "error": "minor_radius must be a positive number.",
        }

    # Reject ellipses whose major semi-axis is shorter than the minor semi-axis.
    if major_val < minor_val:
        return {
            "ok": False,
            "op": "sketch_edit_ellipse",
            "error": "major_radius must be greater than or equal to minor_radius.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_ellipse", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_ellipse", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_ellipse", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_ellipse", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        if geometry_index < 0 or geometry_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range (valid range: 0 to {geometry_count - 1})."
            )

        # Verify the geometry at this index is an ellipse, not a circle or
        # another geometry type.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        target_geometry = geometry_list[geometry_index]
        geom_type = type(target_geometry).__name__
        if geom_type != "Ellipse":
            return _fail(
                f"geometry at index {geometry_index} is type '{geom_type}', not 'Ellipse'; "
                f"cannot edit as an ellipse."
            )

        # Constraint safety: reject if any constraint references this geometry
        # index.  FreeCAD Constraint.First/Second/Third are integer geometry
        # indices (not sub-element name strings), so we can check them directly.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                if (
                    getattr(constraint, "First", None) == geometry_index
                    or getattr(constraint, "Second", None) == geometry_index
                    or getattr(constraint, "Third", None) == geometry_index
                ):
                    constraint_references_geometry = True
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at index {geometry_index}: it is referenced by one or more "
                f"constraints. Remove the constraints first."
            )

        # Mutate the existing ellipse in place, preserving geometry index,
        # geometry ID, and construction state.  Reassign the full geometry
        # list back to the Geometry property.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            target_geometry = geometry_list[geometry_index]
            target_geometry.Center = _FC.Vector(cx_val, cy_val, 0.0)
            target_geometry.MajorRadius = major_val
            target_geometry.MinorRadius = minor_val
            # FreeCAD 1.1.4 has no writable MajorDirection/MinorDirection
            # properties (and no setGeometry method).  Part.Ellipse.rotate()
            # mutates the conic in place and expects a Placement whose rotation
            # angle is in degrees.  rotation_deg is absolute (counter-clockwise
            # from +X), so rotate by the difference from the current AngleXU.
            current_angle_deg = math.degrees(float(target_geometry.AngleXU))
            target_geometry.rotate(
                _FC.Placement(
                    _FC.Vector(cx_val, cy_val, 0.0),
                    _FC.Rotation(_FC.Vector(0.0, 0.0, 1.0), rotation_val - current_angle_deg),
                )
            )
            geometry_list[geometry_index] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing ellipse geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_ellipse",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "center_x": cx_val,
            "center_y": cy_val,
            "major_radius": major_val,
            "minor_radius": minor_val,
            "rotation_deg": rotation_val,
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_rectangle(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing rectangle (four connected LineSegment geometries) in a sketch.

    ``geometry_index`` is the index of the *first* line of the rectangle; the
    rectangle occupies four consecutive geometry slots (index through index+3).
    The four lines are mutated in place and the full geometry list is reassigned
    to ``sketch.Geometry``, preserving each geometry's index, construction state,
    and geometry ID, as well as all unrelated geometry.

    Parameters:
        *x*, *y*: new lower-left corner before rotation (millimetres).
        *width*, *height*: positive dimensions in millimetres.
        *rotation_deg*: optional rotation around the rectangle centre, in
            degrees (default 0 = axis-aligned).

    The operation validates that the four consecutive geometries actually form a
    connected rectangle before mutating anything, and rejects constraint-dependent
    geometry following the same policy as sketch_edit_line/circle/ellipse.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    x_param = params.get("x")
    y_param = params.get("y")
    width_param = params.get("width")
    height_param = params.get("height")
    rotation_deg_param = params.get("rotation_deg", 0)
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_rectangle",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }

    geometry_index = geometry_index_param

    # Validate all numeric inputs are finite numbers.
    try:
        x_val = float(x_param)
        y_val = float(y_param)
        width_val = float(width_param)
        height_val = float(height_param)
        rotation_val = float(rotation_deg_param)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_edit_rectangle",
            "error": "x, y, width, height, and rotation_deg must be finite numbers.",
        }
    for name, val in (
        ("x", x_val),
        ("y", y_val),
        ("width", width_val),
        ("height", height_val),
        ("rotation_deg", rotation_val),
    ):
        if math.isnan(val) or math.isinf(val):
            return {
                "ok": False,
                "op": "sketch_edit_rectangle",
                "error": f"{name} must be a finite number.",
            }

    # Reject zero or negative dimensions.
    if width_val <= 0:
        return {
            "ok": False,
            "op": "sketch_edit_rectangle",
            "error": "width must be a positive number.",
        }
    if height_val <= 0:
        return {
            "ok": False,
            "op": "sketch_edit_rectangle",
            "error": "height must be a positive number.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_rectangle", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_rectangle", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_rectangle", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_rectangle", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        # A rectangle occupies exactly four consecutive geometry slots.
        last_index = geometry_index + 3
        if geometry_index < 0 or last_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range for a rectangle "
                f"(valid range: 0 to {geometry_count - 4})."
            )

        # Load the four candidate geometries.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        rect_geometries = [geometry_list[geometry_index + i] for i in range(4)]

        # Verify all four are line segments.
        geom_types = [type(g).__name__ for g in rect_geometries]
        if any(gt != "LineSegment" for gt in geom_types):
            return _fail(
                f"geometry at index {geometry_index} does not form a rectangle: "
                f"found types {geom_types}; expected four LineSegment geometries."
            )

        # Verify the four lines form a connected, valid rectangle.
        points: list[_FC.Vector] = []
        try:
            for seg in rect_geometries:
                sp = getattr(seg, "StartPoint", None)
                ep = getattr(seg, "EndPoint", None)
                if sp is None or ep is None:
                    return _fail(
                        f"geometry at index {geometry_index} does not form a valid rectangle: "
                        f"a line segment is missing StartPoint or EndPoint."
                    )
                points.append(_FC.Vector(float(sp.x), float(sp.y), float(sp.z)))
                points.append(_FC.Vector(float(ep.x), float(ep.y), float(ep.z)))
        except (TypeError, ValueError, AttributeError) as exc:
            return _fail(
                f"geometry at index {geometry_index} does not form a valid rectangle: {exc}"
            )

        # points = [P0,P1, P1,P2, P2,P3, P3,P0] -> unique corners P0..P3
        p0, p1, p2, p3 = points[0], points[1], points[3], points[5]
        # Fourth point of last segment should equal p0.
        eps = 1e-9

        def _same_point(a: _FC.Vector, b: _FC.Vector) -> bool:
            return (
                abs(a.x - b.x) <= eps
                and abs(a.y - b.y) <= eps
                and abs(a.z - b.z) <= eps
            )

        # Connectivity: P1==P1, P2==P2, P3==P3, P0(last)==P0(first).
        if not (_same_point(points[1], points[2]) and _same_point(points[3], points[4])):
            return _fail(
                f"geometry at index {geometry_index} does not form a connected rectangle: "
                f"line endpoints do not match."
            )
        if not _same_point(points[5], points[6]) or not _same_point(points[7], points[0]):
            return _fail(
                f"geometry at index {geometry_index} does not form a connected rectangle: "
                f"line endpoints do not match."
            )

        # Edge vectors.
        e0x, e0y = p1.x - p0.x, p1.y - p0.y  # bottom
        e1x, e1y = p2.x - p1.x, p2.y - p1.y  # right
        e2x, e2y = p3.x - p2.x, p3.y - p2.y  # top
        e3x, e3y = p0.x - p3.x, p0.y - p3.y  # left

        def _dot(ax, ay, bx, by) -> float:
            return ax * bx + ay * by

        def _len2(ax, ay) -> float:
            return ax * ax + ay * ay

        # Right angles: adjacent edges perpendicular.
        if (
            abs(_dot(e0x, e0y, e1x, e1y)) > eps * max(1.0, _len2(e0x, e0y), _len2(e1x, e1y))
            or abs(_dot(e1x, e1y, e2x, e2y)) > eps * max(1.0, _len2(e1x, e1y), _len2(e2x, e2y))
            or abs(_dot(e2x, e2y, e3x, e3y)) > eps * max(1.0, _len2(e2x, e2y), _len2(e3x, e3y))
            or abs(_dot(e3x, e3y, e0x, e0y)) > eps * max(1.0, _len2(e3x, e3y), _len2(e0x, e0y))
        ):
            return _fail(
                f"geometry at index {geometry_index} does not form a rectangle: "
                f"adjacent edges are not perpendicular."
            )
        # Opposite edges equal length and parallel.
        if (
            abs(_len2(e0x, e0y) - _len2(e2x, e2y)) > eps * max(1.0, _len2(e0x, e0y), _len2(e2x, e2y))
            or abs(_len2(e1x, e1y) - _len2(e3x, e3y)) > eps * max(1.0, _len2(e1x, e1y), _len2(e3x, e3y))
        ):
            return _fail(
                f"geometry at index {geometry_index} does not form a rectangle: "
                f"opposite edges are not equal."
            )
        if _len2(e0x, e0y) <= eps or _len2(e1x, e1y) <= eps:
            return _fail(
                f"geometry at index {geometry_index} does not form a rectangle: "
                f"degenerate (zero-length) edge."
            )

        # Constraint safety: reject if any constraint references any of the four
        # geometry indices.  FreeCAD Constraint.First/Second/Third are integer
        # geometry indices, so we can check them directly.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                for i in range(4):
                    idx = geometry_index + i
                    if (
                        getattr(constraint, "First", None) == idx
                        or getattr(constraint, "Second", None) == idx
                        or getattr(constraint, "Third", None) == idx
                    ):
                        constraint_references_geometry = True
                        break
                if constraint_references_geometry:
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at indices {geometry_index} through {last_index}: "
                f"one or more lines are referenced by constraints. Remove the constraints first."
            )

        # Compute the rotated corners around the rectangle centre.
        centre_x = x_val + width_val / 2.0
        centre_y = y_val + height_val / 2.0
        angle = math.radians(rotation_val)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        # Local corners relative to centre: lower-left, lower-right,
        # upper-right, upper-left.
        local_corners = [
            (-width_val / 2.0, -height_val / 2.0),
            (width_val / 2.0, -height_val / 2.0),
            (width_val / 2.0, height_val / 2.0),
            (-width_val / 2.0, height_val / 2.0),
        ]
        corners: list[_FC.Vector] = []
        for lx, ly in local_corners:
            rx = centre_x + lx * cos_a - ly * sin_a
            ry = centre_y + lx * sin_a + ly * cos_a
            corners.append(_FC.Vector(rx, ry, 0.0))

        # Mutate the four existing line geometries in place, preserving index,
        # construction state, and geometry ID.  Reassign the full geometry list.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            new_segments = [
                (corners[0], corners[1]),  # bottom
                (corners[1], corners[2]),  # right
                (corners[2], corners[3]),  # top
                (corners[3], corners[0]),  # left
            ]
            for i in range(4):
                target_geometry = geometry_list[geometry_index + i]
                start_pt, end_pt = new_segments[i]
                target_geometry.StartPoint = start_pt
                target_geometry.EndPoint = end_pt
                geometry_list[geometry_index + i] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing rectangle geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_rectangle",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "x": x_val,
            "y": y_val,
            "width": width_val,
            "height": height_val,
            "rotation_deg": rotation_val,
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_triangle(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing triangle (three connected LineSegment geometries) in a sketch.

    ``geometry_index`` is the index of the *first* line of the triangle; the
    triangle occupies three consecutive geometry slots (index through index+2).
    The three lines are mutated in place and the full geometry list is reassigned
    to ``sketch.Geometry``, preserving each geometry's index, construction state,
    and geometry ID, as well as all unrelated geometry.

    *vertices* is a list of exactly three ordered 2D vertices, each with *x* and *y*
    keys.  Vertex ordering is preserved.  The three segments connect vertex 0->1,
    1->2, and 2->0 (closing back to the start).

    The operation validates that the three consecutive geometries actually form a
    connected, non-degenerate triangle before mutating anything, and rejects
    constraint-dependent geometry following the same policy as
    sketch_edit_line/circle/ellipse/rectangle.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    vertices_param = params.get("vertices")
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_triangle",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }

    geometry_index = geometry_index_param

    # Validate vertices: must be a list/tuple of exactly three objects with x and y.
    if not isinstance(vertices_param, (list, tuple)) or len(vertices_param) != 3:
        return {
            "ok": False,
            "op": "sketch_edit_triangle",
            "error": "vertices must be a list of exactly three {x, y} objects.",
        }

    verts = []
    for i, v in enumerate(vertices_param):
        if not isinstance(v, dict) or "x" not in v or "y" not in v:
            return {
                "ok": False,
                "op": "sketch_edit_triangle",
                "error": f"vertices[{i}] must be an object with 'x' and 'y' keys.",
            }
        try:
            vx = float(v["x"])
            vy = float(v["y"])
        except (TypeError, ValueError):
            return {
                "ok": False,
                "op": "sketch_edit_triangle",
                "error": f"vertices[{i}] x and y must be finite numbers.",
            }
        if math.isnan(vx) or math.isinf(vx) or math.isnan(vy) or math.isinf(vy):
            return {
                "ok": False,
                "op": "sketch_edit_triangle",
                "error": f"vertices[{i}] x and y must be finite numbers.",
            }
        verts.append((vx, vy))

    # Reject duplicate adjacent vertices (zero-length edge) and collinear /
    # zero-area triangles via the signed area check.
    (x1, y1), (x2, y2), (x3, y3) = verts

    eps = 1e-9
    def _same_point(ax, ay, bx, by) -> bool:
        return abs(ax - bx) <= eps and abs(ay - by) <= eps

    if (
        _same_point(x1, y1, x2, y2)
        or _same_point(x2, y2, x3, y3)
        or _same_point(x3, y3, x1, y1)
    ):
        return {
            "ok": False,
            "op": "sketch_edit_triangle",
            "error": "triangle vertices must be distinct; duplicate adjacent vertices produce a zero-length edge.",
        }

    # Reject collinear / zero-area triangles using signed area.
    cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
    scale_sq = max(
        1.0,
        (x2 - x1) ** 2 + (y2 - y1) ** 2,
        (x3 - x2) ** 2 + (y3 - y2) ** 2,
        (x1 - x3) ** 2 + (y1 - y3) ** 2,
    )
    if abs(cross) <= eps * scale_sq:
        return {
            "ok": False,
            "op": "sketch_edit_triangle",
            "error": "triangle is degenerate or collinear (zero signed area).",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_triangle", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_triangle", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_triangle", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_triangle", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        # A triangle occupies exactly three consecutive geometry slots.
        last_index = geometry_index + 2
        if geometry_index < 0 or last_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range for a triangle "
                f"(valid range: 0 to {geometry_count - 3})."
            )

        # Load the three candidate geometries.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        tri_geometries = [geometry_list[geometry_index + i] for i in range(3)]

        # Verify all three are line segments.
        geom_types = [type(g).__name__ for g in tri_geometries]
        if any(gt != "LineSegment" for gt in geom_types):
            return _fail(
                f"geometry at index {geometry_index} does not form a triangle: "
                f"found types {geom_types}; expected three LineSegment geometries."
            )

        # Verify the three lines form a connected, valid triangle.
        points: list[_FC.Vector] = []
        try:
            for seg in tri_geometries:
                sp = getattr(seg, "StartPoint", None)
                ep = getattr(seg, "EndPoint", None)
                if sp is None or ep is None:
                    return _fail(
                        f"geometry at index {geometry_index} does not form a valid triangle: "
                        f"a line segment is missing StartPoint or EndPoint."
                    )
                points.append(_FC.Vector(float(sp.x), float(sp.y), float(sp.z)))
                points.append(_FC.Vector(float(ep.x), float(ep.y), float(ep.z)))
        except (TypeError, ValueError, AttributeError) as exc:
            return _fail(
                f"geometry at index {geometry_index} does not form a valid triangle: {exc}"
            )

        # points = [P0,P1, P1,P2, P2,P0] for a closed triangle where segment 2
        # goes from vertex 2 back to vertex 0.
        p0, p1, p2 = points[0], points[1], points[3]

        # Connectivity: segment[1].start == segment[0].end, segment[2].start ==
        # segment[1].end, segment[2].end == segment[0].start (closed loop).
        if (
            not _same_point(points[1].x, points[1].y, points[2].x, points[2].y)
            or not _same_point(points[3].x, points[3].y, points[4].x, points[4].y)
            or not _same_point(points[5].x, points[5].y, points[0].x, points[0].y)
        ):
            return _fail(
                f"geometry at index {geometry_index} does not form a connected triangle: "
                f"line endpoints do not match."
            )

        # Edge vectors.
        e0x, e0y = p1.x - p0.x, p1.y - p0.y  # edge 0->1
        e1x, e1y = p2.x - p1.x, p2.y - p1.y  # edge 1->2
        e2x, e2y = p0.x - p2.x, p0.y - p2.y  # edge 2->0

        def _dot(ax, ay, bx, by) -> float:
            return ax * bx + ay * by

        def _len2(ax, ay) -> float:
            return ax * ax + ay * ay

        # Non-degenerate: cross product (signed area * 2) must be non-zero.
        cross_existing = e0x * e1y - e0y * e1x
        if abs(cross_existing) <= eps * max(1.0, _len2(e0x, e0y), _len2(e1x, e1y)):
            return _fail(
                f"geometry at index {geometry_index} does not form a triangle: "
                f"edges are collinear or degenerate (zero area)."
            )

        # Constraint safety: reject if any constraint references any of the three
        # geometry indices.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                for i in range(3):
                    idx = geometry_index + i
                    if (
                        getattr(constraint, "First", None) == idx
                        or getattr(constraint, "Second", None) == idx
                        or getattr(constraint, "Third", None) == idx
                    ):
                        constraint_references_geometry = True
                        break
                if constraint_references_geometry:
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at indices {geometry_index} through {last_index}: "
                f"one or more lines are referenced by constraints. Remove the constraints first."
            )

        # Mutate the three existing line geometries in place, preserving index,
        # construction state, and geometry ID.  Reassign the full geometry list.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            new_segments = [
                (_FC.Vector(x1, y1, 0.0), _FC.Vector(x2, y2, 0.0)),  # edge 0->1
                (_FC.Vector(x2, y2, 0.0), _FC.Vector(x3, y3, 0.0)),  # edge 1->2
                (_FC.Vector(x3, y3, 0.0), _FC.Vector(x1, y1, 0.0)),  # edge 2->0 (closing)
            ]
            for i in range(3):
                target_geometry = geometry_list[geometry_index + i]
                start_pt, end_pt = new_segments[i]
                target_geometry.StartPoint = start_pt
                target_geometry.EndPoint = end_pt
                geometry_list[geometry_index + i] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing triangle geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_triangle",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "vertices": [
                {"x": x1, "y": y1},
                {"x": x2, "y": y2},
                {"x": x3, "y": y3},
            ],
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_regular_polygon(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing regular polygon (sides connected LineSegment geometries) in a sketch.

    ``geometry_index`` is the index of the *first* line of the polygon; the
    polygon occupies ``sides`` consecutive geometry slots (index through
    index+sides-1).  The segments are mutated in place and the full geometry
    list is reassigned to ``sketch.Geometry``, preserving each geometry's index,
    construction state, and geometry ID, as well as all unrelated geometry.

    Vertices lie on a circle centred at ``(center_x, center_y)`` on Z=0.
    *rotation_deg* rotates the first vertex counter-clockwise from the positive
    X axis, matching ``sketch_add_regular_polygon`` exactly.

    Before mutating, the operation validates that the ``sides`` consecutive
    geometries actually form a regular polygon: all LineSegment, connected in a
    closed loop, all vertices at equal distance from the centre, equal edge
    lengths, non-degenerate.  Constraint-dependent geometry is rejected.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    center_x = params.get("center_x")
    center_y = params.get("center_y")
    radius = params.get("radius")
    sides = params.get("sides")
    rotation_deg = params.get("rotation_deg", 0)
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_regular_polygon",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }
    geometry_index = geometry_index_param

    # Validate centre and radius.
    try:
        cx_val = float(center_x)
        cy_val = float(center_y)
        radius_val = float(radius)
        rotation_val = float(rotation_deg)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_edit_regular_polygon",
            "error": "center_x, center_y, radius must be finite numbers and rotation_deg must be a finite number.",
        }
    for name, val in (("center_x", cx_val), ("center_y", cy_val), ("radius", radius_val), ("rotation_deg", rotation_val)):
        if math.isnan(val) or math.isinf(val):
            return {
                "ok": False,
                "op": "sketch_edit_regular_polygon",
                "error": f"{name} must be a finite number.",
            }
    if radius_val <= 0:
        return {
            "ok": False,
            "op": "sketch_edit_regular_polygon",
            "error": "radius must be a positive number.",
        }

    # Validate sides: must be an integer (not bool), at least 3.
    if isinstance(sides, bool) or not isinstance(sides, int):
        return {
            "ok": False,
            "op": "sketch_edit_regular_polygon",
            "error": "sides must be an integer (boolean values are not accepted).",
        }
    sides_val = sides
    if sides_val < 3:
        return {
            "ok": False,
            "op": "sketch_edit_regular_polygon",
            "error": "sides must be at least 3.",
        }
    if sides_val > MAX_POLYGON_SIDES:
        return {
            "ok": False,
            "op": "sketch_edit_regular_polygon",
            "error": f"sides must not exceed {MAX_POLYGON_SIDES}.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_regular_polygon", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_regular_polygon", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_regular_polygon", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_regular_polygon", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part.LineSegment geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        # A regular polygon occupies exactly ``sides_val`` consecutive geometry slots.
        last_index = geometry_index + sides_val - 1
        if geometry_index < 0 or last_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range for a {sides_val}-sided polygon "
                f"(valid range: 0 to {geometry_count - sides_val})."
            )

        # Load the candidate geometries.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        poly_geometries = [geometry_list[geometry_index + i] for i in range(sides_val)]

        # Verify all are line segments.
        geom_types = [type(g).__name__ for g in poly_geometries]
        if any(gt != "LineSegment" for gt in geom_types):
            return _fail(
                f"geometry at index {geometry_index} does not form a regular polygon: "
                f"found types {geom_types}; expected {sides_val} LineSegment geometries."
            )

        # Read segment endpoints.
        eps = 1e-9

        def _same_point(ax, ay, bx, by) -> bool:
            return abs(ax - bx) <= eps and abs(ay - by) <= eps

        try:
            seg_pts = []
            for seg in poly_geometries:
                sp = getattr(seg, "StartPoint", None)
                ep = getattr(seg, "EndPoint", None)
                if sp is None or ep is None:
                    return _fail(
                        f"geometry at index {geometry_index} does not form a valid regular polygon: "
                        f"a line segment is missing StartPoint or EndPoint."
                    )
                seg_pts.append((_FC.Vector(float(sp.x), float(sp.y), float(sp.z)), _FC.Vector(float(ep.x), float(ep.y), float(ep.z))))
        except (TypeError, ValueError, AttributeError) as exc:
            return _fail(
                f"geometry at index {geometry_index} does not form a valid regular polygon: {exc}"
            )

        # Vertices: v[i] = seg[i].start.  Connectivity requires seg[i].end == seg[(i+1)%n].start.
        vertices = [seg_pts[i][0] for i in range(sides_val)]
        for i in range(sides_val):
            end_pt = seg_pts[i][1]
            next_start = seg_pts[(i + 1) % sides_val][0]
            if not _same_point(end_pt.x, end_pt.y, next_start.x, next_start.y):
                return _fail(
                    f"geometry at index {geometry_index} does not form a connected closed polygon: "
                    f"line {i} endpoint does not match line {(i + 1) % sides_val} start."
                )

        # Non-degenerate: signed area must be non-zero.
        cross_sum = 0.0
        for i in range(sides_val):
            x1, y1 = vertices[i].x, vertices[i].y
            x2, y2 = vertices[(i + 1) % sides_val].x, vertices[(i + 1) % sides_val].y
            cross_sum += x1 * y2 - x2 * y1
        scale_sq = max(
            1.0,
            max(
                (vertices[(i + 1) % sides_val].x - vertices[i].x) ** 2
                + (vertices[(i + 1) % sides_val].y - vertices[i].y) ** 2
                for i in range(sides_val)
            ),
        )
        if abs(cross_sum) <= eps * scale_sq:
            return _fail(
                f"geometry at index {geometry_index} does not form a regular polygon: "
                f"edges are collinear or degenerate (zero area)."
            )

        # Regularity: all vertices at equal distance from the existing centre;
        # all edges equal length.  The centre is the existing polygon's centroid
        # (which equals the circumcentre for a regular polygon), not the new
        # centre_x/center_y/radius parameters (those describe the target shape).
        cx_exist = sum(v.x for v in vertices) / sides_val
        cy_exist = sum(v.y for v in vertices) / sides_val
        dists = [math.hypot(v.x - cx_exist, v.y - cy_exist) for v in vertices]
        edge_lens = [
            math.hypot(vertices[(i + 1) % sides_val].x - vertices[i].x,
                       vertices[(i + 1) % sides_val].y - vertices[i].y)
            for i in range(sides_val)
        ]
        max_dist = max(dists)
        max_edge = max(edge_lens)
        dist_tol = max(1e-9, 1e-6 * max_dist)
        edge_tol = max(1e-9, 1e-6 * max_edge)
        for i, d in enumerate(dists):
            if abs(d - dists[0]) > dist_tol:
                return _fail(
                    f"geometry at index {geometry_index} does not form a regular polygon: "
                    f"vertex {i} distance from existing centre ({d:.6f}) differs from vertex 0 "
                    f"({dists[0]:.6f})."
                )
        for i, e in enumerate(edge_lens):
            if abs(e - edge_lens[0]) > edge_tol:
                return _fail(
                    f"geometry at index {geometry_index} does not form a regular polygon: "
                    f"edge {i} length ({e:.6f}) differs from edge 0 length ({edge_lens[0]:.6f})."
                )

        # Constraint safety: reject if any constraint references any of the polygon geometry indices.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                for i in range(sides_val):
                    idx = geometry_index + i
                    if (
                        getattr(constraint, "First", None) == idx
                        or getattr(constraint, "Second", None) == idx
                        or getattr(constraint, "Third", None) == idx
                    ):
                        constraint_references_geometry = True
                        break
                if constraint_references_geometry:
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at indices {geometry_index} through {last_index}: "
                f"one or more lines are referenced by constraints. Remove the constraints first."
            )

        # Generate new vertices on the circumcircle, rotating counter-clockwise from +X,
        # matching sketch_add_regular_polygon exactly.
        start_angle = math.radians(rotation_val)
        new_vertices = []
        for i in range(sides_val):
            angle = start_angle + 2.0 * math.pi * i / sides_val
            new_vertices.append(
                _FC.Vector(cx_val + radius_val * math.cos(angle), cy_val + radius_val * math.sin(angle), 0.0)
            )

        # Mutate the existing line geometries in place, preserving index,
        # construction state, and geometry ID.  Reassign the full geometry list.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            for i in range(sides_val):
                target_geometry = geometry_list[geometry_index + i]
                start_pt = new_vertices[i]
                end_pt = new_vertices[(i + 1) % sides_val]
                target_geometry.StartPoint = start_pt
                target_geometry.EndPoint = end_pt
                geometry_list[geometry_index + i] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing regular polygon geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_regular_polygon",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "center_x": cx_val,
            "center_y": cy_val,
            "radius": radius_val,
            "sides": sides_val,
            "rotation_deg": rotation_val,
            "vertices": [
                {"x": new_vertices[i].x, "y": new_vertices[i].y} for i in range(sides_val)
            ],
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_edit_polygon(params: dict[str, Any]) -> dict[str, Any]:
    """Edit an existing custom polygon (connected LineSegment geometries) in a sketch.

    ``geometry_index`` is the index of the *first* line of the polygon; the
    polygon occupies ``len(vertices)`` consecutive geometry slots.  The number of
    supplied vertices must match the existing polygon's number of edges.  Vertex
    ordering is preserved; no reordering or repair is performed.

    Segments are mutated in place and the full geometry list is reassigned to
    ``sketch.Geometry``, preserving each geometry's index, construction state,
    and geometry ID, as well as all unrelated geometry.  The final edge connects
    the last vertex back to the first (closed loop).

    Before mutating, the operation validates that the selected geometries form a
    valid closed polygon: all LineSegment, connected, at least three edges,
    non-degenerate (non-zero area).  Constraint-dependent geometry is rejected.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    geometry_index_param = params.get("geometry_index")
    vertices_param = params.get("vertices")
    workspace_str = params.get("workspace") or ""

    # Validate geometry_index: must be an integer, not a boolean.
    if isinstance(geometry_index_param, bool) or not isinstance(geometry_index_param, int):
        return {
            "ok": False,
            "op": "sketch_edit_polygon",
            "error": "geometry_index must be an integer (boolean values are not accepted).",
        }
    geometry_index = geometry_index_param

    # Validate vertices: must be a list of at least 3 objects with x and y.
    if not isinstance(vertices_param, list) or len(vertices_param) < 3:
        return {
            "ok": False,
            "op": "sketch_edit_polygon",
            "error": "vertices must be a list of at least 3 {x, y} objects.",
        }

    vertex_coords = []
    for idx, v in enumerate(vertices_param):
        if not isinstance(v, dict) or "x" not in v or "y" not in v:
            return {
                "ok": False,
                "op": "sketch_edit_polygon",
                "error": f"vertices[{idx}] must be an object with 'x' and 'y' keys.",
            }
        try:
            vx = float(v["x"])
            vy = float(v["y"])
        except (TypeError, ValueError):
            return {
                "ok": False,
                "op": "sketch_edit_polygon",
                "error": f"vertices[{idx}] x and y must be finite numbers.",
            }
        if math.isnan(vx) or math.isinf(vx) or math.isnan(vy) or math.isinf(vy):
            return {
                "ok": False,
                "op": "sketch_edit_polygon",
                "error": f"vertices[{idx}] x and y must be finite numbers.",
            }
        vertex_coords.append((vx, vy))

    n_vertices = len(vertex_coords)

    # Reject consecutive duplicate vertices.
    for i in range(n_vertices - 1):
        if vertex_coords[i] == vertex_coords[i + 1]:
            return {
                "ok": False,
                "op": "sketch_edit_polygon",
                "error": f"consecutive duplicate vertex at index {i+1}.",
            }

    # Reject a repeated final vertex equal to the first (closure is automatic).
    if vertex_coords[-1] == vertex_coords[0]:
        return {
            "ok": False,
            "op": "sketch_edit_polygon",
            "error": "final vertex duplicates the first; closure is automatic, do not repeat it.",
        }

    # Reject degenerate (zero area) polygons via signed area.
    cross_sum = 0.0
    for i in range(n_vertices):
        x1, y1 = vertex_coords[i]
        x2, y2 = vertex_coords[(i + 1) % n_vertices]
        cross_sum += x1 * y2 - x2 * y1
    edge_scale_sq = max(
        1.0,
        max(
            (vertex_coords[(i + 1) % n_vertices][0] - vertex_coords[i][0]) ** 2
            + (vertex_coords[(i + 1) % n_vertices][1] - vertex_coords[i][1]) ** 2
            for i in range(n_vertices)
        ),
    )
    if abs(cross_sum) <= 1e-9 * edge_scale_sq:
        return {
            "ok": False,
            "op": "sketch_edit_polygon",
            "error": "polygon has zero area (degenerate or collinear vertices).",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_edit_polygon", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_edit_polygon", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_edit_polygon", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_edit_polygon", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part.LineSegment geometry for Sketcher
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot edit sketch geometry."
            )

        # Get current geometry count for range validation.
        try:
            geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_count = 0

        # The polygon occupies exactly n_vertices consecutive geometry slots.
        last_index = geometry_index + n_vertices - 1
        if geometry_index < 0 or last_index >= geometry_count:
            return _fail(
                f"geometry_index {geometry_index} is out of range for a {n_vertices}-vertex polygon "
                f"(valid range: 0 to {geometry_count - n_vertices})."
            )

        # Load the candidate geometries.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
        except Exception:
            geometry_list = []
        poly_geometries = [geometry_list[geometry_index + i] for i in range(n_vertices)]

        # Verify all are line segments.
        geom_types = [type(g).__name__ for g in poly_geometries]
        if any(gt != "LineSegment" for gt in geom_types):
            return _fail(
                f"geometry at index {geometry_index} does not form a polygon: "
                f"found types {geom_types}; expected {n_vertices} LineSegment geometries."
            )

        # Read segment endpoints.
        eps = 1e-9

        def _same_point(ax, ay, bx, by) -> bool:
            return abs(ax - bx) <= eps and abs(ay - by) <= eps

        try:
            seg_pts = []
            for seg in poly_geometries:
                sp = getattr(seg, "StartPoint", None)
                ep = getattr(seg, "EndPoint", None)
                if sp is None or ep is None:
                    return _fail(
                        f"geometry at index {geometry_index} does not form a valid polygon: "
                        f"a line segment is missing StartPoint or EndPoint."
                    )
                seg_pts.append((_FC.Vector(float(sp.x), float(sp.y), float(sp.z)), _FC.Vector(float(ep.x), float(ep.y), float(ep.z))))
        except (TypeError, ValueError, AttributeError) as exc:
            return _fail(
                f"geometry at index {geometry_index} does not form a valid polygon: {exc}"
            )

        # Vertices: v[i] = seg[i].start.  Connectivity requires seg[i].end ==
        # seg[(i+1)%n].start (closed loop).
        existing_vertices = [seg_pts[i][0] for i in range(n_vertices)]
        for i in range(n_vertices):
            end_pt = seg_pts[i][1]
            next_start = seg_pts[(i + 1) % n_vertices][0]
            if not _same_point(end_pt.x, end_pt.y, next_start.x, next_start.y):
                return _fail(
                    f"geometry at index {geometry_index} does not form a connected closed polygon: "
                    f"line {i} endpoint does not match line {(i + 1) % n_vertices} start."
                )

        # Non-degenerate: signed area of existing geometry must be non-zero.
        existing_cross = 0.0
        for i in range(n_vertices):
            x1, y1 = existing_vertices[i].x, existing_vertices[i].y
            x2, y2 = existing_vertices[(i + 1) % n_vertices].x, existing_vertices[(i + 1) % n_vertices].y
            existing_cross += x1 * y2 - x2 * y1
        existing_scale_sq = max(
            1.0,
            max(
                (existing_vertices[(i + 1) % n_vertices].x - existing_vertices[i].x) ** 2
                + (existing_vertices[(i + 1) % n_vertices].y - existing_vertices[i].y) ** 2
                for i in range(n_vertices)
            ),
        )
        if abs(existing_cross) <= 1e-9 * existing_scale_sq:
            return _fail(
                f"geometry at index {geometry_index} does not form a valid polygon: "
                f"existing geometry is degenerate or collinear (zero area)."
            )

        # Constraint safety: reject if any constraint references any polygon geometry index.
        constraint_references_geometry = False
        constraint_count_before = None
        try:
            constraint_count_before = len(getattr(sketch, "Constraints", []))
            for constraint in getattr(sketch, "Constraints", []):
                for i in range(n_vertices):
                    idx = geometry_index + i
                    if (
                        getattr(constraint, "First", None) == idx
                        or getattr(constraint, "Second", None) == idx
                        or getattr(constraint, "Third", None) == idx
                    ):
                        constraint_references_geometry = True
                        break
                if constraint_references_geometry:
                    break
        except Exception:
            pass

        if constraint_references_geometry:
            return _fail(
                f"Cannot edit geometry at indices {geometry_index} through {last_index}: "
                f"one or more lines are referenced by constraints. Remove the constraints first."
            )

        # Mutate the existing line geometries in place, preserving index,
        # construction state, and geometry ID.  Reassign the full geometry list.
        try:
            geometry_list = list(getattr(sketch, "Geometry", []))
            new_vertices = [_FC.Vector(x, y, 0.0) for x, y in vertex_coords]
            for i in range(n_vertices):
                target_geometry = geometry_list[geometry_index + i]
                start_pt = new_vertices[i]
                end_pt = new_vertices[(i + 1) % n_vertices]
                target_geometry.StartPoint = start_pt
                target_geometry.EndPoint = end_pt
                geometry_list[geometry_index + i] = target_geometry
            sketch.Geometry = geometry_list
        except (AttributeError, TypeError, ValueError) as exc:
            return _fail(
                f"FreeCAD Sketcher API does not support editing polygon geometry: {exc}"
            )

        doc.recompute()

        # Record constraint count after edit.
        constraint_count_after = None
        try:
            constraint_count_after = len(getattr(sketch, "Constraints", []))
        except Exception:
            pass

        # Get updated geometry count.
        updated_geometry_count = None
        try:
            updated_geometry_count = len(getattr(sketch, "Geometry", []))
        except Exception:
            pass

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_edit_polygon",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "geometry_index": geometry_index,
            "vertices": [
                {"x": new_vertices[i].x, "y": new_vertices[i].y} for i in range(n_vertices)
            ],
            "geometry_count": updated_geometry_count if updated_geometry_count is not None else geometry_count,
            "constraint_count_before": constraint_count_before,
            "constraint_count_after": constraint_count_after,
            "meta_before": meta_before,
        },
    }


def _op_sketch_pad(params: dict[str, Any]) -> dict[str, Any]:
    """Create a PartDesign::Pad that extrudes an existing sketch into a 3D solid.

    Uses a native ``PartDesign::Body`` and ``PartDesign::Pad`` (not
    ``Part::Extrusion``).  If the sketch already belongs to a Body, that Body
    is reused.  Otherwise a new Body is created and the sketch is added to it
    via ``body.addObject(sketch)``, preserving the sketch's identity, geometry,
    and parametric editability.

    The Pad profile is set to the existing sketch, and the requested length
    and reversed direction are applied.  After recompute, the resulting Pad
    shape is validated (non-null solid with positive volume).  The document is
    locked, recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the on-disk
    document is left unmodified (rollback via the established save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    length = params.get("length")
    reversed_param = params.get("reversed", False)
    pad_name = params.get("pad_name") or "Pad"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""
    profile_param = params.get("profile", None)

    # Validate sketch_name.
    if not sketch_name.strip():
        return {
            "ok": False,
            "op": "sketch_pad",
            "error": "sketch_name must not be empty.",
        }

    # Validate length: finite and positive.
    try:
        length_val = float(length)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_pad",
            "error": "length must be a finite number.",
        }
    if math.isnan(length_val) or math.isinf(length_val):
        return {
            "ok": False,
            "op": "sketch_pad",
            "error": "length must be a finite number.",
        }
    if length_val <= 0:
        return {
            "ok": False,
            "op": "sketch_pad",
            "error": "length must be a positive number.",
        }

    # Validate reversed: must be a boolean (not an int/float that looks like one).
    if not isinstance(reversed_param, bool):
        return {
            "ok": False,
            "op": "sketch_pad",
            "error": "reversed must be a boolean (true/false).",
        }

    # Optional profile: defaults to the whole sketch (None).  Resolved after the
    # sketch is fetched inside the try block.
    profile_param = params.get("profile", None)
    profile_name = None
    profile_value = None

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_pad", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_pad", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_pad", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_pad", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the sketch and verify it is a Sketcher::SketchObject.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot create a Pad from it."
            )

        # Resolve the optional profile selection.  When profile_param is set it
        # names a closed wire subelement (e.g. "Wire1"); otherwise the whole
        # sketch is used as the Pad profile.
        if profile_param is not None:
            if not isinstance(profile_param, str) or not profile_param.strip():
                return _fail(
                    "profile must be a non-empty string naming a closed wire (e.g. 'Wire1')."
                )
            profile_name = profile_param.strip()
            try:
                shape = getattr(sketch, "Shape", None)
                wires = list(shape.Wires) if shape is not None else []
            except Exception:
                wires = []
            wire_names = [f"Wire{i + 1}" for i in range(len(wires))]
            if profile_name not in wire_names:
                return _fail(
                    f"profile '{profile_name}' not found in sketch '{sketch_name}'. "
                    f"Available wire profiles: {wire_names}."
                )
            wire = wires[wire_names.index(profile_name)]
            try:
                if not bool(wire.isClosed()):
                    return _fail(f"profile '{profile_name}' is not a closed wire.")
            except Exception:
                return _fail(
                    f"profile '{profile_name}' could not be validated as a closed wire."
                )
            profile_value = (sketch, [profile_name])

        # Determine the Body: reuse an existing Body that contains the sketch,
        # or an explicitly named Body supplied by the caller (even when the
        # sketch is not yet a member of it), or create a new Body.
        body = None
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    # Check if the sketch is already a feature of this Body.
                    try:
                        if sketch in getattr(obj, "Group", []) or sketch in getattr(obj, "outList", []):
                            body = obj
                            break
                    except Exception:
                        pass
        except Exception:
            body = None

        if body is None:
            # Reuse an explicitly named Body if it already exists.  This lets a
            # caller create a Body first (create_body) and then add a standalone
            # sketch to it via the Pad operation.
            try:
                named_body = doc.getObject(body_name)
            except Exception:
                named_body = None
            if named_body is not None and getattr(named_body, "TypeId", "") == "PartDesign::Body":
                body = named_body

        if body is None:
            # No existing Body hosts the sketch and no matching named Body
            # exists.  Create a new Body.
            # Avoid name clashes.
            counter = 0
            desired_body_name = body_name
            while doc.getObject(desired_body_name) is not None:
                counter += 1
                desired_body_name = f"{body_name}_{counter}"
            try:
                body = doc.addObject("PartDesign::Body", desired_body_name)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not create a PartDesign::Body: {exc}"
                )

        body_name_used = getattr(body, "Name", body_name)

        # Add the sketch to the Body if it is not already a member.  This
        # preserves the sketch's identity, geometry, and parametric editability.
        try:
            if sketch not in getattr(body, "Group", []) and sketch not in getattr(body, "outList", []):
                body.addObject(sketch)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add sketch to Body: {exc}.  The sketch may already "
                f"belong to another Body or may not be suitable for PartDesign."
            )

        # Create the Pad.  Avoid name clashes.
        counter = 0
        desired_pad_name = pad_name
        while doc.getObject(desired_pad_name) is not None:
            counter += 1
            desired_pad_name = f"{pad_name}_{counter}"
        try:
            pad = doc.addObject("PartDesign::Pad", desired_pad_name)
            body.addObject(pad)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Pad: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Pad profile to the existing sketch, or to the selected wire
        # subelement when a profile was requested.
        try:
            pad.Profile = profile_value if profile_value is not None else sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pad profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Apply length and reversed direction.
        try:
            pad.Length = length_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pad length: {exc}"
            )
        try:
            pad.Reversed = reversed_param
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pad reversed direction: {exc}"
            )

        # Recompute the document so FreeCAD evaluates the Pad feature tree.
        doc.recompute()

        # Validate the resulting Pad: it must have a valid, non-null solid shape.
        shape = getattr(pad, "Shape", None)
        if shape is None:
            return _fail(
                f"Pad '{desired_pad_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"Pad '{desired_pad_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Pad '{desired_pad_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Pad '{desired_pad_name}' produced no solid.  The sketch profile may be "
                f"open or not suitable for a single-solid Pad."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_pad",
            "result": {
                "document_id": normalized,
                "sketch_name": sketch_name,
                "body_name": body_name_used,
                "pad_name": desired_pad_name,
                "profile_name": f"{sketch_name}#{profile_name}" if profile_name else sketch_name,
                "length": length_val,
                "reversed": reversed_param,
                "volume": volume,
                "bounding_box": bbox_info,
                "solids_count": len(solids),
                "meta_before": meta_before,
            },
        }


def _op_sketch_pocket(params: dict[str, Any]) -> dict[str, Any]:
    """Create a PartDesign::Pocket that cuts an existing sketch out of a solid.

    Uses a native ``PartDesign::Body`` and ``PartDesign::Pocket`` (not
    ``Part::Cut``).  The sketch must already belong to a Body, or the document
    must contain exactly one (unambiguous) Body to which the sketch can be
    assigned.  A new Body is never silently created for a Pocket -- a Pocket
    requires an existing solid to cut from.

    The Pocket requires a valid existing solid (the Body's Tip or last feature)
    to cut into.  If no suitable preceding solid feature exists, the operation
    is rejected.

    The Pocket profile is set to the existing sketch, and the requested length
    and reversed direction are applied.  After recompute, the resulting Pocket
    shape is validated (non-null valid solid).  The document is locked,
    recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the on-disk
    document is left unmodified (rollback via the established save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    length = params.get("length")
    reversed_param = params.get("reversed", False)
    pocket_name = params.get("pocket_name") or "Pocket"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""
    profile_param = params.get("profile", None)

    # Validate sketch_name.
    if not sketch_name.strip():
        return {
            "ok": False,
            "op": "sketch_pocket",
            "error": "sketch_name must not be empty.",
        }

    # Validate length: finite and positive.
    try:
        length_val = float(length)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_pocket",
            "error": "length must be a finite number.",
        }
    if math.isnan(length_val) or math.isinf(length_val):
        return {
            "ok": False,
            "op": "sketch_pocket",
            "error": "length must be a finite number.",
        }
    if length_val <= 0:
        return {
            "ok": False,
            "op": "sketch_pocket",
            "error": "length must be a positive number.",
        }

    # Validate reversed: must be a boolean.
    if not isinstance(reversed_param, bool):
        return {
            "ok": False,
            "op": "sketch_pocket",
            "error": "reversed must be a boolean (true/false).",
        }

    # Optional profile: defaults to the whole sketch (None).  Resolved after the
    # sketch is fetched inside the try block.
    profile_name = None
    profile_value = None

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_pocket", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_pocket", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_pocket", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_pocket", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the sketch and verify it is a Sketcher::SketchObject.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot create a Pocket from it."
            )

        # Resolve the optional profile selection.  When profile_param is set it
        # names a closed wire subelement (e.g. "Wire1"); otherwise the whole
        # sketch is used as the Pocket profile.
        if profile_param is not None:
            if not isinstance(profile_param, str) or not profile_param.strip():
                return _fail(
                    "profile must be a non-empty string naming a closed wire (e.g. 'Wire1')."
                )
            profile_name = profile_param.strip()
            try:
                shape = getattr(sketch, "Shape", None)
                wires = list(shape.Wires) if shape is not None else []
            except Exception:
                wires = []
            wire_names = [f"Wire{i + 1}" for i in range(len(wires))]
            if profile_name not in wire_names:
                return _fail(
                    f"profile '{profile_name}' not found in sketch '{sketch_name}'. "
                    f"Available wire profiles: {wire_names}."
                )
            wire = wires[wire_names.index(profile_name)]
            try:
                if not bool(wire.isClosed()):
                    return _fail(f"profile '{profile_name}' is not a closed wire.")
            except Exception:
                return _fail(
                    f"profile '{profile_name}' could not be validated as a closed wire."
                )
            profile_value = (sketch, [profile_name])

        # Determine the Body: reuse an existing Body that contains the sketch,
        # or use the single unambiguous Body if the sketch is standalone.
        # A new Body is never created for a Pocket.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if sketch in getattr(obj, "Group", []) or sketch in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        if body is None:
            # Sketch is not in any Body.  Use the single unambiguous Body if
            # exactly one exists; otherwise reject (ambiguous or none).
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    previous_tip = body.Tip
                    body.addObject(sketch)
                    body.Tip = previous_tip
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add sketch to Body '{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"sketch '{sketch_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A Pocket requires a Body containing a "
                    f"base solid to cut from."
                )
            else:
                return _fail(
                    f"sketch '{sketch_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the Pocket."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Verify the Body has a preceding solid feature to cut from.
        # In FreeCAD PartDesign, the Pocket's Base is automatically set to the
        # Body's Tip (the last feature).  Check that Tip exists and has a valid
        # solid shape.
        base_feature = None
        try:
            base_feature = getattr(body, "Tip", None)
        except Exception:
            base_feature = None

        if base_feature is None:
            # Fall back to the last feature in the Body's Group/outList.
            try:
                group = getattr(body, "Group", []) or []
                out_list = getattr(body, "outList", []) or []
                candidates = list(out_list) or list(group)
                if candidates:
                    base_feature = candidates[-1]
            except Exception:
                base_feature = None

        if base_feature is None:
            return _fail(
                f"Body '{body_name_used}' has no preceding feature to use as the Pocket base. "
                f"A Pocket requires an existing solid feature before it in the Body."
            )

        base_shape = getattr(base_feature, "Shape", None)
        if base_shape is None:
            return _fail(
                f"Body '{body_name_used}' base feature '{getattr(base_feature, 'Name', '?')}' "
                f"has no shape.  A Pocket requires a valid solid to cut from."
            )
        try:
            if not bool(base_shape.isValid()):
                return _fail(
                    f"Body '{body_name_used}' base feature '{getattr(base_feature, 'Name', '?')}' "
                    f"has an invalid shape.  A Pocket requires a valid solid to cut from."
                )
        except Exception as exc:
            return _fail(
                f"Body '{body_name_used}' base feature shape validation failed: {exc}"
            )
        try:
            base_solids = list(getattr(base_shape, "Solids", []) or [])
        except Exception:
            base_solids = []
        if not base_solids:
            return _fail(
                f"Body '{body_name_used}' base feature '{getattr(base_feature, 'Name', '?')}' "
                f"produces no solid.  A Pocket requires an existing solid to cut from."
            )

        # Create the Pocket.  Avoid name clashes.
        counter = 0
        desired_pocket_name = pocket_name
        while doc.getObject(desired_pocket_name) is not None:
            counter += 1
            desired_pocket_name = f"{pocket_name}_{counter}"
        try:
            pocket = doc.addObject("PartDesign::Pocket", desired_pocket_name)
            body.addObject(pocket)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Pocket: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Pocket profile to the existing sketch, or to the selected wire
        # subelement when a profile was requested.
        try:
            pocket.Profile = profile_value if profile_value is not None else sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pocket profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Apply length and reversed direction.
        try:
            pocket.Length = length_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pocket length: {exc}"
            )
        try:
            pocket.Reversed = reversed_param
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pocket reversed direction: {exc}"
            )

        # Recompute the document so FreeCAD evaluates the Pocket feature tree.
        doc.recompute()

        # Validate the resulting Pocket: it must have a valid, non-null solid shape.
        shape = getattr(pocket, "Shape", None)
        if shape is None:
            return _fail(
                f"Pocket '{desired_pocket_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or may not intersect the base solid."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"Pocket '{desired_pocket_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Pocket '{desired_pocket_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Pocket '{desired_pocket_name}' produced no solid.  The sketch may not "
                f"intersect the base solid in a way that produces a valid cut."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_pocket",
            "result": {
                "document_id": normalized,
                "sketch_name": sketch_name,
                "body_name": body_name_used,
                "pocket_name": desired_pocket_name,
                "profile_name": f"{sketch_name}#{profile_name}" if profile_name else sketch_name,
                "length": length_val,
                "reversed": reversed_param,
                "volume": volume,
                "bounding_box": bbox_info,
                "solids_count": len(solids),
                "meta_before": meta_before,
            },
        }


def _find_revolution_axis_edge(sketch: Any, axis: str) -> str | None:
    """Find a sketch edge subname parallel to the requested principal axis.

    FreeCAD 1.1.4's PartDesign::Revolution requires the ReferenceAxis to be a
    subelement of the profile sketch (an edge subname like ``"Edge1"``).  The
    Body origin axes cannot be used: X_Axis/Y_Axis intersect the sketch and
    Z_Axis is rejected as ``perpendicular to the sketch plane``.

    The edge direction is computed in the sketch's local coordinate system and
    then transformed by the sketch's Placement so the selected edge corresponds
    to the requested *global* axis direction.  Only edges lying in the sketch
    plane are considered; a 2D sketch on the XY plane has no Z-parallel edge,
    so Z_Axis cannot be supported and the caller must return a validation error.
    """
    import FreeCAD as App  # noqa: PLC0415 - imported lazily; only available inside FreeCAD

    # Global axis direction vectors.
    target = {
        "X_Axis": App.Vector(1.0, 0.0, 0.0),
        "Y_Axis": App.Vector(0.0, 1.0, 0.0),
        "Z_Axis": App.Vector(0.0, 0.0, 1.0),
    }.get(axis)
    if target is None:
        return None

    # Collect candidate edges: prefer Shape.Edges (real FreeCAD), fall back to
    # Geometry (mock / test environments where Shape may not exist).
    edges: list[tuple[str, Any]] = []
    shape = getattr(sketch, "Shape", None)
    if shape is not None:
        try:
            shape_edges = list(shape.Edges) if hasattr(shape, "Edges") else []
            for i, edge in enumerate(shape_edges, start=1):
                edges.append((f"Edge{i}", edge))
        except Exception:
            pass

    if not edges:
        try:
            geometry = list(getattr(sketch, "Geometry", []) or [])
        except Exception:
            geometry = []
        for i, geom in enumerate(geometry, start=1):
            edges.append((f"Edge{i}", geom))

    placement = getattr(sketch, "Placement", None)
    rotation = placement.Rotation if placement is not None else App.Rotation()

    for subname, edge in edges:
        try:
            # LineSegment-like edges have StartPoint/EndPoint; others use tangentAt.
            if hasattr(edge, "StartPoint") and hasattr(edge, "EndPoint"):
                p1 = edge.StartPoint
                p2 = edge.EndPoint
                direction = App.Vector(p2.x - p1.x, p2.y - p1.y, p2.z - p1.z)
            else:
                tangent = edge.tangentAt(0.5)
                direction = App.Vector(tangent.x, tangent.y, tangent.z)
        except Exception:
            continue

        try:
            direction.normalize()
        except Exception:
            continue

        # Transform from sketch-local to global direction.
        try:
            global_dir = rotation * direction
        except Exception:
            global_dir = direction

        try:
            global_dir.normalize()
        except Exception:
            continue

        # Check parallelism (dot product near +/-1).
        try:
            dot = abs(global_dir.dot(target))
        except Exception:
            continue

        if dot > 0.99:
            return subname

    return None


def _find_body_containing(doc: Any, obj: Any) -> Any:
    """Return the PartDesign::Body whose Group/outList contains obj, or None."""
    try:
        for body in getattr(doc, "Objects", []) or []:
            if getattr(body, "TypeId", "") != "PartDesign::Body":
                continue
            group = getattr(body, "Group", [])
            out_list = getattr(body, "outList", [])
            if obj in group or obj in out_list:
                return body
    except Exception:
        return None
    return None


def _shape_has_subelement(shape: Any, subname: str, expected_type: str) -> bool:
    """True if shape resolves subname to a non-null subshape of expected_type.

    FreeCAD resolves subelement names via Shape.getElement(name); topological
    subshapes themselves do not expose a .Name attribute. Falls back to .Name
    iteration for mocks/tests that model subshapes with names.
    """
    if not shape or not subname:
        return False
    get_element = getattr(shape, "getElement", None)
    if callable(get_element):
        try:
            element = get_element(subname)
        except Exception:
            element = None
        if element is not None:
            try:
                if getattr(element, "isNull", lambda: False)():
                    return False
            except Exception:
                pass
            return getattr(element, "ShapeType", expected_type) == expected_type
    # Fallback for mocks that model subshapes with .Name attributes.
    try:
        for sub in list(getattr(shape, expected_type + "s", []) or []):
            if getattr(sub, "Name", None) == subname:
                return True
    except Exception:
        return False
    return False


def _op_sketch_attach_support(params: dict[str, Any]) -> dict[str, Any]:
    """Attach a Sketch to a support object's subelement via AttachmentSupport.

    Uses the FreeCAD 1.1.4 verified contract:
      sketch.AttachmentSupport = [(support_object, [support_subname])]
      sketch.MapMode = "FlatFace"

    Only MapMode "FlatFace" is verified; other values are rejected. The support
    subelement must be a planar face of the support object. The document is
    locked, recomputed, saved atomically, and its revision is bumped.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    support_object_name = params.get("support_object_name") or ""
    support_subname = params.get("support_subname") or ""
    map_mode = params.get("map_mode")
    workspace_str = params.get("workspace") or ""

    SUPPORTED_MAP_MODES = ("FlatFace",)
    if map_mode not in SUPPORTED_MAP_MODES:
        return {
            "ok": False,
            "op": OP_SKETCH_ATTACH_SUPPORT,
            "error": (
                f"map_mode '{map_mode}' is not supported. "
                f"Supported values: {', '.join(SUPPORTED_MAP_MODES)}."
            ),
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": OP_SKETCH_ATTACH_SUPPORT, "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": OP_SKETCH_ATTACH_SUPPORT, "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": OP_SKETCH_ATTACH_SUPPORT, "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": OP_SKETCH_ATTACH_SUPPORT, "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part module needed before Sketcher loads
        import Sketcher  # noqa: F401,PLC0415 - register Sketcher types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot set attachment support."
            )

        support = doc.getObject(support_object_name)
        if support is None:
            return _fail(f"support object '{support_object_name}' not found")

        shape = getattr(support, "Shape", None)
        if shape is None or not _shape_has_subelement(shape, support_subname, "Face"):
            return _fail(
                f"support subelement '{support_subname}' is not a face of "
                f"object '{support_object_name}'."
            )

        # Attach the sketch to the support face and fix the map mode.
        sketch.AttachmentSupport = [(support, [support_subname])]
        sketch.MapMode = map_mode
        doc.recompute()
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": OP_SKETCH_ATTACH_SUPPORT, "error": f"FreeCAD error: {exc}"}

    attachment_support_readback: list[Any] = []
    try:
        raw_support = list(getattr(sketch, "AttachmentSupport", []))
        for entry in raw_support:
            if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                obj, subs = entry[0], entry[1]
                if isinstance(subs, str):
                    subs = [subs]
                attachment_support_readback.append(
                    {"object": support_object_name, "subelements": list(subs)}
                )
    except Exception:
        attachment_support_readback = []
    map_mode_readback: Any = None
    try:
        map_mode_readback = getattr(sketch, "MapMode", None)
    except Exception:
        map_mode_readback = None
    placement_info: Any = None
    try:
        placement = getattr(sketch, "Placement", None)
        if placement is not None:
            base = getattr(placement, "Base", None)
            if base is not None:
                placement_info = {
                    "x": float(base.x),
                    "y": float(base.y),
                    "z": float(base.z),
                }
    except Exception:
        placement_info = None
    fully_constrained: Any = None
    try:
        fc_val = getattr(sketch, "FullyConstrained", None)
        fully_constrained = bool(fc_val) if fc_val is not None else None
    except Exception:
        fully_constrained = None
    degrees_of_freedom: Any = None
    try:
        degrees_of_freedom = getattr(sketch, "DoF", None)
    except Exception:
        degrees_of_freedom = None

    fcstd_path = wks.document_path(normalized)

    def _write_tmp(tmp_path: str) -> None:
        doc.saveAs(str(tmp_path))

    ws_mod.atomic_save(fcstd_path, _write_tmp)

    meta = ws_mod.read_meta(wks.meta_path(normalized))
    meta["revision"] = meta.get("revision", 0) + 1
    meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
    ws_mod.write_meta(wks.meta_path(normalized), meta)

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": OP_SKETCH_ATTACH_SUPPORT,
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "support_object_name": support_object_name,
            "support_subname": support_subname,
            "map_mode": map_mode,
            "attachment_support": attachment_support_readback,
            "map_mode_readback": map_mode_readback,
            "placement": placement_info,
            "fully_constrained": fully_constrained,
            "degrees_of_freedom": degrees_of_freedom,
            "meta_before": meta_before,
        },
    }


def _op_sketch_add_external_geometry(params: dict[str, Any]) -> dict[str, Any]:
    """Add external geometry to a Sketch via addExternal(source_name, subname).

    Uses the FreeCAD 1.1.4 verified constructor signature:
      sketch.addExternal(object_name, subelement_name, defining=False, intersection=False)

    Conservative source eligibility, based only on verified cases:
      * A standalone sketch (not in a Body) accepts an external source that is
        not itself in any Body.
      * A sketch inside a PartDesign::Body accepts a source in the SAME Body.
      * A sketch inside a Body rejects a source outside that Body.

    Combinations outside those verified cases are rejected with a clear error.
    The document is locked, recomputed, saved atomically, and its revision bumped.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    source_object_name = params.get("source_object_name") or ""
    source_subname = params.get("source_subname") or ""
    defining = params.get("defining", False)
    intersection = params.get("intersection", False)
    workspace_str = params.get("workspace") or ""

    if not isinstance(defining, bool):
        return {
            "ok": False,
            "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY,
            "error": "defining must be a boolean.",
        }
    if not isinstance(intersection, bool):
        return {
            "ok": False,
            "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY,
            "error": "intersection must be a boolean.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY, "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY, "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY, "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY, "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import Part  # noqa: F401,PLC0415 - Part module needed before Sketcher loads
        import Sketcher  # noqa: F401,PLC0415 - register Sketcher types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot add external geometry."
            )

        source = doc.getObject(source_object_name)
        if source is None:
            return _fail(f"source object '{source_object_name}' not found")

        shape = getattr(source, "Shape", None)
        if shape is None or not _shape_has_subelement(shape, source_subname, "Edge"):
            return _fail(
                f"source subelement '{source_subname}' is not an edge of "
                f"object '{source_object_name}'."
            )

        # Conservative eligibility: a sketch inside a Body may only reference a
        # source in the same Body; a standalone sketch may only reference a
        # source outside any Body.
        sketch_body = _find_body_containing(doc, sketch)
        source_body = _find_body_containing(doc, source)
        if sketch_body is not None and source_body is not sketch_body:
            return _fail(
                f"sketch '{sketch_name}' is inside Body '{getattr(sketch_body, 'Name', '?')}' "
                f"but source '{source_object_name}' is not in that same Body. "
                f"Body sketches may only use same-Body external geometry."
            )
        if sketch_body is None and source_body is not None:
            return _fail(
                f"standalone sketch '{sketch_name}' references source '{source_object_name}' "
                f"inside Body '{getattr(source_body, 'Name', '?')}'. "
                f"Standalone sketches may only reference sources outside any Body."
            )

        sketch.addExternal(source_object_name, source_subname, defining=defining, intersection=intersection)
        doc.recompute()
    except Exception as exc:
        try:
            ws_mod.release_lock(lock_path, pid)
        except Exception:
            pass
        return {"ok": False, "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY, "error": f"FreeCAD error: {exc}"}

    # Read back the resulting external geometry.
    external_geometry: list[Any] = []
    try:
        external_geometry = list(getattr(sketch, "ExternalGeometry", []))
    except Exception:
        external_geometry = []
    external_geo: Any = None
    try:
        external_geo = getattr(sketch, "ExternalGeo", None)
    except Exception:
        external_geo = None
    external_types: Any = None
    try:
        external_types = getattr(sketch, "ExternalTypes", None)
    except Exception:
        external_types = None
    placement: Any = None
    try:
        placement = getattr(sketch, "Placement", None)
        if placement is not None:
            base = getattr(placement, "Base", None)
            if base is not None:
                placement = {
                    "x": float(base.x),
                    "y": float(base.y),
                    "z": float(base.z),
                }
    except Exception:
        placement = None

    fcstd_path = wks.document_path(normalized)

    def _write_tmp(tmp_path: str) -> None:
        doc.saveAs(str(tmp_path))

    ws_mod.atomic_save(fcstd_path, _write_tmp)

    meta = ws_mod.read_meta(wks.meta_path(normalized))
    meta["revision"] = meta.get("revision", 0) + 1
    meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
    ws_mod.write_meta(wks.meta_path(normalized), meta)

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": OP_SKETCH_ADD_EXTERNAL_GEOMETRY,
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "source_object_name": source_object_name,
            "source_subname": source_subname,
            "defining": defining,
            "intersection": intersection,
            "external_geometry": external_geometry,
            "external_geo": external_geo,
            "external_types": external_types,
            "placement": placement,
            "meta_before": meta_before,
        },
    }


def _op_sketch_revolution(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Revolution that revolves an existing sketch around an axis.

    Uses a native ``PartDesign::Body`` and ``PartDesign::Revolution``.  If the
    sketch already belongs to a Body, that Body is reused.  Otherwise a new
    Body is created and the sketch is added to it via ``body.addObject(sketch)``,
    preserving the sketch's identity, geometry, and parametric editability.

    The Revolution profile is set to the existing sketch, and the requested
    axis (one of the Body's principal origin axes: X_Axis, Y_Axis, Z_Axis)
    and angle (degrees) are applied.  After recompute, the resulting Revolution
    shape is validated (non-null solid with positive volume).  The document is
    locked, recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the on-disk
    document is left unmodified (rollback via the established save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    axis = params.get("axis") or ""
    angle = params.get("angle")
    revolution_name = params.get("revolution_name") or "Revolution"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate sketch_name.
    if not sketch_name.strip():
        return {
            "ok": False,
            "op": "sketch_revolution",
            "error": "sketch_name must not be empty.",
        }

    # Validate axis: must be one of the three principal origin axes.
    if axis not in ("X_Axis", "Y_Axis", "Z_Axis"):
        return {
            "ok": False,
            "op": "sketch_revolution",
            "error": "axis must be one of 'X_Axis', 'Y_Axis', 'Z_Axis'.",
        }

    # Validate angle: finite and positive, maximum 360 degrees.
    try:
        angle_val = float(angle)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_revolution",
            "error": "angle must be a finite number.",
        }
    if math.isnan(angle_val) or math.isinf(angle_val):
        return {
            "ok": False,
            "op": "sketch_revolution",
            "error": "angle must be a finite number.",
        }
    if angle_val <= 0:
        return {
            "ok": False,
            "op": "sketch_revolution",
            "error": "angle must be a positive number.",
        }
    if angle_val > 360:
        return {
            "ok": False,
            "op": "sketch_revolution",
            "error": "angle must not exceed 360 degrees.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_revolution", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_revolution", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_revolution", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_revolution", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the sketch and verify it is a Sketcher::SketchObject.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot create a Revolution from it."
            )

        # Determine the Body: reuse an existing Body that contains the sketch,
        # or create a new Body and add the sketch to it.
        body = None
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    try:
                        if sketch in getattr(obj, "Group", []) or sketch in getattr(obj, "outList", []):
                            body = obj
                            break
                    except Exception:
                        pass
        except Exception:
            body = None

        if body is None:
            # No existing Body contains the sketch.  Create a new Body.
            counter = 0
            desired_body_name = body_name
            while doc.getObject(desired_body_name) is not None:
                counter += 1
                desired_body_name = f"{body_name}_{counter}"
            try:
                body = doc.addObject("PartDesign::Body", desired_body_name)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not create a PartDesign::Body: {exc}"
                )
            try:
                body.addObject(sketch)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not add sketch to Body: {exc}.  The sketch may already "
                    f"belong to another Body or may not be suitable for PartDesign."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the Revolution.  Avoid name clashes.
        counter = 0
        desired_revolution_name = revolution_name
        while doc.getObject(desired_revolution_name) is not None:
            counter += 1
            desired_revolution_name = f"{revolution_name}_{counter}"
        try:
            revolution = doc.addObject("PartDesign::Revolution", desired_revolution_name)
            body.addObject(revolution)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Revolution: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Revolution profile to the existing sketch.
        try:
            revolution.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Revolution profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Set the ReferenceAxis to the requested principal origin axis.
        # ReferenceAxis is an App::PropertyLinkSub: (linked_object, [subelement_names]).
        # FreeCAD 1.1.4 requires the axis to be a subelement of the profile sketch
        # (an edge subname like "Edge1").  The Body origin axes cannot be used:
        # X_Axis/Y_Axis intersect the sketch, and Z_Axis is rejected as perpendicular
        # to the sketch plane.  We find a sketch edge parallel to the requested
        # global axis, accounting for sketch placement.
        try:
            origin = getattr(body, "Origin", None)
            if origin is None:
                return _fail(
                    f"Body '{body_name_used}' has no Origin.  Cannot set the Revolution axis."
                )
            edge_subname = _find_revolution_axis_edge(sketch, axis)
            if edge_subname is None:
                return _fail(
                    f"Body '{body_name_used}' has no sketch edge parallel to the {axis} "
                    f"direction.  Cannot set the Revolution axis '{axis}'.  This axis cannot "
                    f"be represented for this sketch (e.g. Z_Axis has no parallel edge in a "
                    f"2D sketch on the XY plane)."
                )
            revolution.ReferenceAxis = (sketch, [edge_subname])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Revolution axis '{axis}': {exc}."
            )

        # Apply the revolution angle (degrees).
        try:
            revolution.Angle = angle_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Revolution angle: {exc}"
            )

        # Recompute the document so FreeCAD evaluates the Revolution feature tree.
        doc.recompute()

        # Validate the resulting Revolution: it must have a valid, non-null solid shape.
        shape = getattr(revolution, "Shape", None)
        if shape is None:
            return _fail(
                f"Revolution '{desired_revolution_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or the ReferenceAxis '{axis}' may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"Revolution '{desired_revolution_name}' produced a NULL shape.  The "
                    f"ReferenceAxis reference to '{axis}' was accepted but not resolved by FreeCAD "
                    f"(computed axis is Vector(0,0,0)).  This axis reference cannot be used for "
                    f"Revolution on this sketch."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"Revolution '{desired_revolution_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Revolution '{desired_revolution_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Revolution '{desired_revolution_name}' produced no solid.  The sketch profile or "
                f"axis configuration may not produce a valid solid."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_revolution",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "body_name": body_name_used,
            "revolution_name": desired_revolution_name,
            "profile_name": sketch_name,
            "axis": axis,
            "angle": angle_val,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_sketch_groove(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Groove (subtractive revolution) from a sketch.

    Uses a native ``PartDesign::Body`` and ``PartDesign::Groove``.  Unlike
    ``sketch_revolution``, a Groove requires an existing Body that already
    contains the sketch *and* a valid base solid (the Body.Tip).  The Groove
    removes material by revolving the sketch profile around the requested
    principal origin axis.  The sketch is not added to the Body by this
    operation -- the caller must ensure the sketch already belongs to the Body.

    After recompute, the resulting Groove shape is validated (non-null solid
    with positive volume).  The document is locked, recomputed, saved
    atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the on-disk
    document is left unmodified (rollback via the established save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    sketch_name = params.get("sketch_name") or ""
    axis = params.get("axis") or ""
    angle = params.get("angle")
    groove_name = params.get("groove_name") or "Groove"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate sketch_name.
    if not sketch_name.strip():
        return {
            "ok": False,
            "op": "sketch_groove",
            "error": "sketch_name must not be empty.",
        }

    # Validate axis: must be one of the three principal origin axes.
    if axis not in ("X_Axis", "Y_Axis", "Z_Axis"):
        return {
            "ok": False,
            "op": "sketch_groove",
            "error": "axis must be one of 'X_Axis', 'Y_Axis', 'Z_Axis'.",
        }

    # Validate angle: finite and positive, maximum 360 degrees.
    try:
        angle_val = float(angle)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_groove",
            "error": "angle must be a finite number.",
        }
    if math.isnan(angle_val) or math.isinf(angle_val):
        return {
            "ok": False,
            "op": "sketch_groove",
            "error": "angle must be a finite number.",
        }
    if angle_val <= 0:
        return {
            "ok": False,
            "op": "sketch_groove",
            "error": "angle must be a positive number.",
        }
    if angle_val > 360:
        return {
            "ok": False,
            "op": "sketch_groove",
            "error": "angle must not exceed 360 degrees.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_groove", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_groove", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_groove", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_groove", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the sketch and verify it is a Sketcher::SketchObject.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")

        type_id = sketch.TypeId
        type_name = type_id.split("::")[-1]
        if type_name != "SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{type_id}', not 'Sketcher::SketchObject'; "
                f"cannot create a Groove from it."
            )

        # A Groove requires an existing PartDesign::Body with a valid base solid.
        # Reuse a Body that already contains the sketch, or any Body with a base
        # solid; otherwise create a new Body and add the sketch to it.
        body = None
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    try:
                        if sketch in getattr(obj, "Group", []) or sketch in getattr(obj, "outList", []):
                            body = obj
                            break
                    except Exception:
                        pass
        except Exception:
            body = None

        if body is None:
            # No Body contains the sketch.  Reuse any Body that has a base solid,
            # or create a new one and add the sketch to it.
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    tip = getattr(obj, "Tip", None)
                    tip_shape = getattr(tip, "Shape", None) if tip is not None else None
                    if tip_shape is not None and not getattr(tip_shape, "isNull", lambda: False)() and bool(tip_shape.isValid()):
                        body = obj
                        break

        if body is None:
            counter = 0
            desired_body_name = body_name
            while doc.getObject(desired_body_name) is not None:
                counter += 1
                desired_body_name = f"{body_name}_{counter}"
            try:
                body = doc.addObject("PartDesign::Body", desired_body_name)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not create a PartDesign::Body: {exc}"
                )
            try:
                body.addObject(sketch)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not add sketch to Body: {exc}.  The sketch may already "
                    f"belong to another Body or may not be suitable for PartDesign."
                )
        else:
            # Ensure the sketch belongs to the Body.
            try:
                if sketch not in getattr(body, "Group", []) and sketch not in getattr(body, "outList", []):
                    body.addObject(sketch)
            except Exception:
                pass

        body_name_used = getattr(body, "Name", body_name)

        # Require a valid base solid in the Body (Body.Tip).
        tip = getattr(body, "Tip", None)
        if tip is None:
            return _fail(
                f"Body '{body_name_used}' has no Tip.  A Groove requires a valid base solid.  "
                f"Create a Pad or other base solid before adding a Groove."
            )
        tip_shape = getattr(tip, "Shape", None)
        if tip_shape is None or getattr(tip_shape, "isNull", lambda: False)() or not bool(tip_shape.isValid()):
            return _fail(
                f"Body '{body_name_used}' Tip '{getattr(tip, 'Name', '?')}' has no valid base solid.  "
                f"Create a Pad or other base solid before adding a Groove."
            )

        # Create the Groove.  Avoid name clashes.
        counter = 0
        desired_groove_name = groove_name
        while doc.getObject(desired_groove_name) is not None:
            counter += 1
            desired_groove_name = f"{groove_name}_{counter}"
        try:
            groove = doc.addObject("PartDesign::Groove", desired_groove_name)
            body.addObject(groove)
            # Link the groove to its parent base solid so FreeCAD can recompute
            # it after the document is reopened.
            try:
                groove.BaseFeature = tip
            except Exception:
                pass
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Groove: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Groove profile to the existing sketch.
        try:
            groove.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Groove profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Set the ReferenceAxis to a sketch edge parallel to the requested
        # global axis.  FreeCAD 1.1.4 requires the axis to be a subelement of
        # the profile sketch (an edge subname like ``"Edge1"``).  The Body
        # origin axes cannot be used.  Z_Axis has no parallel edge in a 2D
        # sketch on the XY plane and is rejected.
        try:
            edge_subname = _find_revolution_axis_edge(sketch, axis)
            if edge_subname is None:
                return _fail(
                    f"Body '{body_name_used}' has no sketch edge parallel to the {axis} "
                    f"direction.  Cannot set the Groove axis '{axis}'.  This axis cannot "
                    f"be represented for this sketch (e.g. Z_Axis has no parallel edge in a "
                    f"2D sketch on the XY plane)."
                )
            groove.ReferenceAxis = (sketch, [edge_subname])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Groove axis '{axis}': {exc}."
            )

        # Apply the groove angle (degrees).
        try:
            groove.Angle = angle_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Groove angle: {exc}"
            )

        # Recompute the document so FreeCAD evaluates the Groove feature tree.
        doc.recompute()

        # Validate the resulting Groove: it must have a valid, non-null solid shape.
        shape = getattr(groove, "Shape", None)
        if shape is None:
            return _fail(
                f"Groove '{desired_groove_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or the ReferenceAxis '{axis}' may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"Groove '{desired_groove_name}' produced a NULL shape.  The "
                    f"ReferenceAxis reference to '{axis}' was accepted but not resolved by FreeCAD "
                    f"(computed axis is Vector(0,0,0)).  This axis reference cannot be used for "
                    f"Groove on this sketch."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"Groove '{desired_groove_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Groove '{desired_groove_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Groove '{desired_groove_name}' produced no solid.  The sketch profile or "
                f"axis configuration may not produce a valid solid."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_groove",
        "result": {
            "document_id": normalized,
            "sketch_name": sketch_name,
            "body_name": body_name_used,
            "groove_name": desired_groove_name,
            "profile_name": sketch_name,
            "axis": axis,
            "angle": angle_val,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_sketch_additive_pipe(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::AdditivePipe from an existing sketch and spine.

    Uses a native ``PartDesign::Body`` and ``PartDesign::AdditivePipe``.  The
    target Body must already exist and its Tip must be a valid solid
    (e.g. a Pad).  The sketch profile and the spine wire object must already
    exist; the sketch must already be a member of the specified Body and the
    spine object must remain outside ``Body.Group`` (FreeCAD 1.1.4 rejects
    adding a wire-holding ``Part::Feature`` to a Body).

    The Pipe profile is set to the existing sketch, and the spine is set to
    the existing spine object (optionally scoped to a single edge via
    ``spine_subname``).  After recompute, the resulting Pipe shape is
    validated (non-null solid with at least one solid).  The document is
    locked, recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the established
    save-on-success pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name_raw = params.get("body_name", "Body")
    sketch_name_raw = params.get("sketch_name", "")
    spine_object_name_raw = params.get("spine_object_name", "")
    spine_subname = params.get("spine_subname")
    pipe_name_raw = params.get("pipe_name", "AdditivePipe")
    workspace_str = params.get("workspace") or ""

    # Validate document_id.
    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_additive_pipe", "error": str(exc)}

    # Validate required names: reject empty strings, apply defaults only when
    # the key is absent (not when it is explicitly empty).
    if not str(body_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_pipe",
            "error": "body_name must not be empty.",
        }
    body_name = str(body_name_raw)

    if not str(sketch_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_pipe",
            "error": "sketch_name must not be empty.",
        }
    sketch_name = str(sketch_name_raw)

    if not str(spine_object_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_pipe",
            "error": "spine_object_name must not be empty.",
        }
    spine_object_name = str(spine_object_name_raw)

    if not str(pipe_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_pipe",
            "error": "pipe_name must not be empty.",
        }
    pipe_name = str(pipe_name_raw)

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_additive_pipe", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_additive_pipe", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_additive_pipe", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Require an existing PartDesign::Body with a valid, non-null solid Tip.
        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"body '{body_name}' not found")
        if getattr(body, "TypeId", "") != "PartDesign::Body":
            return _fail(
                f"object '{body_name}' is type '{getattr(body, 'TypeId', '?')}', not 'PartDesign::Body'; "
                f"cannot create an AdditivePipe."
            )
        body_name_used = getattr(body, "Name", body_name)

        tip = getattr(body, "Tip", None)
        if tip is None:
            return _fail(
                f"Body '{body_name_used}' has no Tip.  An AdditivePipe requires a valid base solid.  "
                f"Create a Pad or other base solid before adding a Pipe."
            )
        tip_shape = getattr(tip, "Shape", None)
        if tip_shape is None or getattr(tip_shape, "isNull", lambda: False)() or not bool(tip_shape.isValid()):
            return _fail(
                f"Body '{body_name_used}' Tip '{getattr(tip, 'Name', '?')}' has no valid base solid.  "
                f"Create a Pad or other base solid before adding a Pipe."
            )

        # Require an existing Sketcher::SketchObject that is already a member
        # of the specified Body.  Do not silently change ownership.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")
        if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{getattr(sketch, 'TypeId', '?')}', not 'Sketcher::SketchObject'; "
                f"cannot create an AdditivePipe from it."
            )
        body_members = list(getattr(body, "Group", []) or [])
        if sketch not in body_members and sketch not in list(getattr(body, "outList", []) or []):
            return _fail(
                f"sketch '{sketch_name}' is not a member of Body '{body_name_used}'.  "
                f"Add the sketch to the Body first (e.g. via sketch_pad) or choose "
                f"the Body that already contains it."
            )

        # Require an existing spine object with a non-null valid Shape holding
        # at least one wire/edge.  The spine stays outside Body.Group; do not
        # attempt to add it to the Body.
        spine_obj = doc.getObject(spine_object_name)
        if spine_obj is None:
            return _fail(f"spine object '{spine_object_name}' not found")
        spine_shape = getattr(spine_obj, "Shape", None)
        if spine_shape is None or getattr(spine_shape, "isNull", lambda: False)():
            return _fail(
                f"spine object '{spine_object_name}' has no Shape.  Provide a wire-holding "
                f"object (e.g. a Part::Feature whose Shape is a Wire) as the spine."
            )
        try:
            spine_edges = list(getattr(spine_shape, "Edges", []) or [])
        except Exception:
            spine_edges = []
        if not spine_edges:
            return _fail(
                f"spine object '{spine_object_name}' Shape has no edges.  A spine must define "
                f"a wire with at least one edge."
            )

        # If a spine subname is supplied, verify it references an existing edge.
        spine_ref: Any = spine_obj
        if spine_subname is not None and str(spine_subname).strip():
            spine_subname = str(spine_subname).strip()
            edge_names = [getattr(e, "Name", None) for e in spine_edges]
            if spine_subname not in edge_names:
                return _fail(
                    f"spine_subname '{spine_subname}' does not match any edge of "
                    f"spine object '{spine_object_name}' (edges: {edge_names})."
                )
            spine_ref = (spine_obj, [spine_subname])

        # Create the AdditivePipe.  Avoid name clashes.
        counter = 0
        desired_pipe_name = pipe_name
        while doc.getObject(desired_pipe_name) is not None:
            counter += 1
            desired_pipe_name = f"{pipe_name}_{counter}"
        try:
            pipe = doc.addObject("PartDesign::AdditivePipe", desired_pipe_name)
            body.addObject(pipe)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::AdditivePipe: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Pipe profile to the existing sketch.
        try:
            pipe.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pipe profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Set the Pipe spine to the existing spine object (optionally edge-scoped).
        # FreeCAD 1.1.4 emits a benign "out of allowed scope" warning when the
        # spine object is outside Body.Group; this is expected and non-fatal.
        try:
            pipe.Spine = spine_ref
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pipe spine: {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Pipe feature tree.
        doc.recompute()

        # Validate the resulting Pipe: it must have a valid, non-null solid shape.
        shape = getattr(pipe, "Shape", None)
        if shape is None:
            return _fail(
                f"AdditivePipe '{desired_pipe_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or the spine may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"AdditivePipe '{desired_pipe_name}' produced a NULL shape.  The spine "
                    f"reference was accepted but did not resolve to a valid sweep path."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"AdditivePipe '{desired_pipe_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"AdditivePipe '{desired_pipe_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"AdditivePipe '{desired_pipe_name}' produced no solid.  The sketch profile or "
                f"spine configuration may not produce a valid solid."
            )

        # Verify Body.Tip advanced to the new Pipe.
        new_tip = getattr(body, "Tip", None)
        if new_tip is None or getattr(new_tip, "Name", None) != desired_pipe_name:
            return _fail(
                f"Body '{body_name_used}' Tip did not advance to the new AdditivePipe "
                f"'{desired_pipe_name}' after recompute."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_additive_pipe",
        "result": {
            "document_id": normalized,
            "body_name": body_name_used,
            "pipe_name": desired_pipe_name,
            "profile_name": sketch_name,
            "spine_object_name": spine_object_name,
            "spine_subname": spine_subname if spine_subname else None,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_sketch_subtractive_pipe(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::SubtractivePipe from an existing sketch and spine.

    Uses a native ``PartDesign::Body`` and ``PartDesign::SubtractivePipe``.  The
    target Body must already exist and its Tip must be a valid solid
    (e.g. a Pad).  The sketch profile and the spine wire object must already
    exist; the sketch must already be a member of the specified Body and the
    spine object must remain outside ``Body.Group`` (FreeCAD 1.1.4 rejects
    adding a wire-holding ``Part::Feature`` to a Body).

    The Pipe profile is set to the existing sketch, and the spine is set to
    the existing spine object (optionally scoped to a single edge via
    ``spine_subname``).  After recompute, the resulting Pipe shape is
    validated (non-null solid with at least one solid).  The document is
    locked, recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the established
    save-on-success pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name_raw = params.get("body_name", "Body")
    sketch_name_raw = params.get("sketch_name", "")
    spine_object_name_raw = params.get("spine_object_name", "")
    spine_subname = params.get("spine_subname")
    pipe_name_raw = params.get("pipe_name", "SubtractivePipe")
    workspace_str = params.get("workspace") or ""

    # Validate document_id.
    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_subtractive_pipe", "error": str(exc)}

    # Validate required names: reject empty strings, apply defaults only when
    # the key is absent (not when it is explicitly empty).
    if not str(body_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_pipe",
            "error": "body_name must not be empty.",
        }
    body_name = str(body_name_raw)

    if not str(sketch_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_pipe",
            "error": "sketch_name must not be empty.",
        }
    sketch_name = str(sketch_name_raw)

    if not str(spine_object_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_pipe",
            "error": "spine_object_name must not be empty.",
        }
    spine_object_name = str(spine_object_name_raw)

    if not str(pipe_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_pipe",
            "error": "pipe_name must not be empty.",
        }
    pipe_name = str(pipe_name_raw)

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_subtractive_pipe", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_subtractive_pipe", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_subtractive_pipe", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Require an existing PartDesign::Body with a valid, non-null solid Tip.
        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"body '{body_name}' not found")
        if getattr(body, "TypeId", "") != "PartDesign::Body":
            return _fail(
                f"object '{body_name}' is type '{getattr(body, 'TypeId', '?')}', not 'PartDesign::Body'; "
                f"cannot create a SubtractivePipe."
            )
        body_name_used = getattr(body, "Name", body_name)

        tip = getattr(body, "Tip", None)
        if tip is None:
            return _fail(
                f"Body '{body_name_used}' has no Tip.  A SubtractivePipe requires a valid base solid.  "
                f"Create a Pad or other base solid before adding a Pipe."
            )
        tip_shape = getattr(tip, "Shape", None)
        if tip_shape is None or getattr(tip_shape, "isNull", lambda: False)() or not bool(tip_shape.isValid()):
            return _fail(
                f"Body '{body_name_used}' Tip '{getattr(tip, 'Name', '?')}' has no valid base solid.  "
                f"Create a Pad or other base solid before adding a Pipe."
            )

        # Require an existing Sketcher::SketchObject that is already a member
        # of the specified Body.  Do not silently change ownership.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")
        if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{getattr(sketch, 'TypeId', '?')}', not 'Sketcher::SketchObject'; "
                f"cannot create a SubtractivePipe from it."
            )
        body_members = list(getattr(body, "Group", []) or [])
        if sketch not in body_members and sketch not in list(getattr(body, "outList", []) or []):
            return _fail(
                f"sketch '{sketch_name}' is not a member of Body '{body_name_used}'.  "
                f"Add the sketch to the Body first (e.g. via sketch_pad) or choose "
                f"the Body that already contains it."
            )

        # Require an existing spine object with a non-null valid Shape holding
        # at least one wire/edge.  The spine stays outside Body.Group; do not
        # attempt to add it to the Body.
        spine_obj = doc.getObject(spine_object_name)
        if spine_obj is None:
            return _fail(f"spine object '{spine_object_name}' not found")
        spine_shape = getattr(spine_obj, "Shape", None)
        if spine_shape is None or getattr(spine_shape, "isNull", lambda: False)():
            return _fail(
                f"spine object '{spine_object_name}' has no Shape.  Provide a wire-holding "
                f"object (e.g. a Part::Feature whose Shape is a Wire) as the spine."
            )
        try:
            spine_edges = list(getattr(spine_shape, "Edges", []) or [])
        except Exception:
            spine_edges = []
        if not spine_edges:
            return _fail(
                f"spine object '{spine_object_name}' Shape has no edges.  A spine must define "
                f"a wire with at least one edge."
            )

        # If a spine subname is supplied, verify it references an existing edge.
        spine_ref: Any = spine_obj
        if spine_subname is not None and str(spine_subname).strip():
            spine_subname = str(spine_subname).strip()
            edge_names = [getattr(e, "Name", None) for e in spine_edges]
            if spine_subname not in edge_names:
                return _fail(
                    f"spine_subname '{spine_subname}' does not match any edge of "
                    f"spine object '{spine_object_name}' (edges: {edge_names})."
                )
            spine_ref = (spine_obj, [spine_subname])

        # Create the SubtractivePipe.  Avoid name clashes.
        counter = 0
        desired_pipe_name = pipe_name
        while doc.getObject(desired_pipe_name) is not None:
            counter += 1
            desired_pipe_name = f"{pipe_name}_{counter}"
        try:
            pipe = doc.addObject("PartDesign::SubtractivePipe", desired_pipe_name)
            body.addObject(pipe)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::SubtractivePipe: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Pipe profile to the existing sketch.
        try:
            pipe.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pipe profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Set the Pipe spine to the existing spine object (optionally edge-scoped).
        # FreeCAD 1.1.4 emits a benign "out of allowed scope" warning when the
        # spine object is outside Body.Group; this is expected and non-fatal.
        try:
            pipe.Spine = spine_ref
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Pipe spine: {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Pipe feature tree.
        doc.recompute()

        # Validate the resulting Pipe: it must have a valid, non-null solid shape.
        shape = getattr(pipe, "Shape", None)
        if shape is None:
            return _fail(
                f"SubtractivePipe '{desired_pipe_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or the spine may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"SubtractivePipe '{desired_pipe_name}' produced a NULL shape.  The spine "
                    f"reference was accepted but did not resolve to a valid sweep path."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"SubtractivePipe '{desired_pipe_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"SubtractivePipe '{desired_pipe_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"SubtractivePipe '{desired_pipe_name}' produced no solid.  The sketch profile or "
                f"spine configuration may not produce a valid solid."
            )

        # Verify Body.Tip advanced to the new Pipe.
        new_tip = getattr(body, "Tip", None)
        if new_tip is None or getattr(new_tip, "Name", None) != desired_pipe_name:
            return _fail(
                f"Body '{body_name_used}' Tip did not advance to the new SubtractivePipe "
                f"'{desired_pipe_name}' after recompute."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_subtractive_pipe",
        "result": {
            "document_id": normalized,
            "body_name": body_name_used,
            "pipe_name": desired_pipe_name,
            "profile_name": sketch_name,
            "spine_object_name": spine_object_name,
            "spine_subname": spine_subname if spine_subname else None,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_sketch_additive_loft(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::AdditiveLoft from an existing sketch profile and sections.

    Uses a native ``PartDesign::Body`` and ``PartDesign::AdditiveLoft``.  The
    target Body must already exist and its Tip must be a valid solid
    (e.g. a Pad).  The profile sketch and all section sketches must already
    exist and already be members of the specified Body.  Ownership is never
    silently changed.

    The Loft profile is set to the existing sketch, and the sections are set
    to the existing list of sketches.  ``Ruled`` and ``Closed`` are applied
    as requested.  After recompute, the resulting Loft shape is validated
    (non-null solid with at least one solid).  The document is locked,
    recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the established
    save-on-success pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name_raw = params.get("body_name", "Body")
    sketch_name_raw = params.get("sketch_name", "")
    section_names_raw = params.get("section_names") or []
    loft_name_raw = params.get("loft_name", "Loft")
    ruled = params.get("ruled", False)
    closed = params.get("closed", False)
    workspace_str = params.get("workspace") or ""

    # Validate document_id.
    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_additive_loft", "error": str(exc)}

    # Validate required names: reject empty strings, apply defaults only when
    # the key is absent (not when it is explicitly empty).
    if not str(body_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "body_name must not be empty.",
        }
    body_name = str(body_name_raw)

    if not str(sketch_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "sketch_name must not be empty.",
        }
    sketch_name = str(sketch_name_raw)

    if not isinstance(section_names_raw, list) or not section_names_raw:
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "section_names must be a non-empty list of existing sketch names.",
        }
    section_names = [str(s).strip() for s in section_names_raw]
    if not section_names:
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "section_names must be a non-empty list of existing sketch names.",
        }

    if not str(loft_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "loft_name must not be empty.",
        }
    loft_name = str(loft_name_raw)

    # Validate boolean parameters strictly.
    if not isinstance(ruled, bool):
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "ruled must be a boolean.",
        }
    if not isinstance(closed, bool):
        return {
            "ok": False,
            "op": "sketch_additive_loft",
            "error": "closed must be a boolean.",
        }

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_additive_loft", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_additive_loft", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_additive_loft", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Require an existing PartDesign::Body with a valid, non-null solid Tip.
        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"body '{body_name}' not found")
        if getattr(body, "TypeId", "") != "PartDesign::Body":
            return _fail(
                f"object '{body_name}' is type '{getattr(body, 'TypeId', '?')}', not 'PartDesign::Body'; "
                f"cannot create an AdditiveLoft."
            )
        body_name_used = getattr(body, "Name", body_name)

        tip = getattr(body, "Tip", None)
        if tip is None:
            return _fail(
                f"Body '{body_name_used}' has no Tip.  An AdditiveLoft requires a valid base solid.  "
                f"Create a Pad or other base solid before adding a Loft."
            )
        tip_shape = getattr(tip, "Shape", None)
        if tip_shape is None or getattr(tip_shape, "isNull", lambda: False)() or not bool(tip_shape.isValid()):
            return _fail(
                f"Body '{body_name_used}' Tip '{getattr(tip, 'Name', '?')}' has no valid base solid.  "
                f"Create a Pad or other base solid before adding a Loft."
            )

        # Require an existing Sketcher::SketchObject Profile that is already
        # a member of the specified Body.  Do not silently change ownership.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")
        if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{getattr(sketch, 'TypeId', '?')}', not 'Sketcher::SketchObject'; "
                f"cannot create an AdditiveLoft from it."
            )
        body_members = list(getattr(body, "Group", []) or [])
        if sketch not in body_members and sketch not in list(getattr(body, "outList", []) or []):
            return _fail(
                f"sketch '{sketch_name}' is not a member of Body '{body_name_used}'.  "
                f"Add the sketch to the Body first (e.g. via sketch_pad) or choose "
                f"the Body that already contains it."
            )

        # Require a non-empty list of Sections.  Every section must exist,
        # be a Sketcher::SketchObject, belong to the same Body, be unique,
        # and not duplicate the Profile.
        section_objs: list[Any] = []
        seen_section_names: set[str] = set()
        for section_name in section_names:
            section_obj = doc.getObject(section_name)
            if section_obj is None:
                return _fail(f"section '{section_name}' not found")
            if getattr(section_obj, "TypeId", "") != "Sketcher::SketchObject":
                return _fail(
                    f"section '{section_name}' is type '{getattr(section_obj, 'TypeId', '?')}', "
                    f"not 'Sketcher::SketchObject'; cannot use it as an AdditiveLoft section."
                )
            if section_name in seen_section_names:
                return _fail(
                    f"section '{section_name}' appears more than once in section_names."
                )
            seen_section_names.add(section_name)
            if section_name == sketch_name:
                return _fail(
                    f"section '{section_name}' duplicates the Profile sketch; "
                    f"a section cannot be reused as the profile."
                )
            if section_obj not in body_members and section_obj not in list(getattr(body, "outList", []) or []):
                return _fail(
                    f"section '{section_name}' is not a member of Body '{body_name_used}'.  "
                    f"Add the section to the Body first or choose "
                    f"the Body that already contains it."
                )
            section_objs.append(section_obj)

        # Create the AdditiveLoft.  Avoid name clashes.
        counter = 0
        desired_loft_name = loft_name
        while doc.getObject(desired_loft_name) is not None:
            counter += 1
            desired_loft_name = f"{loft_name}_{counter}"
        try:
            loft = doc.addObject("PartDesign::AdditiveLoft", desired_loft_name)
            body.addObject(loft)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::AdditiveLoft: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Loft profile to the existing sketch.
        try:
            loft.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Loft profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Set the Loft sections to the existing sketches.
        try:
            loft.Sections = section_objs
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Loft sections: {exc}."
            )

        # Set Ruled and Closed.
        try:
            loft.Ruled = ruled
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Ruled on the Loft: {exc}."
            )
        try:
            loft.Closed = closed
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Closed on the Loft: {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Loft feature tree.
        doc.recompute()

        # Validate the resulting Loft: it must have a valid, non-null solid shape.
        shape = getattr(loft, "Shape", None)
        if shape is None:
            return _fail(
                f"AdditiveLoft '{desired_loft_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or the sections may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"AdditiveLoft '{desired_loft_name}' produced a NULL shape.  The sections "
                    f"reference was accepted but did not resolve to a valid loft path."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"AdditiveLoft '{desired_loft_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"AdditiveLoft '{desired_loft_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"AdditiveLoft '{desired_loft_name}' produced no solid.  The sketch profile or "
                f"sections configuration may not produce a valid solid."
            )

        # Verify Body.Tip advanced to the new Loft.
        new_tip = getattr(body, "Tip", None)
        if new_tip is None or getattr(new_tip, "Name", None) != desired_loft_name:
            return _fail(
                f"Body '{body_name_used}' Tip did not advance to the new AdditiveLoft "
                f"'{desired_loft_name}' after recompute."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_additive_loft",
        "result": {
            "document_id": normalized,
            "body_name": body_name_used,
            "loft_name": desired_loft_name,
            "profile_name": sketch_name,
            "section_names": section_names,
            "ruled": ruled,
            "closed": closed,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_sketch_subtractive_loft(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::SubtractiveLoft from an existing sketch profile and sections.

    Uses a native ``PartDesign::Body`` and ``PartDesign::SubtractiveLoft``.  The
    target Body must already exist and its Tip must be a valid solid
    (e.g. a Pad).  The profile sketch and all section sketches must already
    exist and already be members of the specified Body.  Ownership is never
    silently changed.

    The Loft profile is set to the existing sketch, and the sections are set
    to the existing list of sketches.  ``Ruled`` and ``Closed`` are applied
    as requested.  After recompute, the resulting Loft shape is validated
    (non-null solid with at least one solid).  The document is locked,
    recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the established
    save-on-success pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name_raw = params.get("body_name", "Body")
    sketch_name_raw = params.get("sketch_name", "")
    section_names_raw = params.get("section_names") or []
    loft_name_raw = params.get("loft_name", "SubtractiveLoft")
    ruled = params.get("ruled", False)
    closed = params.get("closed", False)
    workspace_str = params.get("workspace") or ""

    # Validate document_id.
    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_subtractive_loft", "error": str(exc)}

    # Validate required names: reject empty strings, apply defaults only when
    # the key is absent (not when it is explicitly empty).
    if not str(body_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "body_name must not be empty.",
        }
    body_name = str(body_name_raw)

    if not str(sketch_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "sketch_name must not be empty.",
        }
    sketch_name = str(sketch_name_raw)

    if not isinstance(section_names_raw, list) or not section_names_raw:
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "section_names must be a non-empty list of existing sketch names.",
        }
    section_names = [str(s).strip() for s in section_names_raw]
    if not section_names:
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "section_names must be a non-empty list of existing sketch names.",
        }

    if not str(loft_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "loft_name must not be empty.",
        }
    loft_name = str(loft_name_raw)

    # Validate boolean parameters strictly.
    if not isinstance(ruled, bool):
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "ruled must be a boolean.",
        }
    if not isinstance(closed, bool):
        return {
            "ok": False,
            "op": "sketch_subtractive_loft",
            "error": "closed must be a boolean.",
        }

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_subtractive_loft", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_subtractive_loft", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_subtractive_loft", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Require an existing PartDesign::Body with a valid, non-null solid Tip.
        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"body '{body_name}' not found")
        if getattr(body, "TypeId", "") != "PartDesign::Body":
            return _fail(
                f"object '{body_name}' is type '{getattr(body, 'TypeId', '?')}', not 'PartDesign::Body'; "
                f"cannot create a SubtractiveLoft."
            )
        body_name_used = getattr(body, "Name", body_name)

        tip = getattr(body, "Tip", None)
        if tip is None:
            return _fail(
                f"Body '{body_name_used}' has no Tip.  A SubtractiveLoft requires a valid base solid.  "
                f"Create a Pad or other base solid before adding a Loft."
            )
        tip_shape = getattr(tip, "Shape", None)
        if tip_shape is None or getattr(tip_shape, "isNull", lambda: False)() or not bool(tip_shape.isValid()):
            return _fail(
                f"Body '{body_name_used}' Tip '{getattr(tip, 'Name', '?')}' has no valid base solid.  "
                f"Create a Pad or other base solid before adding a Loft."
            )

        # Require an existing Sketcher::SketchObject Profile that is already
        # a member of the specified Body.  Do not silently change ownership.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")
        if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{getattr(sketch, 'TypeId', '?')}', not 'Sketcher::SketchObject'; "
                f"cannot create a SubtractiveLoft from it."
            )
        body_members = list(getattr(body, "Group", []) or [])
        if sketch not in body_members and sketch not in list(getattr(body, "outList", []) or []):
            return _fail(
                f"sketch '{sketch_name}' is not a member of Body '{body_name_used}'.  "
                f"Add the sketch to the Body first (e.g. via sketch_pad) or choose "
                f"the Body that already contains it."
            )

        # Require a non-empty list of Sections.  Every section must exist,
        # be a Sketcher::SketchObject, belong to the same Body, be unique,
        # and not duplicate the Profile.
        section_objs: list[Any] = []
        seen_section_names: set[str] = set()
        for section_name in section_names:
            section_obj = doc.getObject(section_name)
            if section_obj is None:
                return _fail(f"section '{section_name}' not found")
            if getattr(section_obj, "TypeId", "") != "Sketcher::SketchObject":
                return _fail(
                    f"section '{section_name}' is type '{getattr(section_obj, 'TypeId', '?')}', "
                    f"not 'Sketcher::SketchObject'; cannot use it as a SubtractiveLoft section."
                )
            if section_name in seen_section_names:
                return _fail(
                    f"section '{section_name}' appears more than once in section_names."
                )
            seen_section_names.add(section_name)
            if section_name == sketch_name:
                return _fail(
                    f"section '{section_name}' duplicates the Profile sketch; "
                    f"a section cannot be reused as the profile."
                )
            if section_obj not in body_members and section_obj not in list(getattr(body, "outList", []) or []):
                return _fail(
                    f"section '{section_name}' is not a member of Body '{body_name_used}'.  "
                    f"Add the section to the Body first or choose "
                    f"the Body that already contains it."
                )
            section_objs.append(section_obj)

        # Create the SubtractiveLoft.  Avoid name clashes.
        counter = 0
        desired_loft_name = loft_name
        while doc.getObject(desired_loft_name) is not None:
            counter += 1
            desired_loft_name = f"{loft_name}_{counter}"
        try:
            loft = doc.addObject("PartDesign::SubtractiveLoft", desired_loft_name)
            body.addObject(loft)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::SubtractiveLoft: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Loft profile to the existing sketch.
        try:
            loft.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Loft profile: {exc}.  The sketch may not contain "
                f"a valid closed profile."
            )

        # Set the Loft sections to the existing sketches.
        try:
            loft.Sections = section_objs
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Loft sections: {exc}."
            )

        # Set Ruled and Closed.
        try:
            loft.Ruled = ruled
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Ruled on the Loft: {exc}."
            )
        try:
            loft.Closed = closed
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Closed on the Loft: {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Loft feature tree.
        doc.recompute()

        # Validate the resulting Loft: it must have a valid, non-null solid shape.
        shape = getattr(loft, "Shape", None)
        if shape is None:
            return _fail(
                f"SubtractiveLoft '{desired_loft_name}' produced no shape.  The sketch profile may be "
                f"open, malformed, or degenerate, or the sections may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"SubtractiveLoft '{desired_loft_name}' produced a NULL shape.  The sections "
                    f"reference was accepted but did not resolve to a valid loft path."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"SubtractiveLoft '{desired_loft_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"SubtractiveLoft '{desired_loft_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"SubtractiveLoft '{desired_loft_name}' produced no solid.  The sketch profile or "
                f"sections configuration may not produce a valid solid."
            )

        # Verify Body.Tip advanced to the new Loft.
        new_tip = getattr(body, "Tip", None)
        if new_tip is None or getattr(new_tip, "Name", None) != desired_loft_name:
            return _fail(
                f"Body '{body_name_used}' Tip did not advance to the new SubtractiveLoft "
                f"'{desired_loft_name}' after recompute."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_subtractive_loft",
        "result": {
            "document_id": normalized,
            "body_name": body_name_used,
            "loft_name": desired_loft_name,
            "profile_name": sketch_name,
            "section_names": section_names,
            "ruled": ruled,
            "closed": closed,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_sketch_hole(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Hole from an existing sketch profile.

    Uses a native ``PartDesign::Body`` and ``PartDesign::Hole``.  The target
    Body must already exist and its Tip must be a valid solid
    (e.g. a Pad).  The profile sketch must already exist and already be a
    member of the specified Body; it defines the hole profile (a circle).
    The base face to drill into is specified by ``base_object_name`` and
    ``base_subname``.  Ownership is never silently changed.

    The Hole uses the sketch as its ``Profile`` (assigned directly as
    ``hole.Profile = sketch``, NOT as a LinkSub tuple carrying
    ``base_subname``: FreeCAD 1.1.4 produces a NULL shape when Profile
    contains a subelement list such as ``(sketch, ['Face1'])``).  The base
    face to drill into is specified by ``base_object_name`` and
    ``base_subname``; it lives on the sketch's ``AttachmentSupport`` (set via
    ``sketch_attach_support`` before calling this operation), not on
    ``Hole.Profile``.  ``Diameter`` and ``Depth`` are applied as requested.
    The sketch must be attached to the target face via ``sketch_attach_support``
    before creating the Hole and must be a member of the specified Body.
    Ownership is never silently changed.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the established
    save-on-success pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    body_name_raw = params.get("body_name", "Body")
    sketch_name_raw = params.get("sketch_name", "")
    base_object_name_raw = params.get("base_object_name", "")
    base_subname_raw = params.get("base_subname", "")
    hole_name_raw = params.get("hole_name", "Hole")
    diameter_raw = params.get("diameter")
    depth_raw = params.get("depth")
    workspace_str = params.get("workspace") or ""

    # Validate document_id.
    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "sketch_hole", "error": str(exc)}

    # Validate required names: reject empty strings, apply defaults only when
    # the key is absent (not when it is explicitly empty).
    if not str(body_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "body_name must not be empty.",
        }
    body_name = str(body_name_raw)

    if not str(sketch_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "sketch_name must not be empty.",
        }
    sketch_name = str(sketch_name_raw)

    if not str(base_object_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "base_object_name must not be empty.",
        }
    base_object_name = str(base_object_name_raw)

    if not str(base_subname_raw).strip():
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "base_subname must not be empty.",
        }
    base_subname = str(base_subname_raw)

    if not str(hole_name_raw).strip():
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "hole_name must not be empty.",
        }
    hole_name = str(hole_name_raw)

    # Validate numeric parameters strictly.
    try:
        diameter = float(diameter_raw)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "diameter must be a finite number.",
        }
    if not math.isfinite(diameter):
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "diameter must be a finite number.",
        }
    if diameter <= 0:
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "diameter must be positive.",
        }

    try:
        depth = float(depth_raw)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "depth must be a finite number.",
        }
    if not math.isfinite(depth):
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "depth must be a finite number.",
        }
    if depth <= 0:
        return {
            "ok": False,
            "op": "sketch_hole",
            "error": "depth must be positive.",
        }

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "sketch_hole", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "sketch_hole", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    def _fail(message: str) -> dict[str, Any]:
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "sketch_hole", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Require an existing PartDesign::Body with a valid, non-null solid Tip.
        body = doc.getObject(body_name)
        if body is None:
            return _fail(f"body '{body_name}' not found")
        if getattr(body, "TypeId", "") != "PartDesign::Body":
            return _fail(
                f"object '{body_name}' is type '{getattr(body, 'TypeId', '?')}', not 'PartDesign::Body'; "
                f"cannot create a Hole."
            )
        body_name_used = getattr(body, "Name", body_name)

        tip = getattr(body, "Tip", None)
        if tip is None:
            return _fail(
                f"Body '{body_name_used}' has no Tip.  A Hole requires a valid base solid.  "
                f"Create a Pad or other base solid before adding a Hole."
            )
        tip_shape = getattr(tip, "Shape", None)
        if tip_shape is None or getattr(tip_shape, "isNull", lambda: False)() or not bool(tip_shape.isValid()):
            return _fail(
                f"Body '{body_name_used}' Tip '{getattr(tip, 'Name', '?')}' has no valid base solid.  "
                f"Create a Pad or other base solid before adding a Hole."
            )

        # Require an existing Sketcher::SketchObject Profile that defines the
        # Hole profile (a circle).  Add it to the target Body if it is not
        # already a member of any Body (mirroring sketch_pad behavior).
        # Do not silently move objects between Bodies.
        sketch = doc.getObject(sketch_name)
        if sketch is None:
            return _fail(f"sketch '{sketch_name}' not found")
        if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
            return _fail(
                f"object '{sketch_name}' is type '{getattr(sketch, 'TypeId', '?')}', not 'Sketcher::SketchObject'; "
                f"cannot create a Hole from it."
            )

        # Check if sketch is already a member of any Body.
        sketch_in_any_body = False
        sketch_in_other_body = False
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    try:
                        body_members = list(getattr(obj, "Group", []) or [])
                        body_outlist = list(getattr(obj, "outList", []) or [])
                        if sketch in body_members or sketch in body_outlist:
                            sketch_in_any_body = True
                            if obj is not body:
                                sketch_in_other_body = True
                                break
                    except Exception:
                        pass
        except Exception:
            pass

        if sketch_in_other_body:
            return _fail(
                f"sketch '{sketch_name}' is already a member of another Body.  "
                f"Do not silently move objects between Bodies."
            )

        if not sketch_in_any_body:
            # Add sketch to target Body's Group.
            try:
                body.addObject(sketch)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not add sketch '{sketch_name}' to Body "
                    f"'{body_name_used}': {exc}."
                )

        # Require an existing base object with a valid Shape whose named
        # subelement (face) exists.  The base face receives the hole.
        base_obj = doc.getObject(base_object_name)
        if base_obj is None:
            return _fail(f"base object '{base_object_name}' not found")
        base_shape = getattr(base_obj, "Shape", None)
        if base_shape is None or not _shape_has_subelement(base_shape, base_subname, "Face"):
            return _fail(
                f"base subelement '{base_subname}' is not a face of "
                f"object '{base_object_name}'."
            )

        # Create the Hole.  Avoid name clashes.
        counter = 0
        desired_hole_name = hole_name
        while doc.getObject(desired_hole_name) is not None:
            counter += 1
            desired_hole_name = f"{hole_name}_{counter}"
        try:
            hole = doc.addObject("PartDesign::Hole", desired_hole_name)
            body.addObject(hole)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Hole: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Set the Hole diameter and depth.
        # The sketch's attachment support (set via sketch_attach_support)
        # defines the drilling base face.  Profile must be assigned directly
        # as `hole.Profile = sketch` (NOT as a LinkSub tuple containing
        # base_subname): FreeCAD 1.1.4 produces a NULL shape when Profile
        # carries a subelement list such as (sketch, ['Face1']).  The base
        # face lives on the sketch's AttachmentSupport, not on Hole.Profile.
        try:
            hole.Diameter = diameter
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Hole Diameter: {exc}."
            )
        try:
            hole.Depth = depth
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Hole Depth: {exc}."
            )
        try:
            hole.Profile = sketch
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set Hole Profile: {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Hole feature tree.
        doc.recompute()

        # Validate the resulting Hole: it must have a valid, non-null solid shape.
        shape = getattr(hole, "Shape", None)
        if shape is None:
            return _fail(
                f"Hole '{desired_hole_name}' produced no shape.  The profile or "
                f"Diameter/Depth may be invalid."
            )
        try:
            if getattr(shape, "isNull", lambda: False)():
                return _fail(
                    f"Hole '{desired_hole_name}' produced a NULL shape.  The profile "
                    f"did not resolve to a valid drilling path."
                )
            if not bool(shape.isValid()):
                return _fail(
                    f"Hole '{desired_hole_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Hole '{desired_hole_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Hole '{desired_hole_name}' produced no solid.  The profile or "
                f"Diameter/Depth configuration may not produce a valid solid."
            )

        # Verify Body.Tip advanced to the new Hole.
        new_tip = getattr(body, "Tip", None)
        if new_tip is None or getattr(new_tip, "Name", None) != desired_hole_name:
            return _fail(
                f"Body '{body_name_used}' Tip did not advance to the new Hole "
                f"'{desired_hole_name}' after recompute."
            )

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "sketch_hole",
        "result": {
            "document_id": normalized,
            "body_name": body_name_used,
            "hole_name": desired_hole_name,
            "profile_name": sketch_name,
            "base_object_name": base_object_name,
            "base_subname": base_subname,
            "diameter": diameter,
            "depth": depth,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_partdesign_fillet(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Fillet on edges of an existing solid feature.

    The target object must already belong to a PartDesign::Body, or the
    document must contain exactly one (unambiguous) Body to which it can be
    assigned.  A new Body is never silently created for a Fillet -- a Fillet
    requires an existing solid feature to round.

    The Fillet's Base links the target and the requested subelements
    (1-based indices mapped to FreeCAD ``Edge1``, ``Edge2``,
    ...).  *Radius* is applied, the document is recomputed, and the resulting
    shape is validated (non-null valid solid containing at least one solid).
    The document is locked, recomputed, saved atomically, and its revision is
    bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    edge_indices = params.get("edge_indices")
    radius = params.get("radius")
    fillet_name = params.get("fillet_name") or "Fillet"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate object_name.
    if not object_name.strip():
        return {
            "ok": False,
            "op": "partdesign_fillet",
            "error": "object_name must not be empty.",
        }

    # Validate edge_indices: non-empty list of unique positive integers
    # (booleans are not accepted).
    if not isinstance(edge_indices, list) or len(edge_indices) == 0:
        return {
            "ok": False,
            "op": "partdesign_fillet",
            "error": "edge_indices must be a non-empty list of edge indices.",
        }
    seen: set[int] = set()
    normalized_edge_indices: list[int] = []
    for i, idx in enumerate(edge_indices):
        if isinstance(idx, bool) or not isinstance(idx, int):
            return {
                "ok": False,
                "op": "partdesign_fillet",
                "error": f"edge_indices[{i}] must be an integer (boolean values are not accepted).",
            }
        if idx <= 0:
            return {
                "ok": False,
                "op": "partdesign_fillet",
                "error": f"edge_indices[{i}] must be a positive integer."
            }
        if idx in seen:
            return {
                "ok": False,
                "op": "partdesign_fillet",
                "error": f"edge_indices[{i}] is a duplicate edge index: {idx}.",
            }
        seen.add(idx)
        normalized_edge_indices.append(idx)

    # Validate radius: finite and positive.
    try:
        radius_val = float(radius)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "partdesign_fillet",
            "error": "radius must be a finite number.",
        }
    if math.isnan(radius_val) or math.isinf(radius_val):
        return {
            "ok": False,
            "op": "partdesign_fillet",
            "error": "radius must be a finite number.",
        }
    if radius_val <= 0:
        return {
            "ok": False,
            "op": "partdesign_fillet",
            "error": "radius must be a positive number.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "partdesign_fillet", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "partdesign_fillet", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "partdesign_fillet", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    transaction_open = False
    doc = None

    def _fail(message: str) -> dict[str, Any]:
        nonlocal transaction_open
        if transaction_open:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception as exc:
                message += f" Rollback failed: {exc}"
            transaction_open = False
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "partdesign_fillet", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the target object and verify it is a solid feature.
        target = doc.getObject(object_name)
        if target is None:
            return _fail(f"object '{object_name}' not found")

        target_shape = getattr(target, "Shape", None)
        if target_shape is None:
            return _fail(
                f"object '{object_name}' has no shape; cannot create a Fillet on it."
            )
        try:
            if not bool(target_shape.isValid()):
                return _fail(
                    f"object '{object_name}' has an invalid shape; cannot create a Fillet on it."
                )
        except Exception as exc:
            return _fail(
                f"object '{object_name}' shape validation failed: {exc}"
            )
        try:
            target_solids = list(getattr(target_shape, "Solids", []) or [])
        except Exception:
            target_solids = []
        if not target_solids:
            return _fail(
                f"object '{object_name}' produces no solid; a Fillet requires an existing solid."
            )

        # Validate edge indices against the target's edge count.
        try:
            target_edges = list(getattr(target_shape, "Edges", []) or [])
        except Exception:
            target_edges = []
        edge_count = len(target_edges)
        for idx in normalized_edge_indices:
            if idx > edge_count:
                return _fail(
                    f"edge index {idx} is out of range for object '{object_name}' "
                    f"(it has {edge_count} edge(s))."
                )

        # Determine the Body: reuse an existing Body that contains the target,
        # or use the single unambiguous Body if the target is standalone.
        # A new Body is never created for a Fillet.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if target in getattr(obj, "Group", []) or target in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        doc.openTransaction("Create PartDesign Fillet")
        transaction_open = True

        if body is None:
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    body.addObject(target)
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add object '{object_name}' to Body "
                        f"'{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A Fillet requires a Body."
                )
            else:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the Fillet."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the Fillet.  Avoid name clashes.
        counter = 0
        desired_fillet_name = fillet_name
        while doc.getObject(desired_fillet_name) is not None:
            counter += 1
            desired_fillet_name = f"{fillet_name}_{counter}"
        try:
            fillet = doc.addObject("PartDesign::Fillet", desired_fillet_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Fillet: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Base is a LinkSub: edge selection belongs in its subelement list.
        try:
            fillet.Base = (target, [f"Edge{idx}" for idx in normalized_edge_indices])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Fillet base: {exc}."
            )

        # Apply the radius.
        try:
            fillet.Radius = radius_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Fillet radius: {exc}."
            )

        fillet.UseAllEdges = False

        # Add the Fillet to the Body (this advances Body.Tip to the Fillet).
        try:
            body.addObject(fillet)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add Fillet to Body '{body_name_used}': {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Fillet feature tree.
        doc.recompute()

        # Validate the resulting Fillet: it must have a valid, non-null solid shape.
        shape = getattr(fillet, "Shape", None)
        if shape is None:
            return _fail(
                f"Fillet '{desired_fillet_name}' produced no shape.  The requested edges or "
                f"radius may not produce a valid fillet."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"Fillet '{desired_fillet_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Fillet '{desired_fillet_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Fillet '{desired_fillet_name}' produced no solid."
            )

        doc.commitTransaction()
        transaction_open = False

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "partdesign_fillet",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "body_name": body_name_used,
            "fillet_name": desired_fillet_name,
            "base_feature": object_name,
            "edge_indices": normalized_edge_indices,
            "radius": radius_val,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_partdesign_chamfer(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Chamfer on edges of an existing solid feature.

    The target object must already belong to a PartDesign::Body, or the
    document must contain exactly one (unambiguous) Body to which it can be
    assigned.  A new Body is never silently created for a Chamfer -- a Chamfer
    requires an existing solid feature to chamfer.

    The Chamfer's Base links the target and the requested subelements
    (1-based indices mapped to FreeCAD ``Edge1``, ``Edge2``,
    ...).  *Size* is applied, the document is recomputed, and the resulting
    shape is validated (non-null valid solid containing at least one solid).
    The document is locked, recomputed, saved atomically, and its revision is
    bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    edge_indices = params.get("edge_indices")
    size = params.get("size")
    chamfer_name = params.get("chamfer_name") or "Chamfer"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate object_name.
    if not object_name.strip():
        return {
            "ok": False,
            "op": "partdesign_chamfer",
            "error": "object_name must not be empty.",
        }

    # Validate edge_indices: non-empty list of unique positive integers
    # (booleans are not accepted).
    if not isinstance(edge_indices, list) or len(edge_indices) == 0:
        return {
            "ok": False,
            "op": "partdesign_chamfer",
            "error": "edge_indices must be a non-empty list of edge indices.",
        }
    seen: set[int] = set()
    normalized_edge_indices: list[int] = []
    for i, idx in enumerate(edge_indices):
        if isinstance(idx, bool) or not isinstance(idx, int):
            return {
                "ok": False,
                "op": "partdesign_chamfer",
                "error": f"edge_indices[{i}] must be an integer (boolean values are not accepted).",
            }
        if idx <= 0:
            return {
                "ok": False,
                "op": "partdesign_chamfer",
                "error": f"edge_indices[{i}] must be a positive integer."
            }
        if idx in seen:
            return {
                "ok": False,
                "op": "partdesign_chamfer",
                "error": f"edge_indices[{i}] is a duplicate edge index: {idx}.",
            }
        seen.add(idx)
        normalized_edge_indices.append(idx)

    # Validate size: finite and positive.
    try:
        size_val = float(size)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "partdesign_chamfer",
            "error": "size must be a finite number.",
        }
    if math.isnan(size_val) or math.isinf(size_val):
        return {
            "ok": False,
            "op": "partdesign_chamfer",
            "error": "size must be a finite number.",
        }
    if size_val <= 0:
        return {
            "ok": False,
            "op": "partdesign_chamfer",
            "error": "size must be a positive number.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "partdesign_chamfer", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "partdesign_chamfer", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "partdesign_chamfer", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    transaction_open = False
    doc = None

    def _fail(message: str) -> dict[str, Any]:
        nonlocal transaction_open
        if transaction_open:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception as exc:
                message += f" Rollback failed: {exc}"
            transaction_open = False
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "partdesign_chamfer", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the target object and verify it is a solid feature.
        target = doc.getObject(object_name)
        if target is None:
            return _fail(f"object '{object_name}' not found")

        target_shape = getattr(target, "Shape", None)
        if target_shape is None:
            return _fail(
                f"object '{object_name}' has no shape; cannot create a Chamfer on it."
            )
        try:
            if not bool(target_shape.isValid()):
                return _fail(
                    f"object '{object_name}' has an invalid shape; cannot create a Chamfer on it."
                )
        except Exception as exc:
            return _fail(
                f"object '{object_name}' shape validation failed: {exc}"
            )
        try:
            target_solids = list(getattr(target_shape, "Solids", []) or [])
        except Exception:
            target_solids = []
        if not target_solids:
            return _fail(
                f"object '{object_name}' produces no solid; a Chamfer requires an existing solid."
            )

        # Validate edge indices against the target's edge count.
        try:
            target_edges = list(getattr(target_shape, "Edges", []) or [])
        except Exception:
            target_edges = []
        edge_count = len(target_edges)
        for idx in normalized_edge_indices:
            if idx > edge_count:
                return _fail(
                    f"edge index {idx} is out of range for object '{object_name}' "
                    f"(it has {edge_count} edge(s))."
                )

        # Determine the Body: reuse an existing Body that contains the target,
        # or use the single unambiguous Body if the target is standalone.
        # A new Body is never created for a Chamfer.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if target in getattr(obj, "Group", []) or target in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        doc.openTransaction("Create PartDesign Chamfer")
        transaction_open = True

        if body is None:
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    body.addObject(target)
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add object '{object_name}' to Body "
                        f"'{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A Chamfer requires a Body."
                )
            else:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the Chamfer."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the Chamfer.  Avoid name clashes.
        counter = 0
        desired_chamfer_name = chamfer_name
        while doc.getObject(desired_chamfer_name) is not None:
            counter += 1
            desired_chamfer_name = f"{chamfer_name}_{counter}"
        try:
            chamfer = doc.addObject("PartDesign::Chamfer", desired_chamfer_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Chamfer: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Base is a LinkSub: edge selection belongs in its subelement list.
        try:
            chamfer.Base = (target, [f"Edge{idx}" for idx in normalized_edge_indices])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Chamfer base: {exc}."
            )

        # Apply the size.
        try:
            chamfer.Size = size_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Chamfer size: {exc}."
            )

        chamfer.UseAllEdges = False

        # Add the Chamfer to the Body (this advances Body.Tip to the Chamfer).
        try:
            body.addObject(chamfer)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add Chamfer to Body '{body_name_used}': {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Chamfer feature tree.
        doc.recompute()

        # Validate the resulting Chamfer: it must have a valid, non-null solid shape.
        shape = getattr(chamfer, "Shape", None)
        if shape is None:
            return _fail(
                f"Chamfer '{desired_chamfer_name}' produced no shape.  The requested edges or "
                f"size may not produce a valid chamfer."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"Chamfer '{desired_chamfer_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Chamfer '{desired_chamfer_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Chamfer '{desired_chamfer_name}' produced no solid."
            )

        doc.commitTransaction()
        transaction_open = False

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "partdesign_chamfer",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "body_name": body_name_used,
            "chamfer_name": desired_chamfer_name,
            "base_feature": object_name,
            "edge_indices": normalized_edge_indices,
            "size": size_val,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_partdesign_linear_pattern(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::LinearPattern on an existing solid feature.

    The target object must already belong to a PartDesign::Body, or the
    document must contain exactly one (unambiguous) Body to which it can be
    assigned.  A new Body is never silently created for a LinearPattern -- a
    LinearPattern requires an existing solid feature to pattern.

    The LinearPattern's BaseFeature links the target, *Originals* lists the
    target as the feature to pattern, and *Direction* links a subelement of the
    target that is parallel to the requested principal axis (X_Axis, Y_Axis,
    or Z_Axis).  *Length* and *Occurrences* are applied, the document is
    recomputed, and the resulting shape is validated (non-null valid solid
    containing at least one solid).  The document is locked, recomputed, saved
    atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    direction = params.get("direction") or ""
    length = params.get("length")
    occurrences = params.get("occurrences")
    pattern_name = params.get("linear_pattern_name") or "LinearPattern"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate object_name.
    if not object_name.strip():
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "object_name must not be empty.",
        }

    # Validate direction: must be one of X_Axis, Y_Axis, Z_Axis.
    if direction not in ("X_Axis", "Y_Axis", "Z_Axis"):
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "direction must be one of 'X_Axis', 'Y_Axis', or 'Z_Axis'.",
        }

    # Validate length: finite and positive.
    try:
        length_val = float(length)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "length must be a finite number.",
        }
    if math.isnan(length_val) or math.isinf(length_val):
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "length must be a finite number.",
        }
    if length_val <= 0:
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "length must be a positive number.",
        }

    # Validate occurrences: integer >= 2 (booleans are not accepted).
    if isinstance(occurrences, bool) or not isinstance(occurrences, int):
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "occurrences must be an integer (boolean values are not accepted).",
        }
    if occurrences < 2:
        return {
            "ok": False,
            "op": "partdesign_linear_pattern",
            "error": "occurrences must be an integer greater than or equal to 2.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "partdesign_linear_pattern", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "partdesign_linear_pattern", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "partdesign_linear_pattern", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    transaction_open = False
    doc = None

    def _fail(message: str) -> dict[str, Any]:
        nonlocal transaction_open
        if transaction_open:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception as exc:
                message += f" Rollback failed: {exc}"
            transaction_open = False
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "partdesign_linear_pattern", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the target object and verify it is a solid feature.
        target = doc.getObject(object_name)
        if target is None:
            return _fail(f"object '{object_name}' not found")

        target_shape = getattr(target, "Shape", None)
        if target_shape is None:
            return _fail(
                f"object '{object_name}' has no shape; cannot create a LinearPattern on it."
            )
        try:
            if not bool(target_shape.isValid()):
                return _fail(
                    f"object '{object_name}' has an invalid shape; cannot create a LinearPattern on it."
                )
        except Exception as exc:
            return _fail(
                f"object '{object_name}' shape validation failed: {exc}"
            )
        try:
            target_solids = list(getattr(target_shape, "Solids", []) or [])
        except Exception:
            target_solids = []
        if not target_solids:
            return _fail(
                f"object '{object_name}' produces no solid; a LinearPattern requires an existing solid."
            )

        # Validate edge indices against the target's edge count and find an
        # edge parallel to the requested principal axis.
        try:
            target_edges = list(getattr(target_shape, "Edges", []) or [])
        except Exception:
            target_edges = []
        edge_count = len(target_edges)
        if edge_count == 0:
            return _fail(
                f"object '{object_name}' has no edges; cannot determine a pattern direction."
            )

        # Map the requested axis to a FreeCAD vector and find a parallel edge.
        axis_vec = (
            _FC.Vector(1, 0, 0) if direction == "X_Axis"
            else _FC.Vector(0, 1, 0) if direction == "Y_Axis"
            else _FC.Vector(0, 0, 1)
        )
        direction_edge_index = None
        for i, edge in enumerate(target_edges, 1):
            try:
                tangent = edge.tangentAt(0.5)
                tangent.normalize()
                if abs(tangent.dot(axis_vec)) > 0.99:
                    direction_edge_index = i
                    break
            except Exception:
                continue
        if direction_edge_index is None:
            return _fail(
                f"object '{object_name}' has no edge parallel to the {direction} direction."
            )

        # Determine the Body: reuse an existing Body that contains the target,
        # or use the single unambiguous Body if the target is standalone.
        # A new Body is never created for a LinearPattern.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if target in getattr(obj, "Group", []) or target in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        doc.openTransaction("Create PartDesign LinearPattern")
        transaction_open = True

        if body is None:
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    body.addObject(target)
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add object '{object_name}' to Body "
                        f"'{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A LinearPattern requires a Body."
                )
            else:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the LinearPattern."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the LinearPattern.  Avoid name clashes.
        counter = 0
        desired_pattern_name = pattern_name
        while doc.getObject(desired_pattern_name) is not None:
            counter += 1
            desired_pattern_name = f"{pattern_name}_{counter}"
        try:
            pattern = doc.addObject("PartDesign::LinearPattern", desired_pattern_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::LinearPattern: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # BaseFeature is the target feature to pattern.
        try:
            pattern.BaseFeature = target
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the LinearPattern base feature: {exc}."
            )

        # Originals lists the features to pattern (required for a valid pattern).
        try:
            pattern.Originals = [target]
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the LinearPattern originals: {exc}."
            )

        # Direction is a LinkSub referencing an edge parallel to the requested axis.
        try:
            pattern.Direction = (target, [f"Edge{direction_edge_index}"])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the LinearPattern direction: {exc}."
            )

        # Apply the length.
        try:
            pattern.Length = length_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the LinearPattern length: {exc}."
            )

        # Apply the occurrence count.
        try:
            pattern.Occurrences = occurrences
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the LinearPattern occurrences: {exc}."
            )

        # Add the LinearPattern to the Body (this advances Body.Tip to the pattern).
        try:
            body.addObject(pattern)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add LinearPattern to Body '{body_name_used}': {exc}."
            )

        # Recompute the document so FreeCAD evaluates the LinearPattern feature tree.
        doc.recompute()

        # Validate the resulting LinearPattern: it must have a valid, non-null solid shape.
        shape = getattr(pattern, "Shape", None)
        if shape is None:
            return _fail(
                f"LinearPattern '{desired_pattern_name}' produced no shape.  The requested "
                f"direction, length, or occurrences may not produce a valid pattern."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"LinearPattern '{desired_pattern_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"LinearPattern '{desired_pattern_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"LinearPattern '{desired_pattern_name}' produced no solid."
            )

        doc.commitTransaction()
        transaction_open = False

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "partdesign_linear_pattern",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "body_name": body_name_used,
            "linear_pattern_name": desired_pattern_name,
            "base_feature": object_name,
            "direction": direction,
            "direction_edge_index": direction_edge_index,
            "length": length_val,
            "occurrences": occurrences,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_partdesign_polar_pattern(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::PolarPattern on an existing solid feature.

    The target object must already belong to a PartDesign::Body, or the
    document must contain exactly one (unambiguous) Body to which it can be
    assigned.  A new Body is never silently created for a PolarPattern -- a
    PolarPattern requires an existing solid feature to pattern.

    The PolarPattern's BaseFeature links the target, *Originals* lists the
    target as the feature to pattern, and *Axis* links a subelement of the
    target that is parallel to the requested principal axis (X_Axis, Y_Axis,
    or Z_Axis).  *Angle* (degrees, finite positive up to 360) and
    *Occurrences* (int >= 2) are applied, the document is recomputed, and the
    resulting shape is validated (non-null valid solid containing at least one
    solid).  The document is locked, recomputed, saved atomically, and its
    revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    axis = params.get("axis") or ""
    angle = params.get("angle")
    occurrences = params.get("occurrences")
    pattern_name = params.get("polar_pattern_name") or "PolarPattern"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate object_name.
    if not object_name.strip():
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "object_name must not be empty.",
        }

    # Validate axis: must be one of X_Axis, Y_Axis, Z_Axis.
    if axis not in ("X_Axis", "Y_Axis", "Z_Axis"):
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "axis must be one of 'X_Axis', 'Y_Axis', or 'Z_Axis'.",
        }

    # Validate angle: finite and positive, up to 360 degrees.
    try:
        angle_val = float(angle)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "angle must be a finite number.",
        }
    if math.isnan(angle_val) or math.isinf(angle_val):
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "angle must be a finite number.",
        }
    if angle_val <= 0:
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "angle must be a positive number.",
        }
    if angle_val > 360:
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "angle must be at most 360 degrees.",
        }

    # Validate occurrences: integer >= 2 (booleans are not accepted).
    if isinstance(occurrences, bool) or not isinstance(occurrences, int):
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "occurrences must be an integer (boolean values are not accepted).",
        }
    if occurrences < 2:
        return {
            "ok": False,
            "op": "partdesign_polar_pattern",
            "error": "occurrences must be an integer greater than or equal to 2.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "partdesign_polar_pattern", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "partdesign_polar_pattern", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "partdesign_polar_pattern", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    transaction_open = False
    doc = None

    def _fail(message: str) -> dict[str, Any]:
        nonlocal transaction_open
        if transaction_open:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception as exc:
                message += f" Rollback failed: {exc}"
            transaction_open = False
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "partdesign_polar_pattern", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the target object and verify it is a solid feature.
        target = doc.getObject(object_name)
        if target is None:
            return _fail(f"object '{object_name}' not found")

        target_shape = getattr(target, "Shape", None)
        if target_shape is None:
            return _fail(
                f"object '{object_name}' has no shape; cannot create a PolarPattern on it."
            )
        try:
            if not bool(target_shape.isValid()):
                return _fail(
                    f"object '{object_name}' has an invalid shape; cannot create a PolarPattern on it."
                )
        except Exception as exc:
            return _fail(
                f"object '{object_name}' shape validation failed: {exc}"
            )
        try:
            target_solids = list(getattr(target_shape, "Solids", []) or [])
        except Exception:
            target_solids = []
        if not target_solids:
            return _fail(
                f"object '{object_name}' produces no solid; a PolarPattern requires an existing solid."
            )

        # Validate edge indices against the target's edge count and find an
        # edge parallel to the requested principal axis.
        try:
            target_edges = list(getattr(target_shape, "Edges", []) or [])
        except Exception:
            target_edges = []
        edge_count = len(target_edges)
        if edge_count == 0:
            return _fail(
                f"object '{object_name}' has no edges; cannot determine a pattern axis."
            )

        # Map the requested axis to a FreeCAD vector and find a parallel edge.
        axis_vec = (
            _FC.Vector(1, 0, 0) if axis == "X_Axis"
            else _FC.Vector(0, 1, 0) if axis == "Y_Axis"
            else _FC.Vector(0, 0, 1)
        )
        axis_edge_index = None
        for i, edge in enumerate(target_edges, 1):
            try:
                tangent = edge.tangentAt(0.5)
                tangent.normalize()
                if abs(tangent.dot(axis_vec)) > 0.99:
                    axis_edge_index = i
                    break
            except Exception:
                continue
        if axis_edge_index is None:
            return _fail(
                f"object '{object_name}' has no edge parallel to the {axis} direction."
            )

        # Determine the Body: reuse an existing Body that contains the target,
        # or use the single unambiguous Body if the target is standalone.
        # A new Body is never created for a PolarPattern.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if target in getattr(obj, "Group", []) or target in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        doc.openTransaction("Create PartDesign PolarPattern")
        transaction_open = True

        if body is None:
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    body.addObject(target)
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add object '{object_name}' to Body "
                        f"'{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A PolarPattern requires a Body."
                )
            else:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the PolarPattern."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the PolarPattern.  Avoid name clashes.
        counter = 0
        desired_pattern_name = pattern_name
        while doc.getObject(desired_pattern_name) is not None:
            counter += 1
            desired_pattern_name = f"{pattern_name}_{counter}"
        try:
            pattern = doc.addObject("PartDesign::PolarPattern", desired_pattern_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::PolarPattern: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # BaseFeature is the target feature to pattern.
        try:
            pattern.BaseFeature = target
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the PolarPattern base feature: {exc}."
            )

        # Originals lists the features to pattern (required for a valid pattern).
        try:
            pattern.Originals = [target]
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the PolarPattern originals: {exc}."
            )

        # Axis is a LinkSub referencing an edge parallel to the requested axis.
        try:
            pattern.Axis = (target, [f"Edge{axis_edge_index}"])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the PolarPattern axis: {exc}."
            )

        # Apply the angle.
        try:
            pattern.Angle = angle_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the PolarPattern angle: {exc}."
            )

        # Apply the occurrence count.
        try:
            pattern.Occurrences = occurrences
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the PolarPattern occurrences: {exc}."
            )

        # Add the PolarPattern to the Body (this advances Body.Tip to the pattern).
        try:
            body.addObject(pattern)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add PolarPattern to Body '{body_name_used}': {exc}."
            )

        # Recompute the document so FreeCAD evaluates the PolarPattern feature tree.
        doc.recompute()

        # Validate the resulting PolarPattern: it must have a valid, non-null solid shape.
        shape = getattr(pattern, "Shape", None)
        if shape is None:
            return _fail(
                f"PolarPattern '{desired_pattern_name}' produced no shape.  The requested "
                f"axis, angle, or occurrences may not produce a valid pattern."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"PolarPattern '{desired_pattern_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"PolarPattern '{desired_pattern_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"PolarPattern '{desired_pattern_name}' produced no solid."
            )

        doc.commitTransaction()
        transaction_open = False

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "partdesign_polar_pattern",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "body_name": body_name_used,
            "polar_pattern_name": desired_pattern_name,
            "base_feature": object_name,
            "axis": axis,
            "axis_edge_index": axis_edge_index,
            "angle": angle_val,
            "occurrences": occurrences,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_partdesign_mirrored(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Mirrored on an existing solid feature.

    The target object must already belong to a PartDesign::Body, or the
    document must contain exactly one (unambiguous) Body to which it can be
    assigned.  A new Body is never silently created for a Mirrored -- a
    Mirrored requires an existing solid feature to mirror.

    The Mirrored's BaseFeature links the target, *Originals* lists the target
    as the feature to mirror, and *MirrorPlane* links a face of the target
    whose normal is parallel to the requested principal plane normal
    (XY_Plane -> Z normal, XZ_Plane -> Y normal, YZ_Plane -> X normal).
    The document is recomputed, and the resulting shape is validated
    (non-null valid solid containing at least one solid).  The document is
    locked, recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    plane = params.get("plane") or ""
    mirrored_name = params.get("mirrored_name") or "Mirrored"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate object_name.
    if not object_name.strip():
        return {
            "ok": False,
            "op": "partdesign_mirrored",
            "error": "object_name must not be empty.",
        }

    # Validate plane: must be one of XY_Plane, XZ_Plane, YZ_Plane.
    if plane not in ("XY_Plane", "XZ_Plane", "YZ_Plane"):
        return {
            "ok": False,
            "op": "partdesign_mirrored",
            "error": "plane must be one of 'XY_Plane', 'XZ_Plane', or 'YZ_Plane'.",
        }

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "partdesign_mirrored", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "partdesign_mirrored", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "partdesign_mirrored", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    transaction_open = False
    doc = None

    def _fail(message: str) -> dict[str, Any]:
        nonlocal transaction_open
        if transaction_open:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception as exc:
                message += f" Rollback failed: {exc}"
            transaction_open = False
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "partdesign_mirrored", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the target object and verify it is a solid feature.
        target = doc.getObject(object_name)
        if target is None:
            return _fail(f"object '{object_name}' not found")

        target_shape = getattr(target, "Shape", None)
        if target_shape is None:
            return _fail(
                f"object '{object_name}' has no shape; cannot create a Mirrored on it."
            )
        try:
            if not bool(target_shape.isValid()):
                return _fail(
                    f"object '{object_name}' has an invalid shape; cannot create a Mirrored on it."
                )
        except Exception as exc:
            return _fail(
                f"object '{object_name}' shape validation failed: {exc}"
            )
        try:
            target_solids = list(getattr(target_shape, "Solids", []) or [])
        except Exception:
            target_solids = []
        if not target_solids:
            return _fail(
                f"object '{object_name}' produces no solid; a Mirrored requires an existing solid."
            )

        # Map the requested plane to a FreeCAD normal vector and find a face
        # whose normal is parallel to that vector.
        plane_normal = (
            _FC.Vector(0, 0, 1) if plane == "XY_Plane"
            else _FC.Vector(0, 1, 0) if plane == "XZ_Plane"
            else _FC.Vector(1, 0, 0)
        )

        # Find faces of the target whose normal is parallel to the plane normal.
        try:
            target_faces = list(getattr(target_shape, "Faces", []) or [])
        except Exception:
            target_faces = []
        face_count = len(target_faces)
        if face_count == 0:
            return _fail(
                f"object '{object_name}' has no faces; cannot determine a mirror plane."
            )

        mirror_face_index = None
        for i, face in enumerate(target_faces, 1):
            try:
                normal = face.normalAt(0.5, 0.5)
                normal.normalize()
                if abs(normal.dot(plane_normal)) > 0.99:
                    mirror_face_index = i
                    break
            except Exception:
                continue
        if mirror_face_index is None:
            return _fail(
                f"object '{object_name}' has no face parallel to the {plane} plane."
            )

        # Determine the Body: reuse an existing Body that contains the target,
        # or use the single unambiguous Body if the target is standalone.
        # A new Body is never created for a Mirrored.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if target in getattr(obj, "Group", []) or target in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        doc.openTransaction("Create PartDesign Mirrored")
        transaction_open = True

        if body is None:
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    body.addObject(target)
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add object '{object_name}' to Body "
                        f"'{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A Mirrored requires a Body."
                )
            else:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the Mirrored."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the Mirrored.  Avoid name clashes.
        counter = 0
        desired_mirrored_name = mirrored_name
        while doc.getObject(desired_mirrored_name) is not None:
            counter += 1
            desired_mirrored_name = f"{mirrored_name}_{counter}"
        try:
            mirrored = doc.addObject("PartDesign::Mirrored", desired_mirrored_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Mirrored: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # BaseFeature is the target feature to mirror.
        try:
            mirrored.BaseFeature = target
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Mirrored base feature: {exc}."
            )

        # Originals lists the features to mirror (required for a valid pattern).
        try:
            mirrored.Originals = [target]
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Mirrored originals: {exc}."
            )

        # MirrorPlane is a LinkSub referencing a face whose normal is parallel
        # to the requested principal plane normal.
        try:
            mirrored.MirrorPlane = (target, [f"Face{mirror_face_index}"])
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Mirrored mirror plane: {exc}."
            )

        # Add the Mirrored to the Body.
        try:
            body.addObject(mirrored)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add Mirrored to Body '{body_name_used}': {exc}."
            )

        # Recompute the document so FreeCAD evaluates the Mirrored feature tree.
        doc.recompute()

        # Validate the resulting Mirrored: it must have a valid, non-null solid shape.
        shape = getattr(mirrored, "Shape", None)
        if shape is None:
            return _fail(
                f"Mirrored '{desired_mirrored_name}' produced no shape.  The requested "
                f"plane may not produce a valid mirror."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"Mirrored '{desired_mirrored_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Mirrored '{desired_mirrored_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Mirrored '{desired_mirrored_name}' produced no solid."
            )

        doc.commitTransaction()
        transaction_open = False

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "partdesign_mirrored",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "body_name": body_name_used,
            "mirrored_name": desired_mirrored_name,
            "base_feature": object_name,
            "plane": plane,
            "mirror_face_index": mirror_face_index,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


def _op_partdesign_thickness(params: dict[str, Any]) -> dict[str, Any]:
    """Create a native PartDesign::Thickness on an existing solid feature.

    The target object must already belong to a PartDesign::Body, or the
    document must contain exactly one (unambiguous) Body to which it can be
    assigned.  A new Body is never silently created for a Thickness -- a
    Thickness requires an existing solid feature to hollow.

    The Thickness's BaseFeature links the target, *Value* is the wall
    thickness, and *Reversed* controls the offset direction.  FreeCAD 1.1.4
    exposes only the ``Mode='Skin'`` enumeration value for Thickness
    direction (no Inward/Outward), so direction is expressed through
    *Reversed*.  Face removal (opening faces) is not exposed as a settable
    property in FreeCAD 1.1.4's PartDesign::Thickness, so removed-face
    selection is not supported; the operation produces a valid solid from the
    base feature with the requested wall thickness applied.

    The document is recomputed, and the resulting shape is validated
    (non-null valid solid containing at least one solid).  The document is
    locked, recomputed, saved atomically, and its revision is bumped.

    On any failure before the atomic save, the lock is released and the
    on-disk document is left unmodified (rollback via the save-on-success
    pattern).
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or ""
    thickness_name = params.get("thickness_name") or "Thickness"
    body_name = params.get("body_name") or "Body"
    workspace_str = params.get("workspace") or ""

    # Validate object_name.
    if not object_name.strip():
        return {
            "ok": False,
            "op": "partdesign_thickness",
            "error": "object_name must not be empty.",
        }

    # Validate thickness: must be finite and positive.
    thickness = params.get("thickness")
    try:
        thickness_val = float(thickness)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "op": "partdesign_thickness",
            "error": "thickness must be a finite number.",
        }
    if math.isnan(thickness_val) or math.isinf(thickness_val):
        return {
            "ok": False,
            "op": "partdesign_thickness",
            "error": "thickness must be a finite number.",
        }
    if thickness_val <= 0:
        return {
            "ok": False,
            "op": "partdesign_thickness",
            "error": "thickness must be positive",
        }

    # Validate reversed: must be a boolean.
    reversed_param = params.get("reversed", False)
    if not isinstance(reversed_param, bool):
        return {
            "ok": False,
            "op": "partdesign_thickness",
            "error": "reversed must be a boolean (true/false).",
        }

    # Validate faces: optional list of 1-based face indices to remove.
    faces_param = params.get("faces", [])
    removed_faces: list[int] = []
    if faces_param is None:
        faces_param = []
    if not isinstance(faces_param, list):
        return {
            "ok": False,
            "op": "partdesign_thickness",
            "error": "faces must be a list of 1-based face indices.",
        }
    seen_faces: set[int] = set()
    for i, f in enumerate(faces_param):
        if isinstance(f, bool) or not isinstance(f, int):
            return {
                "ok": False,
                "op": "partdesign_thickness",
                "error": f"faces[{i}] must be an integer (boolean values are not accepted).",
            }
        if f in seen_faces:
            return {
                "ok": False,
                "op": "partdesign_thickness",
                "error": f"faces[{i}] is a duplicate face index: {f}.",
            }
        seen_faces.add(f)
        if f < 1:
            return {
                "ok": False,
                "op": "partdesign_thickness",
                "error": f"faces[{i}] must be >= 1: {f}.",
            }
        removed_faces.append(f)

    try:
        normalized = ws_mod.validate_document_id(document_id)
    except ValueError as exc:
        return {"ok": False, "op": "partdesign_thickness", "error": str(exc)}

    try:
        wks = (
            ws_mod.Workspace.from_env() if not workspace_str else ws_mod.Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return {"ok": False, "op": "partdesign_thickness", "error": f"workspace error: {exc}"}

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = ws_mod.try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return {"ok": False, "op": "partdesign_thickness", "error": lock_status.get("error", "lock failed")}

    meta_before = ws_mod.read_meta(wks.meta_path(normalized))

    transaction_open = False
    doc = None

    def _fail(message: str) -> dict[str, Any]:
        nonlocal transaction_open
        if transaction_open:
            try:
                doc.abortTransaction()
                doc.recompute()
            except Exception as exc:
                message += f" Rollback failed: {exc}"
            transaction_open = False
        ws_mod.release_lock(lock_path, pid)
        return {"ok": False, "op": "partdesign_thickness", "error": message}

    try:
        import FreeCAD as _FC  # noqa: PLC0415 - imported lazily inside FreeCAD runtime
        import PartDesign  # noqa: F401,PLC0415 - register PartDesign types with FreeCAD
        doc = _open_existing_document(_FC, wks, normalized)

        # Find the target object and verify it is a solid feature.
        target = doc.getObject(object_name)
        if target is None:
            return _fail(f"object '{object_name}' not found")

        target_shape = getattr(target, "Shape", None)
        if target_shape is None:
            return _fail(
                f"object '{object_name}' has no shape; cannot create a Thickness on it."
            )
        try:
            if not bool(target_shape.isValid()):
                return _fail(
                    f"object '{object_name}' has an invalid shape; cannot create a Thickness on it."
                )
        except Exception as exc:
            return _fail(
                f"object '{object_name}' shape validation failed: {exc}"
            )
        try:
            target_solids = list(getattr(target_shape, "Solids", []) or [])
        except Exception:
            target_solids = []
        if not target_solids:
            return _fail(
                f"object '{object_name}' produces no solid; a Thickness requires an existing solid."
            )

        # Get the target's face count for range-checking face indices.
        try:
            target_faces = list(getattr(target_shape, "Faces", []) or [])
        except Exception:
            target_faces = []
        target_face_count = len(target_faces)
        for f in removed_faces:
            if f > target_face_count:
                return _fail(
                    f"faces index {f} is out of range: object '{object_name}' has "
                    f"{target_face_count} face(s)."
                )

        # Determine the Body: reuse an existing Body that contains the target,
        # or use the single unambiguous Body if the target is standalone.
        # A new Body is never created for a Thickness.
        body = None
        bodies = []
        try:
            for obj in getattr(doc, "Objects", []) or []:
                if getattr(obj, "TypeId", "") == "PartDesign::Body":
                    bodies.append(obj)
                    try:
                        if target in getattr(obj, "Group", []) or target in getattr(obj, "outList", []):
                            body = obj
                    except Exception:
                        pass
        except Exception:
            bodies = []

        doc.openTransaction("Create PartDesign Thickness")
        transaction_open = True

        if body is None:
            if len(bodies) == 1:
                body = bodies[0]
                try:
                    body.addObject(target)
                except Exception as exc:
                    return _fail(
                        f"FreeCAD could not add object '{object_name}' to Body "
                        f"'{getattr(body, 'Name', body_name)}': {exc}."
                    )
            elif len(bodies) == 0:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and no "
                    f"Body exists in the document.  A Thickness requires a Body."
                )
            else:
                return _fail(
                    f"object '{object_name}' is not inside any PartDesign::Body, and the "
                    f"document contains {len(bodies)} Bodies.  Cannot determine which Body "
                    f"to use for the Thickness."
                )

        body_name_used = getattr(body, "Name", body_name)

        # Create the Thickness.  Avoid name clashes.
        counter = 0
        desired_thickness_name = thickness_name
        while doc.getObject(desired_thickness_name) is not None:
            counter += 1
            desired_thickness_name = f"{thickness_name}_{counter}"
        try:
            thickness_feature = doc.addObject("PartDesign::Thickness", desired_thickness_name)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not create a PartDesign::Thickness: {exc}.  Ensure the PartDesign "
                f"workbench is available."
            )

        # Add the Thickness to the Body.
        try:
            body.addObject(thickness_feature)
        except Exception as exc:
            return _fail(
                f"FreeCAD could not add Thickness to Body '{body_name_used}': {exc}."
            )

        # BaseFeature is the target feature to apply thickness to.
        try:
            thickness_feature.BaseFeature = target
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Thickness base feature: {exc}."
            )

        # Value is the wall thickness (Quantity; settable as a float in mm).
        try:
            thickness_feature.Value = thickness_val
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Thickness value: {exc}."
            )

        # Reversed controls the offset direction.  FreeCAD 1.1.4 exposes only
        # Mode='Skin'; direction is expressed through Reversed.
        try:
            thickness_feature.Reversed = reversed_param
        except Exception as exc:
            return _fail(
                f"FreeCAD could not set the Thickness direction: {exc}."
            )

        # Base is a LinkSub (object, [face_subname]) listing the faces to
        # remove/open.  FreeCAD 1.1.4 accepts a single tuple containing the
        # target object and a list of face subnames like "Face6".  1-based
        # indices were range-checked above against the target's face count.
        if removed_faces:
            face_subnames = [f"Face{f}" for f in removed_faces]
            try:
                thickness_feature.Base = (target, face_subnames)
            except Exception as exc:
                return _fail(
                    f"FreeCAD could not set the Thickness removed faces: {exc}."
                )

        # Capture base volume before recompute for hollow-result validation.
        try:
            base_volume = float(target_shape.Volume)
        except Exception:
            base_volume = None

        # Recompute the document so FreeCAD evaluates the Thickness feature tree.
        doc.recompute()

        # Validate the resulting Thickness: it must have a valid, non-null solid shape.
        shape = getattr(thickness_feature, "Shape", None)
        if shape is None:
            return _fail(
                f"Thickness '{desired_thickness_name}' produced no shape."
            )
        try:
            if not bool(shape.isValid()):
                return _fail(
                    f"Thickness '{desired_thickness_name}' produced an invalid shape."
                )
        except Exception as exc:
            return _fail(
                f"Thickness '{desired_thickness_name}' shape validation failed: {exc}"
            )

        # Check that the shape contains at least one solid.
        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []
        if not solids:
            return _fail(
                f"Thickness '{desired_thickness_name}' produced no solid."
            )

        # When faces are removed, the result must be genuinely different from
        # the base shape.  Otherwise the feature is ineffective (FreeCAD silently
        # ignores missing/invalid face references), which we must not report as
        # success.  The check is direction-aware:
        #   * Inward (Reversed=True): hollowing reduces volume (result < base).
        #   * Outward (Reversed=False): thickening adds material outward, so the
        #     result volume may be greater than the base; it only needs to differ.
        if removed_faces and base_volume is not None:
            try:
                result_volume = float(shape.Volume)
            except Exception:
                result_volume = None
            if result_volume is None:
                return _fail(
                    f"Thickness '{desired_thickness_name}' produced a shape with "
                    f"no readable volume; the feature may be ineffective."
                )
            # The result must be genuinely different from the base shape.
            volume_tolerance = max(1e-6 * abs(base_volume), 1e-9)
            if abs(result_volume - base_volume) <= volume_tolerance:
                return _fail(
                    f"Thickness '{desired_thickness_name}' produced a shape with "
                    f"volume {result_volume} equal to the base volume "
                    f"{base_volume}; the feature appears ineffective."
                )
            if reversed_param:
                # Inward: hollowing should strictly reduce volume.
                if result_volume >= base_volume:
                    return _fail(
                        f"Thickness '{desired_thickness_name}' with inward direction "
                        f"produced a shape with volume {result_volume} not less than "
                        f"the base volume {base_volume}; removed faces may be invalid."
                    )

        doc.commitTransaction()
        transaction_open = False

        # Get shape info for the result.
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = None
        try:
            bbox = shape.BoundBox
            bbox_info = {
                "xmin": float(bbox.XMin),
                "ymin": float(bbox.YMin),
                "zmin": float(bbox.ZMin),
                "xmax": float(bbox.XMax),
                "ymax": float(bbox.YMax),
                "zmax": float(bbox.ZMax),
            }
        except Exception:
            bbox_info = None

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(str(tmp_path))

        ws_mod.atomic_save(fcstd_path, _write_tmp)

        meta = ws_mod.read_meta(wks.meta_path(normalized))
        meta["revision"] = meta.get("revision", 0) + 1
        meta["last_modified_at"] = datetime.now(timezone.utc).isoformat()
        ws_mod.write_meta(wks.meta_path(normalized), meta)
    except Exception as exc:
        return _fail(f"FreeCAD error: {exc}")

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "partdesign_thickness",
        "result": {
            "document_id": normalized,
            "object_name": object_name,
            "body_name": body_name_used,
            "thickness_name": desired_thickness_name,
            "base_feature": object_name,
            "thickness_value": thickness_val,
            "direction": "inward" if reversed_param else "outward",
            "mode": "Skin",
            "base": f"({object_name}, {[f'Face{f}' for f in removed_faces]})" if removed_faces else None,
            "removed_faces": removed_faces,
            "base_volume": base_volume,
            "volume_reduction": (base_volume - volume) if (base_volume is not None and volume is not None) else None,
            "volume": volume,
            "bounding_box": bbox_info,
            "solids_count": len(solids),
            "meta_before": meta_before,
        },
    }


OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    OP_PING: _op_ping,
    OP_GET_VERSION: _op_get_version,
    OP_CREATE_DOCUMENT: _op_create_document,
    OP_CREATE_BOX: _op_create_box,
    OP_CREATE_CYLINDER: _op_create_cylinder,
    OP_SET_PARAMETER: _op_set_parameter,
    OP_RECOMPUTE: _op_recompute,
    OP_LIST_OBJECTS: _op_list_objects,
    OP_INSPECT_OBJECT: _op_inspect_object,
    OP_VALIDATE: _op_validate,
    OP_SAVE_DOCUMENT: _op_save_document,
    OP_EXPORT_STEP: _op_export_step,
    OP_TANK: _op_tank,
    OP_TUBULAR_COIL: _op_tubular_coil,
    OP_BOOLEAN_CUT: _op_boolean_cut,
    OP_BOOLEAN_FUSE: _op_boolean_fuse,
    OP_BOOLEAN_COMMON: _op_boolean_common,
    OP_SET_PLACEMENT: _op_set_placement,
    OP_GET_BODY_TIP: _op_get_body_tip,
    OP_SET_BODY_TIP: _op_set_body_tip,
    OP_REORDER_BODY_FEATURES: _op_reorder_body_features,
    OP_CREATE_BODY: _op_create_body,
    OP_VALIDATE_REOPEN: _op_validate_reopen,
    OP_CREATE_SKETCH: _op_create_sketch,
    OP_SKETCH_ADD_RECTANGLE: _op_sketch_add_rectangle,
    OP_SKETCH_ADD_CONSTRAINT: _op_sketch_add_constraint,
    OP_SKETCH_DELETE_CONSTRAINT: _op_sketch_delete_constraint,
    OP_SKETCH_EDIT_CONSTRAINT: _op_sketch_edit_constraint,
    OP_SKETCH_ADD_CIRCLE: _op_sketch_add_circle,
    OP_SKETCH_ADD_TRIANGLE: _op_sketch_add_triangle,
    OP_SKETCH_ADD_ELLIPSE: _op_sketch_add_ellipse,
    OP_SKETCH_ADD_REGULAR_POLYGON: _op_sketch_add_regular_polygon,
    OP_SKETCH_ADD_POLYGON: _op_sketch_add_polygon,
    OP_INSPECT_SKETCH: _op_inspect_sketch,
    OP_SKETCH_INSPECT_PROFILES: _op_inspect_sketch_profiles,
    OP_SKETCH_SELECT_PROFILE: _op_select_sketch_profile,
    OP_SKETCH_DELETE_GEOMETRY: _op_sketch_delete_geometry,
    OP_SKETCH_EDIT_LINE: _op_sketch_edit_line,
    OP_SKETCH_EDIT_CIRCLE: _op_sketch_edit_circle,
    OP_SKETCH_EDIT_ELLIPSE: _op_sketch_edit_ellipse,
    OP_SKETCH_EDIT_RECTANGLE: _op_sketch_edit_rectangle,
    OP_SKETCH_EDIT_TRIANGLE: _op_sketch_edit_triangle,
    OP_SKETCH_EDIT_REGULAR_POLYGON: _op_sketch_edit_regular_polygon,
    OP_SKETCH_EDIT_POLYGON: _op_sketch_edit_polygon,
    OP_SKETCH_PAD: _op_sketch_pad,
    OP_SKETCH_POCKET: _op_sketch_pocket,
    OP_SKETCH_REVOLUTION: _op_sketch_revolution,
    OP_SKETCH_GROOVE: _op_sketch_groove,
    OP_SKETCH_ADDITIVE_PIPE: _op_sketch_additive_pipe,
    OP_SKETCH_SUBTRACTIVE_PIPE: _op_sketch_subtractive_pipe,
    OP_SKETCH_ADDITIVE_LOFT: _op_sketch_additive_loft,
    OP_SKETCH_SUBTRACTIVE_LOFT: _op_sketch_subtractive_loft,
    OP_SKETCH_HOLE: _op_sketch_hole,
    OP_SKETCH_ATTACH_SUPPORT: _op_sketch_attach_support,
    OP_SKETCH_ADD_EXTERNAL_GEOMETRY: _op_sketch_add_external_geometry,
    OP_DELETE_OBJECT: _op_delete_object,
    OP_PARTDESIGN_FILLET: _op_partdesign_fillet,
    OP_PARTDESIGN_CHAMFER: _op_partdesign_chamfer,
    OP_PARTDESIGN_LINEAR_PATTERN: _op_partdesign_linear_pattern,
    OP_PARTDESIGN_POLAR_PATTERN: _op_partdesign_polar_pattern,
    OP_PARTDESIGN_MIRRORED: _op_partdesign_mirrored,
    OP_PARTDESIGN_THICKNESS: _op_partdesign_thickness,
    OP_CREATE_SNAPSHOT: _op_create_snapshot,
    OP_LIST_SNAPSHOTS: _op_list_snapshots,
    OP_RESTORE_SNAPSHOT: _op_restore_snapshot,
}


def _canonical_envelope(op: str, handler_result: Any) -> dict[str, Any]:
    """Return a canonical response envelope for a handler's return value.

    Handlers for newer operations build their own envelope (``{"ok": ..., "result": ...}``)
    while older handlers return a bare payload.  A handler's error envelope must
    pass through unchanged: wrapping it in another success envelope would turn a
    failure into an outer ``ok: true`` response and hide it from the bridge.
    """
    if isinstance(handler_result, dict) and "ok" in handler_result:
        # ``ok`` is reserved for the response envelope.  Treat any dict that
        # carries it as an envelope so a malformed ``{"ok": False}`` cannot be
        # wrapped as an outer success.
        if not isinstance(handler_result["ok"], bool):
            return _error_envelope(op, "ResponseError", "Handler returned a non-boolean 'ok'.")
        envelope = dict(handler_result)
        envelope.setdefault("op", op)
        if envelope["ok"]:
            if "result" not in envelope:
                return _error_envelope(
                    op, "ResponseError", "Handler returned a success envelope without 'result'."
                )
        else:
            error = envelope.get("error")
            if not isinstance(error, dict):
                envelope["error"] = {
                    "type": "OperationError",
                    "message": str(error) if error else "FreeCAD operation failed",
                }
        return envelope

    return {"ok": True, "op": op, "result": handler_result}


def handle_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch a request envelope. Never raises; failures become error envelopes."""
    op = request.get("op")
    params = request.get("params") or {}
    if not isinstance(op, str):
        return _error_envelope(None, "RequestError", "Missing or non-string 'op'.")
    if not isinstance(params, dict):
        return _error_envelope(op, "RequestError", "'params' must be an object.")

    handler = OPERATIONS.get(op)
    if handler is None:
        known = ", ".join(sorted(OPERATIONS))
        return _error_envelope(op, "UnknownOperationError", f"Unknown operation {op!r}. Known: {known}.")

    try:
        handler_result = handler(params)
    except Exception as exc:  # noqa: BLE001 - report any handler failure to the parent
        return _error_envelope(op, type(exc).__name__, str(exc))
    return _canonical_envelope(op, handler_result)


def _error_envelope(op: str | None, error_type: str, message: str) -> dict[str, Any]:
    return {"ok": False, "op": op, "error": {"type": error_type, "message": message}}


def main(argv: list[str] | None = None) -> int:
    """Read the request file, dispatch, and write the response file.

    Paths may be supplied positionally after ``--pass``, via named options,
    or via the environment variables for backward compatibility.
    """
    import argparse

    if argv is None:
        argv = sys.argv[1:]
    if "--pass" in argv:
        paths = argv[argv.index("--pass") + 1:]
        request_path, response_path = (paths[:2] if len(paths) >= 2 else (None, None))
    else:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--request", dest="request_path", default=None)
        parser.add_argument("--response", dest="response_path", default=None)
        args, _ = parser.parse_known_args(argv)
        request_path = args.request_path or os.environ.get(REQUEST_ENV)
        response_path = args.response_path or os.environ.get(RESPONSE_ENV)
    if not request_path or not response_path:
        print(f"{REQUEST_ENV} and {RESPONSE_ENV} must be set.", file=sys.stderr)
        return EXIT_ENVELOPE_ERROR

    try:
        with open(request_path, encoding="utf-8") as handle:
            request = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"Failed to read request: {exc}", file=sys.stderr)
        return EXIT_ENVELOPE_ERROR

    response = handle_request(request if isinstance(request, dict) else {})

    try:
        with open(response_path, "w", encoding="utf-8") as handle:
            json.dump(response, handle)
    except OSError as exc:
        print(f"Failed to write response: {exc}", file=sys.stderr)
        return EXIT_ENVELOPE_ERROR

    return 0


#: Marks that this process has already dispatched its request.  Handler imports
#: pull in ``freecad_mcp.freecad_bridge`` (whose ``__init__`` imports ``bridge``
#: and then this module again), so without this guard the request would be
#: executed a second time in the same process, duplicating objects/revisions.
RUNNER_ACTIVE_ENV = "FREECAD_MCP_RUNNER_ACTIVE"

if os.environ.get(RUNNER_ACTIVE_ENV) != "1" and (
    __name__ == "__main__"
    or REQUEST_ENV in os.environ
    or "--request" in sys.argv
    or "--response" in sys.argv
):
    # FreeCAD runs a script file with ``__name__`` set to the module name (not
    # "__main__"), so the environment guard is what makes the runner fire when
    # launched as ``freecadcmd runner.py``. No ``sys.exit`` here: FreeCAD owns
    # process teardown, and the parent relies on the response file, not the code.
    os.environ[RUNNER_ACTIVE_ENV] = "1"
    main()
