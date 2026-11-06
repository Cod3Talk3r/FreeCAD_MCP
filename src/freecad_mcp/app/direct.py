"""Direct operations delegated to an injected FreeCAD execution adapter."""

from __future__ import annotations

import math
from typing import Any

from freecad_mcp.freecad_bridge import FreeCadOperationError

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
