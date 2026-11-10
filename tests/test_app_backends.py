"""Application contracts and backend delegation without FreeCAD or Taiga."""

from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest

from freecad_mcp.app.contracts import ApplicationError, BackendStatus, BuildRequest, BuildResult
from freecad_mcp.app.direct import DirectBackend
from freecad_mcp.app.taiga import TaigaBuildBackend
from freecad_mcp.freecad_bridge import FreeCadOperationError


def test_contract_defaults_are_independent_and_frozen() -> None:
    first = BuildRequest("box")
    second = BuildRequest("box")
    assert first.parameters == second.parameters == {}
    assert first.parameters is not second.parameters
    assert first.timeout is None
    assert BuildResult(True, "completed").data == {}
    assert BuildResult(False, "failed", error=ApplicationError("bad", "Error")).error.message == "bad"
    with pytest.raises(FrozenInstanceError):
        first.operation = "other"


def test_direct_delegates_and_returns_payload() -> None:
    executor = Mock()
    executor.run.return_value = {"document_id": "demo"}
    backend = DirectBackend(executor)
    result = backend.execute(BuildRequest("list_objects", MappingProxyType({"document_id": "demo"}), 3.5))
    executor.run.assert_called_once_with("list_objects", {"document_id": "demo"}, timeout=3.5)
    assert result == BuildResult(True, "completed", {"document_id": "demo"})
    assert backend.status().available is None
    assert "direct_operations" in backend.status().capabilities


@pytest.mark.parametrize("payload,expected", [
    ({"ok": True, "result": {"name": "Box"}}, {"name": "Box"}),
    ({"ok": True, "result": 42}, {"value": 42}),
])
def test_direct_unwraps_legacy_success(payload: dict, expected: dict) -> None:
    executor = Mock()
    executor.run.return_value = payload
    assert DirectBackend(executor).execute(BuildRequest("box")).data == expected


@pytest.mark.parametrize("payload,error_type", [
    ({"ok": False, "error": {"type": "OSError", "message": "invalid"}}, "OSError"),
    ({"ok": False, "error": "invalid"}, "Error"),
    ({"ok": "false"}, "BridgeError"),
    (None, "BridgeError"),
])
def test_direct_rejects_bridge_failures(payload: object, error_type: str) -> None:
    executor = Mock()
    executor.run.return_value = payload
    with pytest.raises(FreeCadOperationError) as excinfo:
        DirectBackend(executor).execute(BuildRequest("box"))
    assert excinfo.value.error_type == error_type
    assert str(excinfo.value)


def test_direct_wraps_unexpected_executor_error() -> None:
    executor = Mock()
    executor.run.side_effect = RuntimeError("disconnected")
    with pytest.raises(FreeCadOperationError, match="disconnected") as excinfo:
        DirectBackend(executor).execute(BuildRequest("box"))
    assert excinfo.value.error_type == "RuntimeError"


def test_taiga_forwards_build_and_converts_result() -> None:
    runtime = Mock()
    runtime.run.return_value = SimpleNamespace(ok=True, status="completed", result={"artifact": "x"}, error=None, error_type=None)
    backend = TaigaBuildBackend(runtime)
    goal = {"features": []}
    result = backend.execute(BuildRequest("build", {"goal": goal, "document_id": "doc", "workspace": "/tmp/ws"}, 12))
    runtime.run.assert_called_once_with(goal, document_id="doc", workspace="/tmp/ws", timeout=12)
    assert result == BuildResult(True, "completed", {"artifact": "x"})
    assert backend.status().available is None
    assert "goal_build" in backend.status().capabilities


def test_taiga_preserves_failure_and_runtime_default_timeout() -> None:
    runtime = Mock()
    runtime.run.return_value = SimpleNamespace(ok=False, status="invalid_goal", result=None, error="bad goal", error_type="InvalidGoalError")
    backend = TaigaBuildBackend(runtime)
    result = backend.execute(BuildRequest("build", {"goal": {}}))
    runtime.run.assert_called_once_with({}, document_id=None, workspace=None)
    assert result == BuildResult(False, "invalid_goal", None, ApplicationError("bad goal", "InvalidGoalError"))


@pytest.mark.parametrize("payload", [None, {}])
def test_taiga_preserves_absent_and_empty_payload(payload: object) -> None:
    runtime = Mock()
    runtime.run.return_value = SimpleNamespace(ok=False, status="failure", result=payload, error=None, error_type=None)
    result = TaigaBuildBackend(runtime).execute(BuildRequest("build", {"goal": {}}))
    assert result.data is payload


@pytest.mark.parametrize("operation,params,message", [
    ("create_box", {"length": 0}, "length, width, height must be positive"),
    ("create_box", {"width": -1}, "length, width, height must be positive"),
    ("tank", {"height": 0}, "length, width, height must be positive"),
    ("create_cylinder", {"radius": 0}, "radius and height must be positive"),
    ("create_cylinder", {"height": -1}, "radius and height must be positive"),
    ("set_parameter", {"property": "Length", "value": 0}, "value must be positive"),
    ("set_parameter", {"property": "Unknown", "value": 1}, "not whitelisted"),
    ("boolean_cut", {"base_object": "Box", "tool_object": "Box"}, "must be different"),
    ("tubular_coil", {"coil_radius": 0}, "coil_radius must be positive"),
    ("tubular_coil", {"tube_radius": 0}, "tube_radius must be positive"),
    ("tubular_coil", {"num_turns": 0}, "num_turns must be positive"),
    ("tubular_coil", {"pitch": 0}, "pitch must be positive"),
    ("create_sketch", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("create_sketch", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_add_rectangle", {"x": 0, "y": 0, "width": 0, "height": 1}, "width must be positive"),
    ("sketch_add_rectangle", {"x": 0, "y": 0, "width": 1, "height": -1}, "height must be positive"),
    ("sketch_add_rectangle", {"x": float("nan"), "y": 0, "width": 1, "height": 1}, "must be a finite number"),
    ("sketch_add_rectangle", {"x": 0, "y": 0, "width": float("inf"), "height": 1}, "must be a finite number"),
    ("sketch_add_circle", {"center_x": 0, "center_y": 0, "radius": 0}, "radius must be positive"),
    ("sketch_add_circle", {"center_x": 0, "center_y": 0, "radius": -1}, "radius must be positive"),
    ("sketch_add_circle", {"center_x": float("nan"), "center_y": 0, "radius": 1}, "must be a finite number"),
    ("sketch_add_triangle", {"x1": float("nan"), "y1": 0, "x2": 1, "y2": 0, "x3": 0, "y3": 1}, "must be a finite number"),
    ("sketch_add_ellipse", {"center_x": 0, "center_y": 0, "major_radius": 0, "minor_radius": 1}, "major_radius must be positive"),
    ("sketch_add_ellipse", {"center_x": 0, "center_y": 0, "major_radius": 1, "minor_radius": -1}, "minor_radius must be positive"),
    ("sketch_add_ellipse", {"center_x": float("inf"), "center_y": 0, "major_radius": 1, "minor_radius": 1}, "must be a finite number"),
    ("sketch_add_regular_polygon", {"center_x": 0, "center_y": 0, "radius": 0, "sides": 5}, "radius must be positive"),
    ("sketch_add_regular_polygon", {"center_x": 0, "center_y": 0, "radius": 1, "sides": 2}, "sides must be at least 3"),
    ("sketch_add_regular_polygon", {"center_x": 0, "center_y": 0, "radius": 1, "sides": 1001}, "sides must not exceed"),
    ("sketch_add_regular_polygon", {"center_x": float("nan"), "center_y": 0, "radius": 1, "sides": 5}, "must be a finite number"),
    ("sketch_add_polygon", {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]}, "at least 3"),
    ("sketch_add_polygon", {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": float("nan"), "y": 0}]}, "must be a finite number"),
    ("inspect_sketch", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("inspect_sketch", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_delete_geometry", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_delete_geometry", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_delete_geometry", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_delete_geometry", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_line", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_edit_line", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_edit_line", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_edit_line", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_line", {"sketch_name": "S", "geometry_index": 0, "start_x": float("nan")}, "must be a finite number"),
    ("sketch_edit_line", {"sketch_name": "S", "geometry_index": 0, "start_x": 0, "start_y": 0, "end_x": float("inf")}, "must be a finite number"),
    ("sketch_edit_ellipse", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_edit_ellipse", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": 0, "center_x": float("nan")}, "must be a finite number"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": 0, "center_x": 0, "center_y": 0, "major_radius": float("inf")}, "must be a finite number"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": 0, "center_x": 0, "center_y": 0, "major_radius": 0, "minor_radius": 1}, "major_radius must be positive"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": 0, "center_x": 0, "center_y": 0, "major_radius": 1, "minor_radius": -1}, "minor_radius must be positive"),
    ("sketch_edit_ellipse", {"sketch_name": "S", "geometry_index": 0, "center_x": 0, "center_y": 0, "major_radius": 2, "minor_radius": 5}, "greater than or equal"),
    ("sketch_edit_rectangle", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_edit_rectangle", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_edit_rectangle", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_edit_rectangle", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_rectangle", {"sketch_name": "S", "geometry_index": 0, "x": float("nan")}, "must be a finite number"),
    ("sketch_edit_rectangle", {"sketch_name": "S", "geometry_index": 0, "x": 0, "y": 0, "width": float("inf")}, "must be a finite number"),
    ("sketch_edit_rectangle", {"sketch_name": "S", "geometry_index": 0, "x": 0, "y": 0, "width": 0, "height": 1}, "width must be positive"),
    ("sketch_edit_rectangle", {"sketch_name": "S", "geometry_index": 0, "x": 0, "y": 0, "width": 2, "height": -1}, "height must be positive"),
    ("sketch_edit_triangle", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_edit_triangle", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_edit_triangle", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_edit_triangle", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_triangle", {"sketch_name": "S", "geometry_index": 0, "vertices": [{"x": float("nan"), "y": 0.0}, {"x": 2.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]}, "must be a finite number"),
    ("sketch_edit_triangle", {"sketch_name": "S", "geometry_index": 0, "vertices": [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": float("inf")}, {"x": 1.0, "y": 1.0}]}, "must be a finite number"),
    ("sketch_edit_triangle", {"sketch_name": "S", "geometry_index": 0, "vertices": [{"x": 0.0, "y": 0.0}, {"x": 2.0}]}, "vertices must be a list of exactly three"),
    ("sketch_edit_regular_polygon", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_edit_regular_polygon", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": 0, "center_x": float("nan"), "center_y": 0.0, "radius": 5.0, "sides": 4}, "must be a finite number"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": 0, "center_x": 0.0, "center_y": 0.0, "radius": float("inf"), "sides": 4}, "must be a finite number"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": 0, "center_x": 0.0, "center_y": 0.0, "radius": -1.0, "sides": 4}, "radius must be positive"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": 0, "center_x": 0.0, "center_y": 0.0, "radius": 5.0, "sides": 2}, "sides must be at least 3"),
    ("sketch_edit_regular_polygon", {"sketch_name": "S", "geometry_index": 0, "center_x": 0.0, "center_y": 0.0, "radius": 5.0, "sides": True}, "boolean values are not accepted"),
    ("sketch_edit_polygon", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_edit_polygon", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_edit_polygon", {"sketch_name": "S", "geometry_index": True}, "boolean values are not accepted"),
    ("sketch_edit_polygon", {"sketch_name": "S", "geometry_index": 1.5}, "must be an integer"),
    ("sketch_edit_polygon", {"sketch_name": "S", "geometry_index": 0, "vertices": [{"x": float("nan"), "y": 0.0}, {"x": 2.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]}, "must be a finite number"),
    ("sketch_edit_polygon", {"sketch_name": "S", "geometry_index": 0, "vertices": [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.0}]}, "vertices must be a list of at least 3"),
    ("sketch_pad", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_pad", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_pad", {"sketch_name": "S", "length": float("nan")}, "must be a finite number"),
    ("sketch_pad", {"sketch_name": "S", "length": float("inf")}, "must be a finite number"),
    ("sketch_pad", {"sketch_name": "S", "length": 0.0}, "length must be positive"),
    ("sketch_pad", {"sketch_name": "S", "length": -5.0}, "length must be positive"),
    ("sketch_pad", {"sketch_name": "S", "length": 10.0, "reversed": 1}, "reversed must be a boolean."),
    ("sketch_pad", {"sketch_name": "S", "length": 10.0, "reversed": "yes"}, "reversed must be a boolean."),
    ("sketch_pocket", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_pocket", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_pocket", {"sketch_name": "S", "length": float("nan")}, "must be a finite number"),
    ("sketch_pocket", {"sketch_name": "S", "length": float("inf")}, "must be a finite number"),
    ("sketch_pocket", {"sketch_name": "S", "length": 0.0}, "length must be positive"),
    ("sketch_pocket", {"sketch_name": "S", "length": -5.0}, "length must be positive"),
    ("sketch_pocket", {"sketch_name": "S", "length": 10.0, "reversed": 1}, "reversed must be a boolean."),
    ("sketch_pocket", {"sketch_name": "S", "length": 10.0, "reversed": "yes"}, "reversed must be a boolean."),
    ("sketch_revolution", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_revolution", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "InvalidAxis", "angle": 360.0}, "axis must be one of"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "X_Axis", "angle": float("nan")}, "must be a finite number"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "X_Axis", "angle": float("inf")}, "must be a finite number"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "X_Axis", "angle": 0.0}, "angle must be positive"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "X_Axis", "angle": -45.0}, "angle must be positive"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "X_Axis", "angle": 400.0}, "must not exceed 360 degrees"),
    ("sketch_revolution", {"sketch_name": "S", "axis": "X_Axis", "angle": "not-a-number"}, "must be a finite number"),
    ("sketch_groove", {"sketch_name": ""}, "sketch_name must not be empty"),
    ("sketch_groove", {"sketch_name": "   "}, "sketch_name must not be empty"),
    ("sketch_groove", {"sketch_name": "S", "axis": "InvalidAxis", "angle": 360.0}, "axis must be one of"),
    ("sketch_groove", {"sketch_name": "S", "axis": "X_Axis", "angle": float("nan")}, "must be a finite number"),
    ("sketch_groove", {"sketch_name": "S", "axis": "X_Axis", "angle": float("inf")}, "must be a finite number"),
    ("sketch_groove", {"sketch_name": "S", "axis": "X_Axis", "angle": 0.0}, "angle must be positive"),
    ("sketch_groove", {"sketch_name": "S", "axis": "X_Axis", "angle": -45.0}, "angle must be positive"),
    ("sketch_groove", {"sketch_name": "S", "axis": "X_Axis", "angle": 400.0}, "must not exceed 360 degrees"),
    ("sketch_groove", {"sketch_name": "S", "axis": "X_Axis", "angle": "not-a-number"}, "must be a finite number"),
    ("partdesign_fillet", {"object_name": ""}, "object_name must not be empty"),
    ("partdesign_fillet", {"object_name": "   "}, "object_name must not be empty"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": []}, "non-empty"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [1, 1]}, "duplicate edge index"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [0]}, "must be a positive integer"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [-1]}, "must be a positive integer"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [True]}, "boolean values are not accepted"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [1.5]}, "must be an integer"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [1], "radius": float("nan")}, "must be a finite number"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [1], "radius": float("inf")}, "must be a finite number"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [1], "radius": 0.0}, "radius must be positive"),
    ("partdesign_fillet", {"object_name": "O", "edge_indices": [1], "radius": -5.0}, "radius must be positive"),
    ("partdesign_chamfer", {"object_name": ""}, "object_name must not be empty"),
    ("partdesign_chamfer", {"object_name": "   "}, "object_name must not be empty"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": []}, "non-empty"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [1, 1]}, "duplicate edge index"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [0]}, "must be a positive integer"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [-1]}, "must be a positive integer"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [True]}, "boolean values are not accepted"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [1.5]}, "must be an integer"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [1], "size": float("nan")}, "must be a finite number"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [1], "size": float("inf")}, "must be a finite number"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [1], "size": 0.0}, "size must be positive"),
    ("partdesign_chamfer", {"object_name": "O", "edge_indices": [1], "size": -5.0}, "size must be positive"),
    ("partdesign_linear_pattern", {"object_name": ""}, "object_name must not be empty"),
    ("partdesign_linear_pattern", {"object_name": "O", "direction": "INVALID"}, "direction must be one of"),
    ("partdesign_linear_pattern", {"object_name": "O", "direction": "X_Axis", "length": 0.0}, "length must be positive"),
    ("partdesign_linear_pattern", {"object_name": "O", "direction": "X_Axis", "length": -5.0}, "length must be positive"),
    ("partdesign_linear_pattern", {"object_name": "O", "direction": "X_Axis", "length": 10.0, "occurrences": 1}, "greater than or equal to 2"),
    ("partdesign_linear_pattern", {"object_name": "O", "direction": "X_Axis", "length": 10.0, "occurrences": True}, "boolean values are not accepted"),
    ("partdesign_polar_pattern", {"object_name": ""}, "object_name must not be empty"),
    ("partdesign_polar_pattern", {"object_name": "O", "axis": "INVALID"}, "axis must be one of"),
    ("partdesign_polar_pattern", {"object_name": "O", "axis": "X_Axis", "angle": 0.0}, "angle must be positive"),
    ("partdesign_polar_pattern", {"object_name": "O", "axis": "X_Axis", "angle": -45.0}, "angle must be positive"),
    ("partdesign_polar_pattern", {"object_name": "O", "axis": "X_Axis", "angle": 361.0}, "angle must be at most 360"),
    ("partdesign_polar_pattern", {"object_name": "O", "axis": "X_Axis", "angle": 90.0, "occurrences": 1}, "greater than or equal to 2"),
    ("partdesign_polar_pattern", {"object_name": "O", "axis": "X_Axis", "angle": 90.0, "occurrences": True}, "boolean values are not accepted"),
    ("partdesign_mirrored", {"object_name": ""}, "object_name must not be empty"),
    ("partdesign_mirrored", {"object_name": "O", "plane": "INVALID"}, "plane must be one of"),
    ("partdesign_thickness", {"object_name": ""}, "object_name must not be empty"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 0}, "thickness must be positive"),
    ("partdesign_thickness", {"object_name": "O", "thickness": -1.5}, "thickness must be positive"),
    ("partdesign_thickness", {"object_name": "O", "thickness": "x"}, "thickness must be a finite number"),
    ("partdesign_thickness", {"object_name": "O", "thickness": None}, "thickness must be a finite number"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 2.0, "reversed": "yes"}, "reversed must be a boolean"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 2.0, "faces": [3, 3]}, "duplicate face index"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 2.0, "faces": [True]}, "boolean values are not accepted"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 2.0, "faces": ["x"]}, "must be an integer"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 2.0, "faces": [0]}, "must be >= 1"),
    ("partdesign_thickness", {"object_name": "O", "thickness": 2.0, "faces": "not-a-list"}, "faces must be a list"),
])
def test_direct_rejects_invalid_requests_before_execution(operation: str, params: dict, message: str) -> None:
    executor = Mock()
    with pytest.raises(ValueError, match=message):
        DirectBackend(executor).execute(BuildRequest(operation, params))
    executor.run.assert_not_called()


def test_taiga_rejects_other_operations_without_calling_runtime() -> None:
    runtime = Mock()
    with pytest.raises(ValueError, match="build"):
        TaigaBuildBackend(runtime).execute(BuildRequest("box"))
    runtime.run.assert_not_called()


def test_backend_status_defaults() -> None:
    assert BackendStatus(False) == BackendStatus(available=False, capabilities=(), detail="")
