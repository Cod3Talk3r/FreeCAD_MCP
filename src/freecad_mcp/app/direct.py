"""Direct operations delegated to an injected FreeCAD execution adapter."""

from __future__ import annotations

import math
from typing import Any

from freecad_mcp.freecad_bridge import FreeCadOperationError
from freecad_mcp.freecad_bridge.runner import MAX_POLYGON_SIDES

from .contracts import BackendStatus, BuildRequest, BuildResult

_BOX_PROPS = {"Length", "Width", "Height"}
_CYLINDER_PROPS = {"Radius", "Height"}


def validate_direct_request(op: str, params: Any) -> None:
    """Apply the direct tools' validation before invoking an executor."""
    if op in ("create_box", "tank"):
        if any(params.get(key, default) <= 0 for key, default in zip(
            ("length", "width", "height"), (10, 5, 3) if op == "create_box" else (20, 15, 10)
        )):
            raise ValueError("length, width, height must be positive")
    elif op == "create_cylinder":
        if params.get("radius", 2) <= 0 or params.get("height", 7) <= 0:
            raise ValueError("radius and height must be positive")
    elif op == "set_parameter":
        if "property" in params:
            prop = params["property"].strip()
            if prop not in _BOX_PROPS | _CYLINDER_PROPS:
                raise ValueError(
                    f"Property '{prop}' is not whitelisted. "
                    f"Box: {_BOX_PROPS}; Cylinder: {_CYLINDER_PROPS}"
                )
        if "value" in params and params["value"] <= 0:
            raise ValueError("value must be positive")
    elif op == "boolean_cut":
        if "base_object" in params and "tool_object" in params and params["base_object"] == params["tool_object"]:
            raise ValueError("base_object and tool_object must be different")
    elif op == "tubular_coil":
        for key, default in (("coil_radius", 10), ("tube_radius", 2), ("num_turns", 5), ("pitch", 5)):
            if params.get(key, default) <= 0:
                raise ValueError(f"{key} must be positive")
    elif op == "create_sketch":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
    elif op == "sketch_add_rectangle":
        for key in ("x", "y", "width", "height"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
        if params["width"] <= 0:
            raise ValueError("width must be positive")
        if params["height"] <= 0:
            raise ValueError("height must be positive")
    elif op == "sketch_add_circle":
        for key in ("center_x", "center_y", "radius"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
        if params["radius"] <= 0:
            raise ValueError("radius must be positive")
    elif op == "sketch_add_triangle":
        for key in ("x1", "y1", "x2", "y2", "x3", "y3"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
    elif op == "sketch_add_ellipse":
        for key in ("center_x", "center_y", "major_radius", "minor_radius"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
        if params["major_radius"] <= 0:
            raise ValueError("major_radius must be positive")
        if params["minor_radius"] <= 0:
            raise ValueError("minor_radius must be positive")
    elif op == "sketch_add_regular_polygon":
        for key in ("center_x", "center_y", "radius"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
        if params["radius"] <= 0:
            raise ValueError("radius must be positive")
        try:
            sides = int(params.get("sides"))
        except (TypeError, ValueError):
            raise ValueError("sides must be an integer.")
        if sides < 3:
            raise ValueError("sides must be at least 3.")
        if sides > MAX_POLYGON_SIDES:
            raise ValueError(f"sides must not exceed {MAX_POLYGON_SIDES}.")
        # rotation_deg defaults to 0 if omitted.
        try:
            rotation_val = float(params.get("rotation_deg", 0))
        except (TypeError, ValueError):
            raise ValueError("rotation_deg must be a finite number.")
        if not math.isfinite(rotation_val):
            raise ValueError("rotation_deg must be a finite number.")
    elif op == "sketch_add_polygon":
        vertices = params.get("vertices")
        if not isinstance(vertices, list) or len(vertices) < 3:
            raise ValueError("vertices must be a list of at least 3 {x, y} objects.")
        for idx, v in enumerate(vertices):
            if not isinstance(v, dict):
                raise ValueError(f"vertices[{idx}] must be an object with 'x' and 'y'.")
            for key in ("x", "y"):
                try:
                    value = float(v.get(key))
                except (TypeError, ValueError):
                    raise ValueError(f"vertices[{idx}] {key} must be a finite number.")
                if not math.isfinite(value):
                    raise ValueError(f"vertices[{idx}] {key} must be a finite number.")
    elif op == "inspect_sketch":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
    elif op == "sketch_delete_geometry":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
        geometry_index = params.get("geometry_index")
        if isinstance(geometry_index, bool) or not isinstance(geometry_index, int):
            raise ValueError("geometry_index must be an integer (boolean values are not accepted).")
    elif op == "sketch_edit_line":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
        geometry_index = params.get("geometry_index")
        if isinstance(geometry_index, bool) or not isinstance(geometry_index, int):
            raise ValueError("geometry_index must be an integer (boolean values are not accepted).")
        for key in ("start_x", "start_y", "end_x", "end_y"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
    elif op == "sketch_edit_circle":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
        geometry_index = params.get("geometry_index")
        if isinstance(geometry_index, bool) or not isinstance(geometry_index, int):
            raise ValueError("geometry_index must be an integer (boolean values are not accepted).")
        for key in ("center_x", "center_y", "radius"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
        if params["radius"] <= 0:
            raise ValueError("radius must be positive")
    elif op == "sketch_edit_ellipse":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
        geometry_index = params.get("geometry_index")
        if isinstance(geometry_index, bool) or not isinstance(geometry_index, int):
            raise ValueError("geometry_index must be an integer (boolean values are not accepted).")
        converted = {}
        for key in ("center_x", "center_y", "major_radius", "minor_radius"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
            converted[key] = value
        # rotation_deg defaults to 0 when omitted.
        raw_rotation = params.get("rotation_deg", 0)
        if raw_rotation is None:
            raw_rotation = 0
        try:
            rotation_val = float(raw_rotation)
        except (TypeError, ValueError):
            raise ValueError("rotation_deg must be a finite number.")
        if not math.isfinite(rotation_val):
            raise ValueError("rotation_deg must be a finite number.")
        converted["rotation_deg"] = rotation_val
        if converted["major_radius"] <= 0:
            raise ValueError("major_radius must be positive")
        if converted["minor_radius"] <= 0:
            raise ValueError("minor_radius must be positive")
        if converted["major_radius"] < converted["minor_radius"]:
            raise ValueError("major_radius must be greater than or equal to minor_radius")
    elif op == "sketch_edit_rectangle":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
        geometry_index = params.get("geometry_index")
        if isinstance(geometry_index, bool) or not isinstance(geometry_index, int):
            raise ValueError("geometry_index must be an integer (boolean values are not accepted).")
        converted = {}
        for key in ("x", "y", "width", "height"):
            try:
                value = float(params.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number.")
            converted[key] = value
        raw_rotation = params.get("rotation_deg", 0)
        if raw_rotation is None:
            raw_rotation = 0
        try:
            rotation_val = float(raw_rotation)
        except (TypeError, ValueError):
            raise ValueError("rotation_deg must be a finite number.")
        if not math.isfinite(rotation_val):
            raise ValueError("rotation_deg must be a finite number.")
        converted["rotation_deg"] = rotation_val
        if converted["width"] <= 0:
            raise ValueError("width must be positive")
        if converted["height"] <= 0:
            raise ValueError("height must be positive")
    elif op == "sketch_edit_triangle":
        if not params.get("sketch_name", "").strip():
            raise ValueError("sketch_name must not be empty.")
        geometry_index = params.get("geometry_index")
        if isinstance(geometry_index, bool) or not isinstance(geometry_index, int):
            raise ValueError("geometry_index must be an integer (boolean values are not accepted).")
        vertices = params.get("vertices")
        if not isinstance(vertices, list) or len(vertices) != 3:
            raise ValueError("vertices must be a list of exactly three {x, y} objects.")
        for i, v in enumerate(vertices):
            if not isinstance(v, dict) or "x" not in v or "y" not in v:
                raise ValueError(f"vertices[{i}] must be an object with 'x' and 'y' keys.")
            for name in ("x", "y"):
                try:
                    value = float(v[name])
                except (TypeError, ValueError):
                    raise ValueError(f"vertices[{i}].{name} must be a finite number.")
                if not math.isfinite(value):
                    raise ValueError(f"vertices[{i}].{name} must be a finite number.")


class DirectBackend:
    def __init__(self, executor: Any) -> None:
        self._executor = executor

    def status(self) -> BackendStatus:
        return BackendStatus(available=None, capabilities=("direct_operations",), detail="Executor configured; availability has not been checked.")

    def execute(self, request: BuildRequest) -> BuildResult:
        op = request.operation
        validate_direct_request(op, request.parameters)
        try:
            result = self._executor.run(op, dict(request.parameters), timeout=request.timeout)
            if isinstance(result, dict) and "ok" in result:
                if not isinstance(result["ok"], bool):
                    raise FreeCadOperationError(
                        f"Bridge returned a non-boolean 'ok': {result!r}", error_type="BridgeError"
                    )
                if result["ok"]:
                    payload = result.get("result", {})
                    result = payload if isinstance(payload, dict) else {"value": payload}
                else:
                    error = result.get("error")
                    if isinstance(error, dict):
                        message = str(error.get("message", "FreeCAD operation failed"))
                        error_type = str(error.get("type", "Error"))
                    else:
                        message = str(error or "FreeCAD operation failed")
                        error_type = "Error"
                    raise FreeCadOperationError(message, error_type=error_type)
            if not isinstance(result, dict):
                raise FreeCadOperationError(
                    f"Unexpected bridge response format: {result!r}", error_type="BridgeError"
                )
            return BuildResult(ok=True, status="completed", data=result)
        except FreeCadOperationError:
            raise
        except Exception as exc:
            raise FreeCadOperationError(
                f"Bridge operation {op!r} failed: {exc}", error_type=type(exc).__name__
            )
