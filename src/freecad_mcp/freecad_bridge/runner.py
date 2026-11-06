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
OP_CREATE_SKETCH = "create_sketch"
OP_SKETCH_ADD_RECTANGLE = "sketch_add_rectangle"
OP_SKETCH_ADD_CIRCLE = "sketch_add_circle"
OP_SKETCH_ADD_TRIANGLE = "sketch_add_triangle"
OP_SKETCH_ADD_ELLIPSE = "sketch_add_ellipse"
OP_SKETCH_ADD_REGULAR_POLYGON = "sketch_add_regular_polygon"
OP_SKETCH_ADD_POLYGON = "sketch_add_polygon"
OP_INSPECT_SKETCH = "inspect_sketch"
OP_SKETCH_DELETE_GEOMETRY = "sketch_delete_geometry"
OP_SKETCH_EDIT_LINE = "sketch_edit_line"

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
    return {"document_id": normalized, "meta_before": meta_before}


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
    return {"document_id": normalized, "objects": objects}


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
        return {"ok": False, "op": "save_document", "error": f"FreeCAD error: {exc}"}

    ws_mod.release_lock(lock_path, pid)
    return {
        "ok": True,
        "op": "save_document",
        "result": {"document_id": normalized, "saved_path": str(fcstd_path), "meta_before": meta_before},
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
            "geometries": geometries,
            "placement": placement_info,
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


#: The complete, explicit set of operations the bridge can perform.
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
    OP_CREATE_SKETCH: _op_create_sketch,
    OP_SKETCH_ADD_RECTANGLE: _op_sketch_add_rectangle,
    OP_SKETCH_ADD_CIRCLE: _op_sketch_add_circle,
    OP_SKETCH_ADD_TRIANGLE: _op_sketch_add_triangle,
    OP_SKETCH_ADD_ELLIPSE: _op_sketch_add_ellipse,
    OP_SKETCH_ADD_REGULAR_POLYGON: _op_sketch_add_regular_polygon,
    OP_SKETCH_ADD_POLYGON: _op_sketch_add_polygon,
    OP_INSPECT_SKETCH: _op_inspect_sketch,
    OP_SKETCH_DELETE_GEOMETRY: _op_sketch_delete_geometry,
    OP_SKETCH_EDIT_LINE: _op_sketch_edit_line,
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
