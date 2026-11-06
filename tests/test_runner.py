"""Tests for the FreeCAD-side runner's envelope and dispatch logic.

``runner`` is importable on the host interpreter by design: it only imports
``FreeCAD`` lazily inside handlers, so dispatch/envelope behaviour can be tested
without FreeCAD.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from freecad_mcp.freecad_bridge import runner
from freecad_mcp.freecad_bridge.workspace import Workspace


def test_ping_round_trips_message() -> None:
    response = runner.handle_request({"op": runner.OP_PING, "params": {"message": "hello"}})
    assert response == {"ok": True, "op": "ping", "result": {"message": "hello"}}


def test_ping_defaults_message() -> None:
    response = runner.handle_request({"op": runner.OP_PING})
    assert response["ok"] is True
    assert response["result"]["message"] == "pong"


def test_unknown_operation_is_reported() -> None:
    response = runner.handle_request({"op": "nope"})
    assert response["ok"] is False
    assert response["error"]["type"] == "UnknownOperationError"


def test_missing_op_is_reported() -> None:
    response = runner.handle_request({})
    assert response["ok"] is False
    assert response["error"]["type"] == "RequestError"


def test_non_object_params_is_reported() -> None:
    response = runner.handle_request({"op": runner.OP_PING, "params": 5})
    assert response["ok"] is False
    assert response["error"]["type"] == "RequestError"


def test_get_version_reports_import_failure_without_freecad() -> None:
    """On the host interpreter ``import FreeCAD`` fails; that must be an error envelope."""
    response = runner.handle_request({"op": runner.OP_GET_VERSION})
    assert response["ok"] is False
    assert response["error"]["type"] == "ModuleNotFoundError"


def test_main_round_trip_via_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps({"op": runner.OP_PING, "params": {"message": "hi"}}), encoding="utf-8")

    monkeypatch.setenv(runner.REQUEST_ENV, str(request_path))
    monkeypatch.setenv(runner.RESPONSE_ENV, str(response_path))

    assert runner.main() == 0
    assert json.loads(response_path.read_text(encoding="utf-8"))["result"] == {"message": "hi"}


def test_main_requires_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(runner.REQUEST_ENV, raising=False)
    monkeypatch.delenv(runner.RESPONSE_ENV, raising=False)
    assert runner.main() == runner.EXIT_ENVELOPE_ERROR


@pytest.mark.parametrize("transport", ["pass", "options"])
def test_main_argument_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str) -> None:
    request = tmp_path / "request with spaces.json"
    response = tmp_path / "response with spaces.json"
    request.write_text(json.dumps({"op": "ping", "params": {"message": "argument"}}), encoding="utf-8")
    monkeypatch.setenv(runner.REQUEST_ENV, "invalid")
    monkeypatch.setenv(runner.RESPONSE_ENV, "invalid")
    argv = (
        ["--pass", str(request), str(response)]
        if transport == "pass"
        else ["--request", str(request), "--response", str(response)]
    )
    assert runner.main(argv) == 0
    assert json.loads(response.read_text(encoding="utf-8"))["result"] == {"message": "argument"}


def test_main_incomplete_pass_does_not_fall_back_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(runner.REQUEST_ENV, "invalid")
    monkeypatch.setenv(runner.RESPONSE_ENV, "invalid")
    assert runner.main(["--pass", "only-one"]) == runner.EXIT_ENVELOPE_ERROR


# ---------------------------------------------------------------------------
# Document open/create semantics (host-level, FreeCAD mocked)
# ---------------------------------------------------------------------------


class _FakeFreeCad:
    """Minimal stand-in for the FreeCAD module used by the handlers."""

    def __init__(self, *, open_error: Exception | None = None, doc: object | None = None) -> None:
        self.open_error = open_error
        self.doc = doc
        self.open_calls: list[str] = []
        self.new_calls: list[str] = []
        self.Vector = _FakeVector
        self.Placement = _FakePlacement
        self.Rotation = _FakeRotation

    def openDocument(self, path: str) -> object:
        self.open_calls.append(path)
        if self.open_error is not None:
            raise self.open_error
        return self.doc

    def newDocument(self, name: str) -> object:
        self.new_calls.append(name)
        return self.doc


class _FakeVector:
    def __init__(self, x: float, y: float, z: float) -> None:
        self.x = x
        self.y = y
        self.z = z


class _FakeRotation:
    pass


class _FakePlacement:
    def __init__(self, base: object, rotation: object) -> None:
        self.Base = base
        self.Rotation = rotation


class _FakeSegment:
    def __init__(self, start: object, end: object) -> None:
        self.start = start
        self.end = end


class _FakeCircle:
    def __init__(self, center: object, normal: object, radius: float) -> None:
        self.center = center
        self.normal = normal
        self.radius = radius


class _FakePart:
    """Minimal stand-in for FreeCAD's Part module."""

    @staticmethod
    def LineSegment(start: object, end: object) -> _FakeSegment:
        return _FakeSegment(start, end)

    @staticmethod
    def Circle(center: object, normal: object, radius: float) -> _FakeCircle:
        return _FakeCircle(center, normal, radius)


class _FakeSketch:
    """Minimal stand-in for a Sketcher::SketchObject."""

    def __init__(self, name: str) -> None:
        self.Name = name
        self.Label = name
        self.TypeId = "Sketcher::SketchObject"
        self.Placement = None
        self.Geometry: list = []
        self.Constraints: list = []

    def addGeometry(self, geometry: object, check: bool = False) -> None:
        self.Geometry.append(geometry)


class _FakeDoc:
    def __init__(self) -> None:
        self.objects: dict[str, object] = {}

    def getObject(self, name: str) -> object | None:
        return self.objects.get(name)

    def addObject(self, type_id: str, name: str) -> object:
        if type_id == "Sketcher::SketchObject":
            obj: object = _FakeSketch(name)
        else:
            obj = SimpleNamespace(TypeId=type_id, Length=0.0, Width=0.0, Height=0.0)
        self.objects[name] = obj
        return obj

    def recompute(self) -> None:
        return None

    def saveAs(self, path: str) -> None:
        Path(path).write_bytes(b"fake fcstd")


def test_workspace_accepts_string_root(tmp_path: Path) -> None:
    wks = Workspace(root=str(tmp_path))
    assert wks.root == tmp_path
    assert wks.document_path("demo") == tmp_path / "demo.fcstd"


def test_open_or_create_document_creates_only_when_absent(tmp_path: Path) -> None:
    wks = Workspace(root=tmp_path)
    fc = _FakeFreeCad(doc="new-doc")

    result = runner._open_or_create_document(fc, wks, "missingdoc")

    assert result == "new-doc"
    assert fc.new_calls == ["missingdoc"]
    assert fc.open_calls == []


def test_open_or_create_document_opens_existing_file_by_path(tmp_path: Path) -> None:
    wks = Workspace(root=tmp_path)
    target = wks.document_path("existingdoc")
    target.write_bytes(b"stored")
    fc = _FakeFreeCad(doc="opened-doc")

    result = runner._open_or_create_document(fc, wks, "existingdoc")

    assert result == "opened-doc"
    assert fc.open_calls == [str(target)]
    assert fc.new_calls == []


def test_open_or_create_document_propagates_open_failure_without_creating(tmp_path: Path) -> None:
    wks = Workspace(root=tmp_path)
    target = wks.document_path("brokendoc")
    target.write_bytes(b"corrupt")
    fc = _FakeFreeCad(open_error=OSError("Invalid project file"))

    with pytest.raises(OSError, match="Invalid project file"):
        runner._open_or_create_document(fc, wks, "brokendoc")

    assert fc.open_calls == [str(target)]
    assert fc.new_calls == []


def test_open_existing_document_reports_missing_without_creating(tmp_path: Path) -> None:
    wks = Workspace(root=tmp_path)
    fc = _FakeFreeCad(doc="opened-doc")

    with pytest.raises(FileNotFoundError, match="missingdoc"):
        runner._open_existing_document(fc, wks, "missingdoc")

    assert fc.open_calls == []
    assert fc.new_calls == []


def test_tank_reports_corrupt_existing_document_without_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "brokendoc.fcstd"
    original = b"not a valid fcstd archive"
    target.write_bytes(original)

    fc = _FakeFreeCad(open_error=OSError(f"Invalid project file {target}"))
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Part", types.ModuleType("Part"))

    result = runner._op_tank({"document_id": "brokendoc", "workspace": str(tmp_path)})

    assert result["ok"] is False
    assert "Invalid project file" in result["error"]
    assert fc.new_calls == []
    assert target.read_bytes() == original
    assert not (tmp_path / "brokendoc.lock").exists()


def test_tank_creates_document_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _FakeDoc()
    fc = _FakeFreeCad(doc=doc)
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Part", types.ModuleType("Part"))

    result = runner._op_tank(
        {"document_id": "newdoc", "workspace": str(tmp_path), "length": 7.0, "width": 6.0, "height": 5.0}
    )

    assert result["ok"] is True
    assert fc.new_calls == ["newdoc"]
    assert fc.open_calls == []
    box = doc.objects["Tank1"]
    assert box.Length == 7.0 and box.Width == 6.0 and box.Height == 5.0
    assert (tmp_path / "newdoc.fcstd").read_bytes() == b"fake fcstd"
    assert not (tmp_path / "newdoc.lock").exists()


def test_handle_request_does_not_double_wrap_handler_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    fc = _FakeFreeCad(doc=doc)
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Part", types.ModuleType("Part"))

    response = runner.handle_request(
        {"op": "tank", "params": {"document_id": "wrapdoc", "workspace": str(tmp_path)}}
    )

    assert response["ok"] is True
    assert response["op"] == "tank"
    assert response["result"]["object_name"] == "Tank1"
    assert "ok" not in response["result"]


def test_handle_request_reports_handler_failure_as_error_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "brokendoc.fcstd"
    original = b"not a valid fcstd archive"
    target.write_bytes(original)

    fc = _FakeFreeCad(open_error=OSError(f"Invalid project file {target}"))
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Part", types.ModuleType("Part"))

    response = runner.handle_request(
        {"op": "tank", "params": {"document_id": "brokendoc", "workspace": str(tmp_path)}}
    )

    assert response["ok"] is False
    assert "result" not in response
    assert response["error"]["type"] == "OperationError"
    assert "Invalid project file" in response["error"]["message"]
    assert fc.new_calls == []
    assert target.read_bytes() == original
    assert not (tmp_path / "brokendoc.lock").exists()


def test_handle_request_wraps_bare_handler_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    fc = _FakeFreeCad(doc=doc)
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Part", types.ModuleType("Part"))

    response = runner.handle_request(
        {"op": "create_document", "params": {"document_id": "baredoc", "workspace": str(tmp_path)}}
    )

    assert response["ok"] is True
    assert response["op"] == "create_document"
    assert response["result"]["document_id"] == "baredoc"
    assert "ok" not in response["result"]


def test_handle_request_does_not_mask_resultless_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(runner.OPERATIONS, "fake_op", lambda params: {"ok": False})

    response = runner.handle_request({"op": "fake_op"})

    assert response["ok"] is False
    assert "result" not in response
    assert response["error"]["type"] == "OperationError"
    assert response["error"]["message"]


def test_handle_request_rejects_success_envelope_without_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(runner.OPERATIONS, "fake_op", lambda params: {"ok": True})

    response = runner.handle_request({"op": "fake_op"})

    assert response["ok"] is False
    assert "result" not in response
    assert response["error"]["type"] == "ResponseError"
    assert response["error"]["message"]


def test_handle_request_rejects_non_boolean_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(runner.OPERATIONS, "fake_op", lambda params: {"ok": "false"})

    response = runner.handle_request({"op": "fake_op"})

    assert response["ok"] is False
    assert "result" not in response
    assert response["error"]["type"] == "ResponseError"
    assert response["error"]["message"]


# ---------------------------------------------------------------------------
# Milestone 9-A: Sketcher operations (FreeCAD mocked)
# ---------------------------------------------------------------------------

def _install_fake_freecad(
    monkeypatch: pytest.MonkeyPatch, doc: _FakeDoc
) -> None:
    fc = _FakeFreeCad(doc=doc)
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    part = types.ModuleType("Part")
    part.LineSegment = _FakePart.LineSegment
    part.Circle = _FakePart.Circle
    monkeypatch.setitem(sys.modules, "Part", part)
    sketcher = types.ModuleType("Sketcher")
    monkeypatch.setitem(sys.modules, "Sketcher", sketcher)


def test_create_sketch_creates_empty_sketch_on_xy_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_create_sketch(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
        }
    )

    assert result["ok"] is True
    assert result["op"] == "create_sketch"
    assert result["result"]["document_id"] == "skdoc"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["type"] == "Sketcher::SketchObject"
    assert result["result"]["plane"] == "XY"
    assert result["result"]["edges"] == 0

    sketch = doc.objects["Sketch1"]
    assert isinstance(sketch, _FakeSketch)
    assert sketch.Geometry == []
    assert sketch.Constraints == []
    # Identity placement: sketch plane coincides with the document XY plane.
    assert sketch.Placement is not None
    assert sketch.Placement.Base.x == 0.0
    assert sketch.Placement.Base.y == 0.0
    assert sketch.Placement.Base.z == 0.0

    assert (tmp_path / "skdoc.fcstd").read_bytes() == b"fake fcstd"
    assert not (tmp_path / "skdoc.lock").exists()


def test_create_sketch_uniquifies_name_on_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    doc.objects["Sketch"] = _FakeSketch("Sketch")
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch"}
    )

    assert result["ok"] is True
    assert result["result"]["sketch_name"] == "Sketch_1"
    assert "Sketch_1" in doc.objects
    assert "Sketch" in doc.objects  # pre-existing object untouched


def test_create_sketch_rejects_empty_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "   "}
    )

    assert result["ok"] is False
    assert "sketch_name" in result["error"]
    assert doc.objects == {}
    assert not (tmp_path / "skdoc.lock").exists()


def test_create_sketch_reopens_existing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "skdoc.fcstd"
    target.write_bytes(b"stored")
    fc = _FakeFreeCad(doc=_FakeDoc())
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Sketcher", types.ModuleType("Sketcher"))

    result = runner._op_create_sketch({"document_id": "skdoc", "workspace": str(tmp_path)})

    assert result["ok"] is True
    assert fc.open_calls == [str(target)]
    assert fc.new_calls == []


def test_sketch_add_rectangle_creates_four_connected_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_add_rectangle(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "x": 1.0,
            "y": 2.0,
            "width": 4.0,
            "height": 3.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_add_rectangle"
    assert result["result"]["document_id"] == "skdoc"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["x"] == 1.0
    assert result["result"]["y"] == 2.0
    assert result["result"]["width"] == 4.0
    assert result["result"]["height"] == 3.0
    assert result["result"]["edges_added"] == 4
    assert result["result"]["total_edges"] == 4

    sketch = doc.objects["Sketch1"]
    assert len(sketch.Geometry) == 4
    segments = sketch.Geometry
    assert all(isinstance(seg, _FakeSegment) for seg in segments)

    # Corners: counter-clockwise from lower-left, all on Z = 0.
    expected = [
        (1.0, 2.0, 0.0, 5.0, 2.0, 0.0),
        (5.0, 2.0, 0.0, 5.0, 5.0, 0.0),
        (5.0, 5.0, 0.0, 1.0, 5.0, 0.0),
        (1.0, 5.0, 0.0, 1.0, 2.0, 0.0),
    ]
    for seg, (sx, sy, sz, ex, ey, ez) in zip(segments, expected):
        assert seg.start.x == sx and seg.start.y == sy and seg.start.z == sz
        assert seg.end.x == ex and seg.end.y == ey and seg.end.z == ez

    # Edges are connected: end of each segment is the start of the next,
    # and the last segment closes back to the first.
    for i in range(4):
        assert segments[i].end is segments[(i + 1) % 4].start

    assert (tmp_path / "skdoc.fcstd").read_bytes() == b"fake fcstd"
    assert not (tmp_path / "skdoc.lock").exists()


def test_sketch_add_circle_creates_circle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_add_circle(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "center_x": 3.0,
            "center_y": 4.0,
            "radius": 2.5,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_add_circle"
    assert result["result"]["center_x"] == 3.0
    assert result["result"]["center_y"] == 4.0
    assert result["result"]["radius"] == 2.5
    assert result["result"]["edges_added"] == 1
    assert result["result"]["total_edges"] == 1

    sketch = doc.objects["Sketch1"]
    assert len(sketch.Geometry) == 1
    circle = sketch.Geometry[0]
    assert isinstance(circle, _FakeCircle)
    assert circle.center.x == 3.0
    assert circle.center.y == 4.0
    assert circle.center.z == 0.0
    assert circle.radius == 2.5


def test_multiple_geometries_in_same_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    rect = runner._op_sketch_add_rectangle(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        }
    )
    assert rect["ok"] is True
    assert rect["result"]["total_edges"] == 4

    circ = runner._op_sketch_add_circle(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "center_x": 1.0,
            "center_y": 1.0,
            "radius": 0.5,
        }
    )
    assert circ["ok"] is True
    assert circ["result"]["total_edges"] == 5

    sketch = doc.objects["Sketch1"]
    assert len(sketch.Geometry) == 5


def test_sketch_add_rectangle_rejects_invalid_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    base = {
        "document_id": "skdoc",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "x": 0.0,
        "y": 0.0,
        "width": 2.0,
        "height": 2.0,
    }
    for bad in (
        {**base, "width": 0},
        {**base, "height": -1.5},
        {**base, "width": float("nan")},
        {**base, "x": float("inf")},
        {**base, "y": float("-inf")},
    ):
        result = runner._op_sketch_add_rectangle(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_add_rectangle"
        assert result["error"]

    # Geometry must not have been added on failed attempts.
    assert len(doc.objects["Sketch1"].Geometry) == 0


def test_sketch_add_circle_rejects_invalid_radius(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    base = {
        "document_id": "skdoc",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "center_x": 0.0,
        "center_y": 0.0,
        "radius": 1.0,
    }
    for bad in (
        {**base, "radius": 0},
        {**base, "radius": -2.0},
        {**base, "center_x": float("nan")},
        {**base, "radius": float("inf")},
    ):
        result = runner._op_sketch_add_circle(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_add_circle"
        assert result["error"]

    assert len(doc.objects["Sketch1"].Geometry) == 0


def test_sketch_add_geometry_requires_existing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    for op, params in (
        (
            runner.OP_SKETCH_ADD_RECTANGLE,
            {
                "document_id": "skdoc",
                "workspace": str(tmp_path),
                "sketch_name": "Missing",
                "x": 0.0,
                "y": 0.0,
                "width": 2.0,
                "height": 2.0,
            },
        ),
        (
            runner.OP_SKETCH_ADD_CIRCLE,
            {
                "document_id": "skdoc",
                "workspace": str(tmp_path),
                "sketch_name": "Missing",
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 1.0,
            },
        ),
    ):
        if op == runner.OP_SKETCH_ADD_RECTANGLE:
            result = runner._op_sketch_add_rectangle(params)
        else:
            result = runner._op_sketch_add_circle(params)
        assert result["ok"] is False
        assert "not found" in result["error"]


def test_sketch_add_geometry_rejects_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document(
        {"document_id": "skdoc", "workspace": str(tmp_path)}
    )
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_add_rectangle(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_add_rectangle"
    assert "Sketcher::SketchObject" in result["error"]

    result = runner._op_sketch_add_circle(
        {
            "document_id": "skdoc",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )
    assert result["ok"] is False
    assert "Sketcher::SketchObject" in result["error"]
    assert len(doc.objects["Box1"].__dict__) > 0  # box object left untouched


def test_handle_request_dispatches_sketch_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    response = runner.handle_request(
        {
            "op": runner.OP_CREATE_SKETCH,
            "params": {"document_id": "skdoc", "workspace": str(tmp_path), "sketch_name": "Sketch1"},
        }
    )
    assert response["ok"] is True
    assert response["op"] == "create_sketch"
    assert "result" in response
    assert response["result"]["type"] == "Sketcher::SketchObject"

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_ADD_RECTANGLE,
            "params": {
                "document_id": "skdoc",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "x": 0.0,
                "y": 0.0,
                "width": 3.0,
                "height": 3.0,
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "sketch_add_rectangle"
    assert response["result"]["edges_added"] == 4

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_ADD_CIRCLE,
            "params": {
                "document_id": "skdoc",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "center_x": 1.0,
                "center_y": 1.0,
                "radius": 1.0,
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "sketch_add_circle"
    assert response["result"]["edges_added"] == 1
    assert response["result"]["total_edges"] == 5
