"""FreeCAD-side operation runner.

This module executes **inside FreeCAD's own Python interpreter**
(``freecadcmd runner.py``). It is never imported by the MCP server process.

It reads a request envelope (JSON) from the file named by ``FREECAD_MCP_REQUEST``,
dispatches the named operation to a fixed handler, and writes a response envelope
(JSON) to the file named by ``FREECAD_MCP_RESPONSE``.

Keeping the operations here - explicit and fixed - is what prevents the bridge
from becoming a general "run this code inside FreeCAD" interface.

``FreeCAD``/``Part`` are imported *lazily inside handlers* so that the envelope
and dispatch logic remain importable and unit-testable on the host interpreter.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

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

REQUEST_ENV = "FREECAD_MCP_REQUEST"
RESPONSE_ENV = "FREECAD_MCP_RESPONSE"

#: Exit code the runner returns when it could not even load/write its envelope.
EXIT_ENVELOPE_ERROR = 2


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
    from . import workspace as ws_mod

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
        if _FC.Document.exists(normalized):
            doc = _FC.openDocument(normalized)
        else:
            doc = _FC.newDocument(normalized)
        doc.recompute()
        fcstd_path = wks.document_path(normalized)

        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(tmp_path)

        atomic_save(fcstd_path, _write_tmp)

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
    from . import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or "Box1"
    length = float(params.get("length") or 10.0)
    width = float(params.get("width") or 5.0)
    height = float(params.get("height") or 3.0)
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
        doc = _FC.openDocument(normalized) if _FC.Document.exists(normalized) else _FC.newDocument(normalized)
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
            doc.saveAs(tmp_path)
        atomic_save(fcstd_path, _write_tmp)

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
            "meta_before": meta_before,
        },
    }


def _op_create_cylinder(params: dict[str, Any]) -> dict[str, Any]:
    """Create a Part::Cylinder in the current document."""
    from . import workspace as ws_mod

    document_id = params.get("document_id") or ""
    object_name = params.get("object_name") or "Cyl1"
    radius = float(params.get("radius") or 2.0)
    height = float(params.get("height") or 7.0)
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
        doc = _FC.openDocument(normalized) if _FC.Document.exists(normalized) else _FC.newDocument(normalized)
        obj_name = object_name
        counter = 0
        while doc.getObject(obj_name) is not None:
            counter += 1
            obj_name = f"{object_name}_{counter}"
        cyl = doc.addObject("Part::Cylinder", obj_name)
        cyl.Radius = radius
        cyl.Height = height
        doc.recompute()

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(tmp_path)
        atomic_save(fcstd_path, _write_tmp)

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
            "meta_before": meta_before,
        },
    }


def _op_set_parameter(params: dict[str, Any]) -> dict[str, Any]:
    """Set a whitelisted parameter on an existing object."""
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)
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
            doc.saveAs(tmp_path)
        atomic_save(fcstd_path, _write_tmp)

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
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)
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
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)
        objects = []
        for obj in doc.Objects:
            obj_info = {"name": obj.Name, "type": obj.TypeId}
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
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)
        obj = doc.getObject(object_name)
        if obj is None:
            ws_mod.release_lock(lock_path, pid)
            return {"ok": False, "op": "inspect_object", "error": f"object '{object_name}' not found"}

        info = {"name": obj.Name, "type": obj.TypeId, "label": obj.Label}
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
        if hasattr(obj, "Material"):
            info["material"] = obj.Material
        if hasattr(obj, "Color"):
            info["color"] = obj.Color
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
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)
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
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)
        meta_before = ws_mod.read_meta(wks.meta_path(normalized))

        fcstd_path = wks.document_path(normalized)
        def _write_tmp(tmp_path: str) -> None:
            doc.saveAs(tmp_path)
        atomic_save(fcstd_path, _write_tmp)

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
    from . import workspace as ws_mod

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
        doc = _FC.openDocument(normalized)

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

}

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
        return {"ok": True, "op": op, "result": handler(params)}
    except Exception as exc:  # noqa: BLE001 - report any handler failure to the parent
        return _error_envelope(op, type(exc).__name__, str(exc))


def _error_envelope(op: str | None, error_type: str, message: str) -> dict[str, Any]:
    return {"ok": False, "op": op, "error": {"type": error_type, "message": message}}


def main(argv: list[str] | None = None) -> int:
    """Read the request file, dispatch, and write the response file."""
    del argv  # the runner takes no arguments; paths come from the environment
    request_path = os.environ.get(REQUEST_ENV)
    response_path = os.environ.get(RESPONSE_ENV)
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


if __name__ == "__main__" or REQUEST_ENV in os.environ:
    # FreeCAD runs a script file with ``__name__`` set to the module name (not
    # "__main__"), so the environment guard is what makes the runner fire when
    # launched as ``freecadcmd runner.py``. No ``sys.exit`` here: FreeCAD owns
    # process teardown, and the parent relies on the response file, not the code.
    main()
