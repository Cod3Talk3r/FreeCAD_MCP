"""Tests for the FreeCAD-side runner's envelope and dispatch logic.

``runner`` is importable on the host interpreter by design: it only imports
``FreeCAD`` lazily inside handlers, so dispatch/envelope behaviour can be tested
without FreeCAD.
"""

from __future__ import annotations

import json
import math
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
    def __init__(self, axis: object | None = None, angle: float = 0.0) -> None:
        self.Axis = axis
        self.Angle = angle


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


class _FakeEllipse:
    def __init__(self, center: object, major_radius: float, minor_radius: float) -> None:
        self.center = center
        self.major_radius = major_radius
        self.minor_radius = minor_radius


# --- FreeCAD-style geometry fakes for inspect_sketch tests -----------------

class _FakePoint:
    def __init__(self, x: float, y: float, z: float = 0.0) -> None:
        self.x = x
        self.y = y
        self.z = z


class LineSegment:
    def __init__(self, start: _FakePoint, end: _FakePoint, construction: bool = False) -> None:
        self.StartPoint = start
        self.EndPoint = end
        self.start = start
        self.end = end
        self.Construction = construction


class Circle:
    def __init__(self, center: _FakePoint, radius: float, construction: bool = False) -> None:
        self.Center = center
        self.Radius = radius
        self.center = center
        self.radius = radius
        self.Construction = construction


class Ellipse:
    def __init__(
        self,
        center: _FakePoint,
        major_radius: float,
        minor_radius: float,
        major_dir: _FakePoint | None = None,
        minor_dir: _FakePoint | None = None,
        construction: bool = False,
    ) -> None:
        self.Center = center
        self.MajorRadius = major_radius
        self.MinorRadius = minor_radius
        self.center = center
        self.major_radius = major_radius
        self.minor_radius = minor_radius
        self.AngleXU = 0.0
        self.XAxis = major_dir or _FakePoint(1, 0, 0)
        self.YAxis = minor_dir or _FakePoint(0, 1, 0)
        # Kept for inspect_sketch fake coverage; real FreeCAD 1.1.4 does not
        # expose these attributes on Part.Ellipse.
        self.MajorDirection = self.XAxis
        self.MinorDirection = self.YAxis
        self.Construction = construction

    def rotate(self, placement: _FakePlacement) -> None:
        """Mirror Part.Ellipse.rotate(): Placement angle is in degrees."""
        self.Center = placement.Base
        self.center = placement.Base
        self.AngleXU += math.radians(placement.Rotation.Angle)
        cos_a = math.cos(self.AngleXU)
        sin_a = math.sin(self.AngleXU)
        self.XAxis = _FakePoint(cos_a, sin_a, 0.0)
        self.YAxis = _FakePoint(-sin_a, cos_a, 0.0)
        self.MajorDirection = self.XAxis
        self.MinorDirection = self.YAxis


class _FakeUnknownGeom:
    def __init__(self, construction: bool = False) -> None:
        self.Construction = construction


# Backward-compatible aliases for existing tests that reference _FakeSegment etc.
_FakeSegment = LineSegment
_FakeCircle = Circle
_FakeEllipse = Ellipse


class _FakePart:
    """Minimal stand-in for FreeCAD's Part module."""

    @staticmethod
    def LineSegment(start: object, end: object) -> LineSegment:
        return LineSegment(start, end)

    @staticmethod
    def Circle(center: object, normal: object, radius: float) -> Circle:
        return Circle(center, radius)

    @staticmethod
    def Ellipse(center: object, major_radius: float, minor_radius: float) -> _FakeEllipse:
        return _FakeEllipse(center, major_radius, minor_radius)


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

    def delGeometry(self, index: int) -> None:
        del self.Geometry[index]


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
    part.Ellipse = _FakePart.Ellipse
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


# ---------------------------------------------------------------------------
# Milestone 9-B: Sketch triangle and ellipse (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_sketch_add_triangle_creates_three_connected_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "tri", "workspace": str(tmp_path), "sketch_name": "Tri1"}
    )

    result = runner._op_sketch_add_triangle(
        {
            "document_id": "tri",
            "workspace": str(tmp_path),
            "sketch_name": "Tri1",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 4.0,
            "y2": 0.0,
            "x3": 2.0,
            "y3": 3.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_add_triangle"
    assert result["result"]["document_id"] == "tri"
    assert result["result"]["sketch_name"] == "Tri1"
    assert result["result"]["x1"] == 0.0
    assert result["result"]["y1"] == 0.0
    assert result["result"]["x2"] == 4.0
    assert result["result"]["y2"] == 0.0
    assert result["result"]["x3"] == 2.0
    assert result["result"]["y3"] == 3.0
    assert result["result"]["edges_added"] == 3
    assert result["result"]["total_edges"] == 3

    sketch = doc.objects["Tri1"]
    assert len(sketch.Geometry) == 3
    segments = sketch.Geometry
    assert all(isinstance(seg, _FakeSegment) for seg in segments)

    # Vertices preserved in order, all on Z = 0.
    expected = [
        (0.0, 0.0, 0.0, 4.0, 0.0, 0.0),
        (4.0, 0.0, 0.0, 2.0, 3.0, 0.0),
        (2.0, 3.0, 0.0, 0.0, 0.0, 0.0),
    ]
    for seg, (sx, sy, sz, ex, ey, ez) in zip(segments, expected):
        assert seg.start.x == sx and seg.start.y == sy and seg.start.z == sz
        assert seg.end.x == ex and seg.end.y == ey and seg.end.z == ez

    # Closed: end of each segment is the start of the next, last closes to first.
    for i in range(3):
        assert segments[i].end is segments[(i + 1) % 3].start

    assert (tmp_path / "tri.fcstd").read_bytes() == b"fake fcstd"
    assert not (tmp_path / "tri.lock").exists()


def test_sketch_add_triangle_rejects_collinear_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "tri", "workspace": str(tmp_path), "sketch_name": "Tri1"}
    )

    base = {
        "document_id": "tri",
        "workspace": str(tmp_path),
        "sketch_name": "Tri1",
        "x1": 0.0,
        "y1": 0.0,
        "x2": 2.0,
        "y2": 2.0,
        "x3": 4.0,
        "y3": 4.0,
    }
    result = runner._op_sketch_add_triangle(base)
    assert result["ok"] is False
    assert result["op"] == "sketch_add_triangle"
    assert "collinear" in result["error"] or "degenerate" in result["error"]

    # Duplicate vertex (zero area) also rejected.
    dup = {**base, "x3": 2.0, "y3": 2.0}
    result = runner._op_sketch_add_triangle(dup)
    assert result["ok"] is False
    assert "collinear" in result["error"] or "degenerate" in result["error"]

    assert len(doc.objects["Tri1"].Geometry) == 0


def test_sketch_add_triangle_rejects_invalid_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "tri", "workspace": str(tmp_path), "sketch_name": "Tri1"}
    )

    base = {
        "document_id": "tri",
        "workspace": str(tmp_path),
        "sketch_name": "Tri1",
        "x1": 0.0,
        "y1": 0.0,
        "x2": 1.0,
        "y2": 0.0,
        "x3": 0.0,
        "y3": 1.0,
    }
    for bad in (
        {**base, "x1": float("nan")},
        {**base, "y2": float("inf")},
        {**base, "x3": float("-inf")},
    ):
        result = runner._op_sketch_add_triangle(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_add_triangle"
        assert result["error"]

    assert len(doc.objects["Tri1"].Geometry) == 0


def test_sketch_add_ellipse_creates_ellipse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "ell", "workspace": str(tmp_path), "sketch_name": "Ell1"}
    )

    result = runner._op_sketch_add_ellipse(
        {
            "document_id": "ell",
            "workspace": str(tmp_path),
            "sketch_name": "Ell1",
            "center_x": 3.0,
            "center_y": -2.0,
            "major_radius": 6.0,
            "minor_radius": 2.5,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_add_ellipse"
    assert result["result"]["document_id"] == "ell"
    assert result["result"]["sketch_name"] == "Ell1"
    assert result["result"]["center_x"] == 3.0
    assert result["result"]["center_y"] == -2.0
    assert result["result"]["major_radius"] == 6.0
    assert result["result"]["minor_radius"] == 2.5
    assert result["result"]["edges_added"] == 1
    assert result["result"]["total_edges"] == 1

    sketch = doc.objects["Ell1"]
    assert len(sketch.Geometry) == 1
    ellipse = sketch.Geometry[0]
    assert isinstance(ellipse, _FakeEllipse)
    assert ellipse.center.x == 3.0
    assert ellipse.center.y == -2.0
    assert ellipse.center.z == 0.0
    assert ellipse.major_radius == 6.0
    assert ellipse.minor_radius == 2.5
    # Part.Ellipse(center, major, minor) is axis-aligned: major along X,
    # minor along Y, centre on Z=0.

    assert (tmp_path / "ell.fcstd").read_bytes() == b"fake fcstd"
    assert not (tmp_path / "ell.lock").exists()


def test_sketch_add_ellipse_rejects_invalid_radii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "ell", "workspace": str(tmp_path), "sketch_name": "Ell1"}
    )

    base = {
        "document_id": "ell",
        "workspace": str(tmp_path),
        "sketch_name": "Ell1",
        "center_x": 0.0,
        "center_y": 0.0,
        "major_radius": 4.0,
        "minor_radius": 2.0,
    }
    for bad in (
        {**base, "major_radius": 0},
        {**base, "minor_radius": -1.5},
        {**base, "center_x": float("nan")},
        {**base, "major_radius": float("inf")},
    ):
        result = runner._op_sketch_add_ellipse(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_add_ellipse"
        assert result["error"]

    assert len(doc.objects["Ell1"].Geometry) == 0


def test_sketch_add_triangle_and_ellipse_mixed_in_same_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mix", "workspace": str(tmp_path), "sketch_name": "Sk1"}
    )

    tri = runner._op_sketch_add_triangle(
        {
            "document_id": "mix",
            "workspace": str(tmp_path),
            "sketch_name": "Sk1",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 2.0,
            "y2": 0.0,
            "x3": 1.0,
            "y3": 2.0,
        }
    )
    assert tri["ok"] is True
    assert tri["result"]["total_edges"] == 3

    ell = runner._op_sketch_add_ellipse(
        {
            "document_id": "mix",
            "workspace": str(tmp_path),
            "sketch_name": "Sk1",
            "center_x": 1.0,
            "center_y": 1.0,
            "major_radius": 1.0,
            "minor_radius": 0.5,
        }
    )
    assert ell["ok"] is True
    assert ell["result"]["total_edges"] == 4

    sketch = doc.objects["Sk1"]
    assert len(sketch.Geometry) == 4
    assert isinstance(sketch.Geometry[0], _FakeSegment)
    assert isinstance(sketch.Geometry[3], _FakeEllipse)


def test_sketch_add_triangle_requires_existing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_add_triangle(
        {
            "document_id": "tri",
            "workspace": str(tmp_path),
            "sketch_name": "Missing",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 1.0,
            "y2": 0.0,
            "x3": 0.0,
            "y3": 1.0,
        }
    )
    assert result["ok"] is False
    assert "not found" in result["error"]

    result = runner._op_sketch_add_ellipse(
        {
            "document_id": "tri",
            "workspace": str(tmp_path),
            "sketch_name": "Missing",
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_sketch_add_triangle_and_ellipse_reject_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "tri", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_add_triangle(
        {
            "document_id": "tri",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 1.0,
            "y2": 0.0,
            "x3": 0.0,
            "y3": 1.0,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_add_triangle"
    assert "Sketcher::SketchObject" in result["error"]

    result = runner._op_sketch_add_ellipse(
        {
            "document_id": "tri",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_add_ellipse"
    assert "Sketcher::SketchObject" in result["error"]


def test_handle_request_dispatches_triangle_and_ellipse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "tri", "workspace": str(tmp_path), "sketch_name": "Tri1"}
    )

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_ADD_TRIANGLE,
            "params": {
                "document_id": "tri",
                "workspace": str(tmp_path),
                "sketch_name": "Tri1",
                "x1": 0.0,
                "y1": 0.0,
                "x2": 2.0,
                "y2": 0.0,
                "x3": 1.0,
                "y3": 2.0,
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "sketch_add_triangle"
    assert response["result"]["edges_added"] == 3

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_ADD_ELLIPSE,
            "params": {
                "document_id": "tri",
                "workspace": str(tmp_path),
                "sketch_name": "Tri1",
                "center_x": 1.0,
                "center_y": 1.0,
                "major_radius": 2.0,
                "minor_radius": 1.0,
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "sketch_add_ellipse"
    assert response["result"]["edges_added"] == 1
    assert response["result"]["total_edges"] == 4


# ---------------------------------------------------------------------------
# Milestone 9-C: Sketch regular and custom polygon (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_sketch_add_regular_polygon_hexagon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "reg", "workspace": str(tmp_path), "sketch_name": "Reg1"}
    )

    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "reg",
            "workspace": str(tmp_path),
            "sketch_name": "Reg1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 10.0,
            "sides": 6,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_add_regular_polygon"
    assert result["result"]["edges_added"] == 6
    assert result["result"]["total_edges"] == 6
    assert result["result"]["sides"] == 6
    assert result["result"]["radius"] == 10.0

    sketch = doc.objects["Reg1"]
    assert len(sketch.Geometry) == 6
    segments = sketch.Geometry
    assert all(isinstance(seg, _FakeSegment) for seg in segments)

    # Closed: end of each segment is start of next, last closes to first.
    for i in range(6):
        assert segments[i].end is segments[(i + 1) % 6].start

    # All vertices lie on the circumcircle (radius 10) at Z = 0.
    starts = [seg.start for seg in segments]
    for v in starts:
        assert v.z == 0.0
        dist = math.sqrt(v.x * v.x + v.y * v.y)
        assert dist == pytest.approx(10.0, rel=1e-9)

    # Vertices are spaced 60 degrees apart.
    assert starts[0].x == pytest.approx(10.0) and starts[0].y == pytest.approx(0.0)
    assert starts[1].x == pytest.approx(5.0) and starts[1].y == pytest.approx(8.6602540378, rel=1e-6)


def test_sketch_add_regular_polygon_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "rot", "workspace": str(tmp_path), "sketch_name": "Reg1"}
    )

    # 90-degree rotation: first vertex at (0, radius).
    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "rot",
            "workspace": str(tmp_path),
            "sketch_name": "Reg1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
            "rotation_deg": 90.0,
        }
    )
    assert result["ok"] is True
    sketch = doc.objects["Reg1"]
    first = sketch.Geometry[0].start
    assert first.x == pytest.approx(0.0)
    assert first.y == pytest.approx(5.0)
    assert first.z == 0.0


def test_sketch_add_regular_polygon_triangle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "trireg", "workspace": str(tmp_path), "sketch_name": "Reg1"}
    )

    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "trireg",
            "workspace": str(tmp_path),
            "sketch_name": "Reg1",
            "center_x": 1.0,
            "center_y": 2.0,
            "radius": 3.0,
            "sides": 3,
        }
    )
    assert result["ok"] is True
    assert result["result"]["edges_added"] == 3

    sketch = doc.objects["Reg1"]
    assert len(sketch.Geometry) == 3
    # Circumradius: distance from center to each vertex should be 3.
    for seg in sketch.Geometry:
        dist = math.sqrt(
            (seg.start.x - 1.0) ** 2 + (seg.start.y - 2.0) ** 2
        )
        assert dist == pytest.approx(3.0, rel=1e-9)
        assert seg.start.z == 0.0


def test_sketch_add_regular_polygon_min_max_sides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "lim", "workspace": str(tmp_path), "sketch_name": "Reg1"}
    )

    # Minimum: 3 sides is allowed.
    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "lim",
            "workspace": str(tmp_path),
            "sketch_name": "Reg1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
            "sides": 3,
        }
    )
    assert result["ok"] is True

    # Below minimum rejected.
    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "lim",
            "workspace": str(tmp_path),
            "sketch_name": "Reg1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
            "sides": 2,
        }
    )
    assert result["ok"] is False
    assert "at least 3" in result["error"]

    # Above max rejected.
    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "lim",
            "workspace": str(tmp_path),
            "sketch_name": "Reg1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
            "sides": runner.MAX_POLYGON_SIDES + 1,
        }
    )
    assert result["ok"] is False
    assert "must not exceed" in result["error"]

    assert len(doc.objects["Reg1"].Geometry) == 3


def test_sketch_add_regular_polygon_invalid_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "badreg", "workspace": str(tmp_path), "sketch_name": "Reg1"}
    )

    base = {
        "document_id": "badreg",
        "workspace": str(tmp_path),
        "sketch_name": "Reg1",
        "center_x": 0.0,
        "center_y": 0.0,
        "radius": 5.0,
        "sides": 6,
    }
    for bad in (
        {**base, "center_x": float("nan")},
        {**base, "radius": float("inf")},
        {**base, "rotation_deg": float("-inf")},
        {**base, "radius": 0},
        {**base, "radius": -2.0},
        {**base, "sides": 0},
        {**base, "sides": -1},
        {**base, "sides": "six"},
    ):
        result = runner._op_sketch_add_regular_polygon(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_add_regular_polygon"
        assert result["error"]

    assert len(doc.objects["Reg1"].Geometry) == 0


def test_sketch_add_polygon_custom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "cus", "workspace": str(tmp_path), "sketch_name": "Poly1"}
    )

    result = runner._op_sketch_add_polygon(
        {
            "document_id": "cus",
            "workspace": str(tmp_path),
            "sketch_name": "Poly1",
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 2.0, "y": 1.0},
                {"x": 0.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_add_polygon"
    assert result["result"]["edges_added"] == 4
    assert result["result"]["total_edges"] == 4

    sketch = doc.objects["Poly1"]
    assert len(sketch.Geometry) == 4
    segments = sketch.Geometry
    assert all(isinstance(seg, _FakeSegment) for seg in segments)

    # Vertex order preserved, all on Z=0.
    expected = [
        (0.0, 0.0, 2.0, 0.0),
        (2.0, 0.0, 2.0, 1.0),
        (2.0, 1.0, 0.0, 1.0),
        (0.0, 1.0, 0.0, 0.0),
    ]
    for seg, (sx, sy, ex, ey) in zip(segments, expected):
        assert seg.start.x == sx and seg.start.y == sy and seg.start.z == 0.0
        assert seg.end.x == ex and seg.end.y == ey and seg.end.z == 0.0

    # Closed: last segment end == first segment start.
    assert segments[3].end is segments[0].start


def test_sketch_add_polygon_automatic_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "clo", "workspace": str(tmp_path), "sketch_name": "Poly1"}
    )

    # Triangle: 3 vertices, 3 edges (closure is automatic).
    result = runner._op_sketch_add_polygon(
        {
            "document_id": "clo",
            "workspace": str(tmp_path),
            "sketch_name": "Poly1",
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 1.0, "y": 0.0},
                {"x": 0.0, "y": 1.0},
            ],
        }
    )
    assert result["ok"] is True
    assert result["result"]["edges_added"] == 3
    assert len(doc.objects["Poly1"].Geometry) == 3


def test_sketch_add_polygon_rejects_invalid_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "badcus", "workspace": str(tmp_path), "sketch_name": "Poly1"}
    )

    base = {
        "document_id": "badcus",
        "workspace": str(tmp_path),
        "sketch_name": "Poly1",
        "vertices": [
            {"x": 0.0, "y": 0.0},
            {"x": 1.0, "y": 0.0},
            {"x": 0.0, "y": 1.0},
        ],
    }

    # Too few vertices.
    result = runner._op_sketch_add_polygon({**base, "vertices": [{"x": 0, "y": 0}]})
    assert result["ok"] is False
    assert "at least 3" in result["error"]

    # Non-dict vertex.
    result = runner._op_sketch_add_polygon(
        {**base, "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, "notadict"]}
    )
    assert result["ok"] is False
    assert "object with 'x' and 'y'" in result["error"]

    # NaN coordinate.
    result = runner._op_sketch_add_polygon(
        {**base, "vertices": [{"x": 0, "y": 0}, {"x": float("nan"), "y": 0}, {"x": 0, "y": 1}]}
    )
    assert result["ok"] is False
    assert "finite" in result["error"]

    # Consecutive duplicate.
    result = runner._op_sketch_add_polygon(
        {**base, "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}]}
    )
    assert result["ok"] is False
    assert "consecutive duplicate" in result["error"]

    # Repeated first vertex at end.
    result = runner._op_sketch_add_polygon(
        {**base, "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}, {"x": 0, "y": 0}]}
    )
    assert result["ok"] is False
    assert "duplicates the first" in result["error"]

    # Collinear / zero area.
    result = runner._op_sketch_add_polygon(
        {**base, "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}]}
    )
    assert result["ok"] is False
    assert "zero area" in result["error"]

    assert len(doc.objects["Poly1"].Geometry) == 0


def test_sketch_add_polygon_mixed_with_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixpoly", "workspace": str(tmp_path), "sketch_name": "Sk1"}
    )

    rect = runner._op_sketch_add_rectangle(
        {
            "document_id": "mixpoly",
            "workspace": str(tmp_path),
            "sketch_name": "Sk1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        }
    )
    assert rect["ok"] is True
    assert rect["result"]["total_edges"] == 4

    poly = runner._op_sketch_add_polygon(
        {
            "document_id": "mixpoly",
            "workspace": str(tmp_path),
            "sketch_name": "Sk1",
            "vertices": [
                {"x": 3.0, "y": 3.0},
                {"x": 4.0, "y": 3.0},
                {"x": 3.5, "y": 4.0},
            ],
        }
    )
    assert poly["ok"] is True
    assert poly["result"]["total_edges"] == 7

    sketch = doc.objects["Sk1"]
    assert len(sketch.Geometry) == 7


def test_sketch_add_polygon_requires_existing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_add_polygon(
        {
            "document_id": "cus",
            "workspace": str(tmp_path),
            "sketch_name": "Missing",
            "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
        }
    )
    assert result["ok"] is False
    assert "not found" in result["error"]

    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "cus",
            "workspace": str(tmp_path),
            "sketch_name": "Missing",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
            "sides": 5,
        }
    )
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_sketch_add_polygon_rejects_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "wtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_add_polygon(
        {
            "document_id": "wtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_add_polygon"
    assert "Sketcher::SketchObject" in result["error"]

    result = runner._op_sketch_add_regular_polygon(
        {
            "document_id": "wtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
            "sides": 5,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_add_regular_polygon"
    assert "Sketcher::SketchObject" in result["error"]


def test_handle_request_dispatches_polygon_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "disp", "workspace": str(tmp_path), "sketch_name": "Sk1"}
    )

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_ADD_REGULAR_POLYGON,
            "params": {
                "document_id": "disp",
                "workspace": str(tmp_path),
                "sketch_name": "Sk1",
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 2.0,
                "sides": 5,
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "sketch_add_regular_polygon"
    assert response["result"]["edges_added"] == 5

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_ADD_POLYGON,
            "params": {
                "document_id": "disp",
                "workspace": str(tmp_path),
                "sketch_name": "Sk1",
                "vertices": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 1.0, "y": 0.0},
                    {"x": 0.0, "y": 1.0},
                ],
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "sketch_add_polygon"
    assert response["result"]["edges_added"] == 3
    assert response["result"]["total_edges"] == 8


# ---------------------------------------------------------------------------
# Milestone 9-D1: Inspect sketch (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_inspect_sketch_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "empty", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_inspect_sketch(
        {"document_id": "empty", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    assert result["ok"] is True
    assert result["op"] == "inspect_sketch"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_count"] == 0
    assert result["result"]["constraint_count"] == 0
    assert result["result"]["geometries"] == []
    assert result["result"]["type"] == "Sketcher::SketchObject"


def test_inspect_sketch_all_supported_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "insp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]

    # Add line segment, circle, ellipse directly to Geometry list.
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(2, 0, 0)))
    sketch.Geometry.append(Circle(_FakePoint(1, 1, 0), 1.5))
    sketch.Geometry.append(
        Ellipse(_FakePoint(3, 3, 0), 4.0, 2.0, _FakePoint(1, 0, 0), _FakePoint(0, 1, 0))
    )

    result = runner._op_inspect_sketch(
        {"document_id": "insp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 3
    assert result["result"]["sketch_name"] == "Sketch1"

    geometries = result["result"]["geometries"]
    assert len(geometries) == 3

    # Line segment.
    line = geometries[0]
    assert line["index"] == 0
    assert line["type"] == "LineSegment"
    assert line["start_point"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert line["end_point"] == {"x": 2.0, "y": 0.0, "z": 0.0}

    # Circle.
    circle = geometries[1]
    assert circle["index"] == 1
    assert circle["type"] == "Circle"
    assert circle["center"] == {"x": 1.0, "y": 1.0, "z": 0.0}
    assert circle["radius"] == 1.5

    # Ellipse.
    ellipse = geometries[2]
    assert ellipse["index"] == 2
    assert ellipse["type"] == "Ellipse"
    assert ellipse["center"] == {"x": 3.0, "y": 3.0, "z": 0.0}
    assert ellipse["major_radius"] == 4.0
    assert ellipse["minor_radius"] == 2.0
    assert ellipse["major_direction"] == {"x": 1.0, "y": 0.0, "z": 0.0}
    assert ellipse["minor_direction"] == {"x": 0.0, "y": 1.0, "z": 0.0}


def test_inspect_sketch_construction_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "constr", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]

    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0), construction=True))
    sketch.Geometry.append(LineSegment(_FakePoint(1, 0, 0), _FakePoint(1, 1, 0)))

    result = runner._op_inspect_sketch(
        {"document_id": "constr", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    assert result["ok"] is True
    geometries = result["result"]["geometries"]
    assert geometries[0]["construction"] is True
    assert geometries[1]["construction"] is False


def test_inspect_sketch_unknown_geometry_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "unk", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]

    sketch.Geometry.append(_FakeUnknownGeom())

    result = runner._op_inspect_sketch(
        {"document_id": "unk", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 1
    geometries = result["result"]["geometries"]
    assert geometries[0]["type"] == "_FakeUnknownGeom"
    assert geometries[0]["index"] == 0


def test_inspect_sketch_placement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "place", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Placement = _FakePlacement(_FakeVector(10.0, 20.0, 0.0), _FakeRotation())

    result = runner._op_inspect_sketch(
        {"document_id": "place", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    assert result["ok"] is True
    assert result["result"]["placement"] == {"x": 10.0, "y": 20.0, "z": 0.0}


def test_inspect_sketch_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_inspect_sketch(
        {"document_id": "missing", "workspace": str(tmp_path), "sketch_name": "NoSuchSketch"}
    )
    assert result["ok"] is False
    assert result["op"] == "inspect_sketch"
    assert "not found" in result["error"]


def test_inspect_sketch_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "wtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_inspect_sketch(
        {"document_id": "wtype", "workspace": str(tmp_path), "sketch_name": "Box1"}
    )
    assert result["ok"] is False
    assert result["op"] == "inspect_sketch"
    assert "Sketcher::SketchObject" in result["error"]


def test_handle_request_dispatches_inspect_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "disp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    response = runner.handle_request(
        {
            "op": runner.OP_INSPECT_SKETCH,
            "params": {
                "document_id": "disp",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
            },
        }
    )
    assert response["ok"] is True
    assert response["op"] == "inspect_sketch"
    assert response["result"]["geometry_count"] == 0
    assert response["result"]["sketch_name"] == "Sketch1"


# ---------------------------------------------------------------------------
# Milestone 9-D2-A: Delete sketch geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_sketch_delete_geometry_deletes_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "del", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(1, 0, 0), _FakePoint(1, 1, 0)))
    sketch.Geometry.append(Circle(_FakePoint(2, 2, 0), 1.0))

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "del",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_delete_geometry"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["deleted_index"] == 1
    assert result["result"]["geometry_count"] == 2

    # Remaining geometries preserve order and properties.
    assert len(sketch.Geometry) == 2
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert isinstance(sketch.Geometry[1], Circle)
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert sketch.Geometry[1].Center.x == 2.0


def test_sketch_delete_geometry_deletes_first_and_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "delend", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(1, 0, 0), _FakePoint(1, 1, 0)))

    # Delete first (index 0).
    result = runner._op_sketch_delete_geometry(
        {"document_id": "delend", "workspace": str(tmp_path), "sketch_name": "Sketch1", "geometry_index": 0}
    )
    assert result["ok"] is True
    assert result["result"]["deleted_index"] == 0
    assert result["result"]["geometry_count"] == 1
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert sketch.Geometry[0].StartPoint.x == 1.0

    # Delete last (index 0 again, since only one remains).
    result = runner._op_sketch_delete_geometry(
        {"document_id": "delend", "workspace": str(tmp_path), "sketch_name": "Sketch1", "geometry_index": 0}
    )
    assert result["ok"] is True
    assert result["result"]["deleted_index"] == 0
    assert result["result"]["geometry_count"] == 0
    assert len(sketch.Geometry) == 0


def test_sketch_delete_geometry_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "delneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "delneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_delete_geometry"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1  # nothing deleted


def test_sketch_delete_geometry_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "deloor", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "deloor",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 5,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_delete_geometry"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1  # nothing deleted


def test_sketch_delete_geometry_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "delbool", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "delbool",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_delete_geometry"
    assert "boolean" in result["error"].lower()


def test_sketch_delete_geometry_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "delfloat", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "delfloat",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_delete_geometry"
    assert "integer" in result["error"].lower()


def test_sketch_delete_geometry_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "delmiss",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_delete_geometry"
    assert "not found" in result["error"]


def test_sketch_delete_geometry_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "delwtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_delete_geometry(
        {
            "document_id": "delwtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_delete_geometry"
    assert "Sketcher::SketchObject" in result["error"]


def test_handle_request_dispatches_sketch_delete_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "deldel", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_DELETE_GEOMETRY,
            "params": {
                "document_id": "deldel",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_delete_geometry"
    assert response["result"]["deleted_index"] == 0
    assert response["result"]["geometry_count"] == 0


# ---------------------------------------------------------------------------
# Milestone 9-D2-B1: Edit sketch line geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_sketch_edit_line_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_line(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "start_x": 2.0,
            "start_y": 3.0,
            "end_x": 5.0,
            "end_y": 7.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_line"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["start_x"] == 2.0
    assert result["result"]["start_y"] == 3.0
    assert result["result"]["end_x"] == 5.0
    assert result["result"]["end_y"] == 7.0
    assert result["result"]["geometry_count"] == 1

    # Line was modified in place.
    line = sketch.Geometry[0]
    assert line.StartPoint.x == 2.0
    assert line.StartPoint.y == 3.0
    assert line.EndPoint.x == 5.0
    assert line.EndPoint.y == 7.0


def test_sketch_edit_line_mixed_sketch_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(Circle(_FakePoint(2, 2, 0), 1.5))
    sketch.Geometry.append(LineSegment(_FakePoint(4, 4, 0), _FakePoint(5, 4, 0)))

    result = runner._op_sketch_edit_line(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 2,
            "start_x": 9.0,
            "start_y": 9.0,
            "end_x": 10.0,
            "end_y": 9.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 3

    # Other geometries preserved.
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert isinstance(sketch.Geometry[1], Circle)
    assert sketch.Geometry[1].Radius == 1.5

    # Edited line updated.
    edited = sketch.Geometry[2]
    assert isinstance(edited, LineSegment)
    assert edited.StartPoint.x == 9.0
    assert edited.StartPoint.y == 9.0
    assert edited.EndPoint.x == 10.0
    assert edited.EndPoint.y == 9.0


def test_sketch_edit_line_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "out of range" in result["error"]
    # Nothing changed.
    assert len(sketch.Geometry) == 1


def test_sketch_edit_line_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editoor", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editoor",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 5,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_line_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editbool", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editbool",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "boolean" in result["error"].lower()


def test_sketch_edit_line_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editfloat", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editfloat",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "integer" in result["error"].lower()


def test_sketch_edit_line_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editmiss",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "not found" in result["error"]


def test_sketch_edit_line_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "editwtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editwtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_line_rejects_non_line_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnonline", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editnonline",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 1.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "LineSegment" in result["error"]
    assert "Circle" in result["error"]


def test_sketch_edit_line_rejects_non_finite_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnan", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    base = {
        "document_id": "editnan",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "geometry_index": 0,
        "start_x": 0.0,
        "start_y": 0.0,
        "end_x": 1.0,
        "end_y": 0.0,
    }
    for bad in (
        {**base, "start_x": float("nan")},
        {**base, "start_y": float("inf")},
        {**base, "end_x": float("-inf")},
        {**base, "end_y": float("nan")},
    ):
        result = runner._op_sketch_edit_line(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_edit_line"
        assert "finite" in result["error"]

    assert len(sketch.Geometry) == 1


def test_sketch_edit_line_rejects_zero_length_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editzero", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editzero",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "start_x": 0.0,
            "start_y": 0.0,
            "end_x": 0.0,
            "end_y": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "degenerate" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_line_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editcon", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    # Add a constraint that references geometry index 0.
    class _FakeConstraint:
        First = 0
        FirstPos = 1
        Second = -1
        SecondPos = 0
        Third = -1
        ThirdPos = 0

    sketch.Constraints.append(_FakeConstraint())

    result = runner._op_sketch_edit_line(
        {
            "document_id": "editcon",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "start_x": 2.0,
            "start_y": 3.0,
            "end_x": 5.0,
            "end_y": 7.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_line"
    assert "constraint" in result["error"].lower()
    assert len(sketch.Geometry) == 1  # unchanged


def test_handle_request_dispatches_sketch_edit_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editdisp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_EDIT_LINE,
            "params": {
                "document_id": "editdisp",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "start_x": 3.0,
                "start_y": 4.0,
                "end_x": 6.0,
                "end_y": 8.0,
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_line"
    assert response["result"]["geometry_index"] == 0
    assert response["result"]["start_x"] == 3.0
    assert response["result"]["start_y"] == 4.0
    assert response["result"]["end_x"] == 6.0
    assert response["result"]["end_y"] == 8.0


# ---------------------------------------------------------------------------
# Milestone 9-D2-B2: Edit sketch circle geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_sketch_edit_circle_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(1.0, 1.0, 0), 2.5))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 10.0,
            "center_y": 20.0,
            "radius": 7.5,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_circle"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["center_x"] == 10.0
    assert result["result"]["center_y"] == 20.0
    assert result["result"]["radius"] == 7.5
    assert result["result"]["geometry_count"] == 1

    # Circle was modified in place.
    circle = sketch.Geometry[0]
    assert circle.Center.x == 10.0
    assert circle.Center.y == 20.0
    assert circle.Radius == 7.5


def test_sketch_edit_circle_mixed_sketch_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(Circle(_FakePoint(2, 2, 0), 1.5))
    sketch.Geometry.append(LineSegment(_FakePoint(4, 4, 0), _FakePoint(5, 4, 0)))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "center_x": 99.0,
            "center_y": 88.0,
            "radius": 3.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 3

    # Other geometries preserved.
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert isinstance(sketch.Geometry[2], LineSegment)
    assert sketch.Geometry[2].StartPoint.x == 4.0

    # Edited circle updated.
    edited = sketch.Geometry[1]
    assert isinstance(edited, Circle)
    assert edited.Center.x == 99.0
    assert edited.Center.y == 88.0
    assert edited.Radius == 3.0


def test_sketch_edit_circle_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_circle_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editoor", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editoor",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 5,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_circle_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editbool", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editbool",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "boolean" in result["error"].lower()


def test_sketch_edit_circle_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editfloat", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editfloat",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "integer" in result["error"].lower()


def test_sketch_edit_circle_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editmiss",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "not found" in result["error"]


def test_sketch_edit_circle_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "editwtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editwtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_circle_rejects_non_circle_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnoncircle", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editnoncircle",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "Circle" in result["error"]
    assert "LineSegment" in result["error"]


def test_sketch_edit_circle_rejects_non_finite_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnan", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    base = {
        "document_id": "editnan",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "geometry_index": 0,
        "center_x": 0.0,
        "center_y": 0.0,
        "radius": 1.0,
    }
    for bad in (
        {**base, "center_x": float("nan")},
        {**base, "center_y": float("inf")},
        {**base, "radius": float("-inf")},
        {**base, "center_x": float("nan")},
    ):
        result = runner._op_sketch_edit_circle(bad)
        assert result["ok"] is False, bad
        assert result["op"] == "sketch_edit_circle"
        assert "finite" in result["error"]

    assert len(sketch.Geometry) == 1


def test_sketch_edit_circle_rejects_zero_radius(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editzero", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editzero",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 0.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "positive" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_circle_rejects_negative_radius(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnegrad", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editnegrad",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": -5.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "positive" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_circle_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editcon", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    # Add a constraint that references geometry index 0.
    class _FakeConstraint:
        First = 0
        FirstPos = 1
        Second = -1
        SecondPos = 0
        Third = -1
        ThirdPos = 0

    sketch.Constraints.append(_FakeConstraint())

    result = runner._op_sketch_edit_circle(
        {
            "document_id": "editcon",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 10.0,
            "center_y": 20.0,
            "radius": 7.5,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_circle"
    assert "constraint" in result["error"].lower()
    assert len(sketch.Geometry) == 1  # unchanged


def test_handle_request_dispatches_sketch_edit_circle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editdisp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(1.0, 1.0, 0), 2.5))

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_EDIT_CIRCLE,
            "params": {
                "document_id": "editdisp",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "center_x": 3.0,
                "center_y": 4.0,
                "radius": 6.0,
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_circle"
    assert response["result"]["geometry_index"] == 0
    assert response["result"]["center_x"] == 3.0
    assert response["result"]["center_y"] == 4.0
    assert response["result"]["radius"] == 6.0


# ---------------------------------------------------------------------------
# Milestone 9-D2-B3: Edit sketch ellipse geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------


def test_sketch_edit_ellipse_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(1.0, 1.0, 0), 5.0, 3.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 10.0,
            "center_y": 20.0,
            "major_radius": 7.5,
            "minor_radius": 4.0,
            "rotation_deg": 30.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_ellipse"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["center_x"] == 10.0
    assert result["result"]["center_y"] == 20.0
    assert result["result"]["major_radius"] == 7.5
    assert result["result"]["minor_radius"] == 4.0
    assert result["result"]["rotation_deg"] == 30.0
    assert result["result"]["geometry_count"] == 1

    # Ellipse was modified in place.
    ellipse = sketch.Geometry[0]
    assert ellipse.Center.x == 10.0
    assert ellipse.Center.y == 20.0
    assert ellipse.MajorRadius == 7.5
    assert ellipse.MinorRadius == 4.0
    assert ellipse.AngleXU == pytest.approx(math.radians(30.0))
    assert ellipse.XAxis.x == pytest.approx(math.cos(math.radians(30.0)))
    assert ellipse.XAxis.y == pytest.approx(math.sin(math.radians(30.0)))


def test_sketch_edit_ellipse_mixed_sketch_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(Ellipse(_FakePoint(2, 2, 0), 4.0, 2.0))
    sketch.Geometry.append(LineSegment(_FakePoint(4, 4, 0), _FakePoint(5, 4, 0)))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "center_x": 99.0,
            "center_y": 88.0,
            "major_radius": 6.0,
            "minor_radius": 2.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 3

    # Other geometries preserved.
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert isinstance(sketch.Geometry[2], LineSegment)
    assert sketch.Geometry[2].StartPoint.x == 4.0

    # Edited ellipse updated.
    edited = sketch.Geometry[1]
    assert isinstance(edited, Ellipse)
    assert edited.Center.x == 99.0
    assert edited.Center.y == 88.0
    assert edited.MajorRadius == 6.0
    assert edited.MinorRadius == 2.0


def test_sketch_edit_ellipse_rotation_defaults_to_axis_aligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "rot", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 4.0, 2.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "rot",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 4.0,
            "minor_radius": 2.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["rotation_deg"] == 0.0
    ellipse = sketch.Geometry[0]
    # Default rotation leaves axis-aligned directions.
    assert ellipse.MajorDirection.x == 1.0
    assert ellipse.MajorDirection.y == 0.0
    assert ellipse.MinorDirection.x == 0.0
    assert ellipse.MinorDirection.y == 1.0


def test_sketch_edit_ellipse_rotation_is_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "rotabs", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 4.0, 2.0))

    for rotation_deg in (30.0, 0.0, 45.0):
        result = runner._op_sketch_edit_ellipse(
            {
                "document_id": "rotabs",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 4.0,
                "minor_radius": 2.0,
                "rotation_deg": rotation_deg,
            }
        )
        assert result["ok"] is True
        assert sketch.Geometry[0].AngleXU == pytest.approx(math.radians(rotation_deg))


def test_sketch_edit_ellipse_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editoor", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editoor",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 5,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editbool", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editbool",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "boolean" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editfloat", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editfloat",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "integer" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editmiss",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "not found" in result["error"].lower()


def test_sketch_edit_ellipse_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "editwtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editwtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_ellipse_rejects_non_ellipse_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnonell", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editnonell",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 1.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "Ellipse" in result["error"]
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_non_finite_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnan", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    base = {
        "document_id": "editnan",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "geometry_index": 0,
        "center_x": 0.0,
        "center_y": 0.0,
        "major_radius": 1.0,
        "minor_radius": 1.0,
    }
    for bad_key, bad_value in (
        ("center_x", float("nan")),
        ("center_y", float("inf")),
        ("major_radius", float("nan")),
        ("minor_radius", float("-inf")),
        ("rotation_deg", float("nan")),
    ):
        bad = dict(base)
        bad[bad_key] = bad_value
        result = runner._op_sketch_edit_ellipse(bad)
        assert result["ok"] is False, bad_key
        assert result["op"] == "sketch_edit_ellipse"
        assert "finite" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_zero_radius(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editzero", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editzero",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 0.0,
            "minor_radius": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "positive" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_negative_radius(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 1.0, 1.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 5.0,
            "minor_radius": -1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "positive" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_rejects_major_less_than_minor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editorder", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 5.0, 3.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editorder",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "major_radius": 2.0,
            "minor_radius": 5.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "greater than or equal" in result["error"].lower()
    assert len(sketch.Geometry) == 1


def test_sketch_edit_ellipse_accepts_equal_radii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editeq", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 3.0, 3.0))

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editeq",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 1.0,
            "center_y": 2.0,
            "major_radius": 3.0,
            "minor_radius": 3.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_ellipse"


def test_sketch_edit_ellipse_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editcon", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(0, 0, 0), 5.0, 3.0))

    # Add a constraint that references geometry index 0.
    class _FakeConstraint:
        First = 0
        FirstPos = 1
        Second = -1
        SecondPos = 0
        Third = -1
        ThirdPos = 0

    sketch.Constraints.append(_FakeConstraint())

    result = runner._op_sketch_edit_ellipse(
        {
            "document_id": "editcon",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 2.0,
            "center_y": 3.0,
            "major_radius": 6.0,
            "minor_radius": 4.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_ellipse"
    assert "constraint" in result["error"].lower()
    assert len(sketch.Geometry) == 1  # unchanged


def test_handle_request_dispatches_sketch_edit_ellipse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editdisp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Ellipse(_FakePoint(1.0, 1.0, 0), 5.0, 3.0))

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_EDIT_ELLIPSE,
            "params": {
                "document_id": "editdisp",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "center_x": 3.0,
                "center_y": 4.0,
                "major_radius": 6.0,
                "minor_radius": 2.0,
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_ellipse"
    assert response["result"]["geometry_index"] == 0
    assert response["result"]["center_x"] == 3.0
    assert response["result"]["center_y"] == 4.0
    assert response["result"]["major_radius"] == 6.0
    assert response["result"]["minor_radius"] == 2.0


# ---------------------------------------------------------------------------
# Milestone 9-D2-C: Edit sketch rectangle geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------


def _make_rectangle(sketch, x=0.0, y=0.0, width=2.0, height=1.0):
    """Add four connected LineSegment geometries forming an axis-aligned rectangle."""
    sketch.Geometry.append(LineSegment(_FakePoint(x, y, 0), _FakePoint(x + width, y, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(x + width, y, 0), _FakePoint(x + width, y + height, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(x + width, y + height, 0), _FakePoint(x, y + height, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(x, y + height, 0), _FakePoint(x, y, 0)))


def test_sketch_edit_rectangle_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch, x=1.0, y=1.0, width=4.0, height=2.0)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 10.0,
            "y": 20.0,
            "width": 6.0,
            "height": 4.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_rectangle"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["x"] == 10.0
    assert result["result"]["y"] == 20.0
    assert result["result"]["width"] == 6.0
    assert result["result"]["height"] == 4.0
    assert result["result"]["rotation_deg"] == 0.0
    assert result["result"]["geometry_count"] == 4

    # All four lines updated and still connected.
    bottom, right, top, left = sketch.Geometry[0], sketch.Geometry[1], sketch.Geometry[2], sketch.Geometry[3]
    assert bottom.StartPoint.x == 10.0 and bottom.StartPoint.y == 20.0
    assert bottom.EndPoint.x == 16.0 and bottom.EndPoint.y == 20.0
    assert right.StartPoint.x == 16.0 and right.StartPoint.y == 20.0
    assert right.EndPoint.x == 16.0 and right.EndPoint.y == 24.0
    assert top.StartPoint.x == 16.0 and top.StartPoint.y == 24.0
    assert top.EndPoint.x == 10.0 and top.EndPoint.y == 24.0
    assert left.StartPoint.x == 10.0 and left.StartPoint.y == 24.0
    assert left.EndPoint.x == 10.0 and left.EndPoint.y == 20.0


def test_sketch_edit_rectangle_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "rot", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch, x=0.0, y=0.0, width=2.0, height=2.0)

    # 90-degree rotation around centre (1, 1).
    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "rot",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
            "rotation_deg": 90.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["rotation_deg"] == 90.0
    assert result["result"]["geometry_count"] == 4

    # A 90-degree rotation of a square centred at (1,1) keeps the same axis-aligned
    # bounding box; verify edges remain connected and perpendicular.
    bottom, right, top, left = sketch.Geometry[0], sketch.Geometry[1], sketch.Geometry[2], sketch.Geometry[3]
    assert bottom.EndPoint.x == right.StartPoint.x
    assert bottom.EndPoint.y == right.StartPoint.y
    assert right.EndPoint.x == top.StartPoint.x
    assert right.EndPoint.y == top.StartPoint.y
    assert top.EndPoint.x == left.StartPoint.x
    assert top.EndPoint.y == left.StartPoint.y
    assert left.EndPoint.x == bottom.StartPoint.x
    assert left.EndPoint.y == bottom.StartPoint.y


def test_sketch_edit_rectangle_mixed_sketch_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    _make_rectangle(sketch, x=5.0, y=5.0, width=2.0, height=1.0)
    sketch.Geometry.append(LineSegment(_FakePoint(9, 9, 0), _FakePoint(10, 9, 0)))

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "x": 50.0,
            "y": 50.0,
            "width": 3.0,
            "height": 2.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 6

    # Unrelated geometry preserved.
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert isinstance(sketch.Geometry[5], LineSegment)
    assert sketch.Geometry[5].StartPoint.x == 9.0

    # Rectangle updated at indices 1-4.
    rect = sketch.Geometry[1:5]
    assert all(isinstance(g, LineSegment) for g in rect)
    assert rect[0].StartPoint.x == 50.0 and rect[0].StartPoint.y == 50.0
    assert rect[0].EndPoint.x == 53.0 and rect[0].EndPoint.y == 50.0


def test_sketch_edit_rectangle_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editoor", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    # Rectangle needs four slots; index 1 would need slots 1-4 but only 0-3 exist.
    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editoor",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editbool", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editbool",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "boolean" in result["error"].lower()
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editfloat", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editfloat",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "integer" in result["error"].lower()
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editmiss",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "not found" in result["error"].lower()


def test_sketch_edit_rectangle_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "editwtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editwtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_rectangle_rejects_non_rectangle_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnonrect", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    # Four lines that do NOT form a rectangle (disconnected / wrong angles).
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(5, 5, 0), _FakePoint(6, 5, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(9, 9, 0), _FakePoint(10, 9, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(0, 1, 0)))

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editnonrect",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "rectangle" in result["error"].lower()
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_non_line_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnonline", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))
    sketch.Geometry.append(Circle(_FakePoint(1, 0, 0), 1.0))
    sketch.Geometry.append(Circle(_FakePoint(1, 1, 0), 1.0))
    sketch.Geometry.append(Circle(_FakePoint(0, 1, 0), 1.0))

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editnonline",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "LineSegment" in result["error"]
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_non_finite_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnan", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    base = {
        "document_id": "editnan",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "geometry_index": 0,
        "x": 0.0,
        "y": 0.0,
        "width": 2.0,
        "height": 1.0,
    }
    for bad_key, bad_value in (
        ("x", float("nan")),
        ("y", float("inf")),
        ("width", float("nan")),
        ("height", float("-inf")),
        ("rotation_deg", float("nan")),
    ):
        bad = dict(base)
        bad[bad_key] = bad_value
        result = runner._op_sketch_edit_rectangle(bad)
        assert result["ok"] is False, bad_key
        assert result["op"] == "sketch_edit_rectangle"
        assert "finite" in result["error"].lower()
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_zero_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editzero", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editzero",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 0.0,
            "height": 1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "positive" in result["error"].lower()
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_negative_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": -1.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "positive" in result["error"].lower()
    assert len(sketch.Geometry) == 4


def test_sketch_edit_rectangle_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editcon", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch)

    # Add a constraint that references geometry index 1 (second line of rectangle).
    class _FakeConstraint:
        First = -1
        FirstPos = 1
        Second = 1
        SecondPos = 0
        Third = -1
        ThirdPos = 0

    sketch.Constraints.append(_FakeConstraint())

    result = runner._op_sketch_edit_rectangle(
        {
            "document_id": "editcon",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "x": 2.0,
            "y": 3.0,
            "width": 4.0,
            "height": 5.0,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_rectangle"
    assert "constraint" in result["error"].lower()
    assert len(sketch.Geometry) == 4  # unchanged


def test_handle_request_dispatches_sketch_edit_rectangle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editdisp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_rectangle(sketch, x=1.0, y=1.0, width=4.0, height=2.0)

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_EDIT_RECTANGLE,
            "params": {
                "document_id": "editdisp",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "x": 3.0,
                "y": 4.0,
                "width": 6.0,
                "height": 2.0,
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_rectangle"
    assert response["result"]["geometry_index"] == 0
    assert response["result"]["x"] == 3.0
    assert response["result"]["y"] == 4.0
    assert response["result"]["width"] == 6.0
    assert response["result"]["height"] == 2.0


# ---------------------------------------------------------------------------
# Milestone 9-D2-D: Edit sketch triangle geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------


def _make_triangle(sketch, x1=0.0, y1=0.0, x2=4.0, y2=0.0, x3=2.0, y3=3.0):
    """Add three connected LineSegment geometries forming a triangle."""
    sketch.Geometry.append(LineSegment(_FakePoint(x1, y1, 0), _FakePoint(x2, y2, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(x2, y2, 0), _FakePoint(x3, y3, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(x3, y3, 0), _FakePoint(x1, y1, 0)))


def test_sketch_edit_triangle_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch, x1=0.0, y1=0.0, x2=4.0, y2=0.0, x3=2.0, y3=3.0)

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 10.0, "y": 20.0},
                {"x": 16.0, "y": 20.0},
                {"x": 13.0, "y": 25.0},
            ],
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_triangle"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["vertices"] == [
        {"x": 10.0, "y": 20.0},
        {"x": 16.0, "y": 20.0},
        {"x": 13.0, "y": 25.0},
    ]
    assert result["result"]["geometry_count"] == 3

    # All three lines updated and still connected/closed.
    s0, s1, s2 = sketch.Geometry[0], sketch.Geometry[1], sketch.Geometry[2]
    assert s0.StartPoint.x == 10.0 and s0.StartPoint.y == 20.0
    assert s0.EndPoint.x == 16.0 and s0.EndPoint.y == 20.0
    assert s1.StartPoint.x == 16.0 and s1.StartPoint.y == 20.0
    assert s1.EndPoint.x == 13.0 and s1.EndPoint.y == 25.0
    assert s2.StartPoint.x == 13.0 and s2.StartPoint.y == 25.0
    assert s2.EndPoint.x == 10.0 and s2.EndPoint.y == 20.0


def test_sketch_edit_triangle_mixed_sketch_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    _make_triangle(sketch, x1=5.0, y1=5.0, x2=7.0, y2=5.0, x3=6.0, y3=7.0)
    sketch.Geometry.append(LineSegment(_FakePoint(9, 9, 0), _FakePoint(10, 9, 0)))

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "vertices": [
                {"x": 50.0, "y": 50.0},
                {"x": 54.0, "y": 50.0},
                {"x": 52.0, "y": 55.0},
            ],
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 5

    # Unrelated geometry preserved.
    assert isinstance(sketch.Geometry[0], LineSegment)
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert isinstance(sketch.Geometry[4], LineSegment)
    assert sketch.Geometry[4].StartPoint.x == 9.0

    # Triangle updated at indices 1-3.
    tri = sketch.Geometry[1:4]
    assert all(isinstance(g, LineSegment) for g in tri)
    assert tri[0].StartPoint.x == 50.0 and tri[0].StartPoint.y == 50.0
    assert tri[0].EndPoint.x == 54.0 and tri[0].EndPoint.y == 50.0
    assert tri[1].EndPoint.x == 52.0 and tri[1].EndPoint.y == 55.0
    assert tri[2].EndPoint.x == 50.0 and tri[2].EndPoint.y == 50.0


def test_sketch_edit_triangle_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editneg", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editneg",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editoor", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    # Triangle needs three slots; index 1 would need slots 1-3 but only 0-2 exist.
    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editoor",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "out of range" in result["error"]
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editbool", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editbool",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "boolean" in result["error"].lower()
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editfloat", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editfloat",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "integer" in result["error"].lower()
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editmiss",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "not found" in result["error"].lower()


def test_sketch_edit_triangle_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": "editwtype", "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editwtype",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_triangle_rejects_non_triangle_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnontri", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    # Three lines that do NOT form a connected triangle.
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(5, 5, 0), _FakePoint(6, 5, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(9, 9, 0), _FakePoint(10, 9, 0)))

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editnontri",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "triangle" in result["error"].lower()
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_non_line_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnonline", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))
    sketch.Geometry.append(Circle(_FakePoint(1, 0, 0), 1.0))
    sketch.Geometry.append(Circle(_FakePoint(0, 1, 0), 1.0))

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editnonline",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "LineSegment" in result["error"]
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_non_finite_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editnan", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    base = {
        "document_id": "editnan",
        "workspace": str(tmp_path),
        "sketch_name": "Sketch1",
        "geometry_index": 0,
        "vertices": [
            {"x": 0.0, "y": 0.0},
            {"x": 2.0, "y": 0.0},
            {"x": 1.0, "y": 1.0},
        ],
    }
    bad_cases = [
        {**base, "vertices": [{"x": float("nan"), "y": 0.0}, {"x": 2.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]},
        {**base, "vertices": [{"x": 0.0, "y": float("inf")}, {"x": 2.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]},
        {**base, "vertices": [{"x": 0.0, "y": 0.0}, {"x": float("-inf"), "y": 0.0}, {"x": 1.0, "y": 1.0}]},
    ]
    for bad in bad_cases:
        result = runner._op_sketch_edit_triangle(bad)
        assert result["ok"] is False
        assert result["op"] == "sketch_edit_triangle"
        assert "finite" in result["error"].lower()
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_duplicate_vertex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editdup", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    # Duplicate adjacent vertices: vertex 0 == vertex 1.
    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editdup",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 0.0, "y": 0.0},
                {"x": 1.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "duplicate" in result["error"].lower()
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_collinear_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editcoll", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    # Collinear vertices: all on a straight line.
    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editcoll",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "collinear" in result["error"].lower() or "degenerate" in result["error"].lower()
    assert len(sketch.Geometry) == 3


def test_sketch_edit_triangle_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editcon", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch)

    # Add a constraint that references geometry index 1 (second line of triangle).
    class _FakeConstraint:
        First = -1
        FirstPos = 1
        Second = 1
        SecondPos = 0
        Third = -1
        ThirdPos = 0

    sketch.Constraints.append(_FakeConstraint())

    result = runner._op_sketch_edit_triangle(
        {
            "document_id": "editcon",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 2.0, "y": 3.0},
                {"x": 6.0, "y": 3.0},
                {"x": 4.0, "y": 7.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_triangle"
    assert "constraint" in result["error"].lower()
    assert len(sketch.Geometry) == 3  # unchanged


def test_handle_request_dispatches_sketch_edit_triangle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "editdisp", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_triangle(sketch, x1=0.0, y1=0.0, x2=4.0, y2=0.0, x3=2.0, y3=3.0)

    response = runner.handle_request(
        {
            "op": runner.OP_SKETCH_EDIT_TRIANGLE,
            "params": {
                "document_id": "editdisp",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "vertices": [
                    {"x": 3.0, "y": 4.0},
                    {"x": 7.0, "y": 4.0},
                    {"x": 5.0, "y": 7.0},
                ],
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_triangle"
    assert response["result"]["geometry_index"] == 0
    assert response["result"]["vertices"] == [
        {"x": 3.0, "y": 4.0},
        {"x": 7.0, "y": 4.0},
        {"x": 5.0, "y": 7.0},
    ]


# ---------------------------------------------------------------------------
# Milestone 9-D2-E: Edit sketch regular polygon geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------

def _make_regular_polygon(sketch, center_x=0.0, center_y=0.0, radius=5.0, sides=5, rotation_deg=0.0):
    """Add ``sides`` connected LineSegment geometries forming a regular polygon.

    Vertices lie on the circumcircle centred at (center_x, center_y), rotated
    counter-clockwise from +X by rotation_deg, matching sketch_add_regular_polygon.
    """
    import math as _math
    start_angle = _math.radians(rotation_deg)
    vertices = []
    for i in range(sides):
        angle = start_angle + 2.0 * _math.pi * i / sides
        vertices.append(_FakePoint(center_x + radius * _math.cos(angle), center_y + radius * _math.sin(angle), 0))
    for i in range(sides):
        sketch.Geometry.append(LineSegment(vertices[i], vertices[(i + 1) % sides]))


def test_sketch_edit_regular_polygon_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, center_x=0.0, center_y=0.0, radius=5.0, sides=5, rotation_deg=0.0)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 10.0,
            "center_y": 20.0,
            "radius": 8.0,
            "sides": 5,
            "rotation_deg": 30.0,
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_regular_polygon"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["center_x"] == 10.0
    assert result["result"]["center_y"] == 20.0
    assert result["result"]["radius"] == 8.0
    assert result["result"]["sides"] == 5
    assert result["result"]["rotation_deg"] == 30.0
    assert result["result"]["geometry_count"] == 5

    # All five edges updated; closed loop preserved.
    for i in range(5):
        seg = sketch.Geometry[i]
        next_seg = sketch.Geometry[(i + 1) % 5]
        assert seg.EndPoint.x == pytest.approx(next_seg.StartPoint.x)
        assert seg.EndPoint.y == pytest.approx(next_seg.StartPoint.y)

    # Vertex 0 should be at rotation 30deg on the new circumcircle.
    import math as _math
    v0 = sketch.Geometry[0].StartPoint
    assert v0.x == pytest.approx(10.0 + 8.0 * _math.cos(_math.radians(30.0)))
    assert v0.y == pytest.approx(20.0 + 8.0 * _math.sin(_math.radians(30.0)))


def test_sketch_edit_regular_polygon_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    _make_regular_polygon(sketch, center_x=5.0, center_y=5.0, radius=2.0, sides=4, rotation_deg=0.0)
    sketch.Geometry.append(LineSegment(_FakePoint(9, 9, 0), _FakePoint(10, 9, 0)))

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "center_x": 50.0,
            "center_y": 50.0,
            "radius": 3.0,
            "sides": 4,
            "rotation_deg": 0.0,
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 6

    # Unrelated geometry preserved.
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert sketch.Geometry[5].StartPoint.x == 9.0

    # Polygon updated at indices 1-4.
    for i in range(4):
        seg = sketch.Geometry[1 + i]
        next_seg = sketch.Geometry[1 + (i + 1) % 4]
        assert seg.EndPoint.x == pytest.approx(next_seg.StartPoint.x)
        assert seg.EndPoint.y == pytest.approx(next_seg.StartPoint.y)


def test_sketch_edit_regular_polygon_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "out of range" in result["error"].lower()


def test_sketch_edit_regular_polygon_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "out of range" in result["error"].lower()


def test_sketch_edit_regular_polygon_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "boolean" in result["error"].lower()


def test_sketch_edit_regular_polygon_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "integer" in result["error"].lower()


def test_sketch_edit_regular_polygon_rejects_wrong_side_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, center_x=0.0, center_y=0.0, radius=5.0, sides=4, rotation_deg=0.0)

    # Ask to edit as a 5-sided polygon but only 4 edges exist at that index.
    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 5,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "out of range" in result["error"].lower()


def test_sketch_edit_regular_polygon_rejects_non_regular_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    # Connected lines but NOT a regular polygon (unequal edge lengths).
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(4, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(4, 0, 0), _FakePoint(5, 1, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(5, 1, 0), _FakePoint(1, 1, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(1, 1, 0), _FakePoint(0, 0, 0)))

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 2.0,
            "center_y": 0.5,
            "radius": 2.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"


def test_sketch_edit_regular_polygon_rejects_non_finite_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    import math as _math
    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": _math.nan,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "finite" in result["error"].lower()

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": _math.inf,
            "sides": 4,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "finite" in result["error"].lower()

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": -1.0,
            "sides": 4,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "positive" in result["error"].lower()


def test_sketch_edit_regular_polygon_rejects_invalid_sides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 2,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "at least 3" in result["error"].lower()

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": True,
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "integer" in result["error"].lower()


def test_sketch_edit_regular_polygon_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "not found" in result["error"].lower()


def test_sketch_edit_regular_polygon_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    # Add a box (non-sketch object) to the document.
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_regular_polygon_rejects_non_line_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    # Put a Circle where the first line segment should be, then 3 more lines.
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))
    sketch.Geometry.append(LineSegment(_FakePoint(1, 0, 0), _FakePoint(2, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(2, 0, 0), _FakePoint(3, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(3, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "LineSegment" in result["error"]


def test_sketch_edit_regular_polygon_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, sides=4)

    # Reference index 1 with a constraint.
    class _FakeConstraint:
        def __init__(self, First=None, Second=None, Third=None):
            self.First = First
            self.Second = Second
            self.Third = Third

    sketch.Constraints.append(_FakeConstraint(First=1))

    result = runner._op_sketch_edit_regular_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "center_x": 0.0,
            "center_y": 0.0,
            "radius": 5.0,
            "sides": 4,
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_regular_polygon"
    assert "constraint" in result["error"].lower()


def test_handle_request_dispatches_sketch_edit_regular_polygon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_regular_polygon(sketch, center_x=0.0, center_y=0.0, radius=5.0, sides=4, rotation_deg=0.0)

    response = runner.handle_request(
        {
            "op": "sketch_edit_regular_polygon",
            "params": {
                "document_id": "edit",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "center_x": 10.0,
                "center_y": 20.0,
                "radius": 8.0,
                "sides": 4,
                "rotation_deg": 45.0,
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_regular_polygon"
    assert response["result"]["geometry_index"] == 0
    assert len(response["result"]["vertices"]) == 4


# ---------------------------------------------------------------------------
# Milestone 9-D2-E: Edit sketch custom polygon geometry (FreeCAD mocked)
# ---------------------------------------------------------------------------

def _make_polygon(sketch, vertices):
    """Add connected LineSegment geometries forming a closed polygon from vertices."""
    n = len(vertices)
    pts = [_FakePoint(x, y, 0) for x, y in vertices]
    for i in range(n):
        sketch.Geometry.append(LineSegment(pts[i], pts[(i + 1) % n]))


def test_sketch_edit_polygon_successful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 10.0, "y": 10.0},
                {"x": 16.0, "y": 10.0},
                {"x": 16.0, "y": 15.0},
                {"x": 10.0, "y": 15.0},
            ],
        }
    )

    assert result["ok"] is True
    assert result["op"] == "sketch_edit_polygon"
    assert result["result"]["sketch_name"] == "Sketch1"
    assert result["result"]["geometry_index"] == 0
    assert result["result"]["vertices"] == [
        {"x": 10.0, "y": 10.0},
        {"x": 16.0, "y": 10.0},
        {"x": 16.0, "y": 15.0},
        {"x": 10.0, "y": 15.0},
    ]
    assert result["result"]["geometry_count"] == 4

    # Closed loop preserved: last edge connects back to first vertex.
    for i in range(4):
        seg = sketch.Geometry[i]
        next_seg = sketch.Geometry[(i + 1) % 4]
        assert seg.EndPoint.x == pytest.approx(next_seg.StartPoint.x)
        assert seg.EndPoint.y == pytest.approx(next_seg.StartPoint.y)
    assert sketch.Geometry[3].EndPoint.x == pytest.approx(sketch.Geometry[0].StartPoint.x)
    assert sketch.Geometry[3].EndPoint.y == pytest.approx(sketch.Geometry[0].StartPoint.y)


def test_sketch_edit_polygon_preserves_other_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "mixed", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    _make_polygon(sketch, [(5.0, 5.0), (7.0, 5.0), (7.0, 7.0), (5.0, 7.0)])
    sketch.Geometry.append(LineSegment(_FakePoint(9, 9, 0), _FakePoint(10, 9, 0)))

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "mixed",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "vertices": [
                {"x": 50.0, "y": 50.0},
                {"x": 54.0, "y": 50.0},
                {"x": 54.0, "y": 54.0},
                {"x": 50.0, "y": 54.0},
            ],
        }
    )

    assert result["ok"] is True
    assert result["result"]["geometry_count"] == 6

    # Unrelated geometry preserved.
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert sketch.Geometry[5].StartPoint.x == 9.0


def test_sketch_edit_polygon_rejects_negative_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": -1,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "out of range" in result["error"].lower()


def test_sketch_edit_polygon_rejects_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "out of range" in result["error"].lower()


def test_sketch_edit_polygon_rejects_boolean_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": True,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "boolean" in result["error"].lower()


def test_sketch_edit_polygon_rejects_float_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 1.5,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "integer" in result["error"].lower()


def test_sketch_edit_polygon_rejects_mismatched_vertex_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    # Existing polygon has 4 edges; supplying 5 vertices exceeds the geometry range.
    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
                {"x": 2.0, "y": 1.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "out of range" in result["error"].lower()


def test_sketch_edit_polygon_rejects_fewer_than_three_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "at least 3" in result["error"].lower()


def test_sketch_edit_polygon_rejects_duplicate_vertex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "duplicate" in result["error"].lower()


def test_sketch_edit_polygon_rejects_repeated_final_vertex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
                {"x": 0.0, "y": 0.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "duplicates the first" in result["error"].lower()


def test_sketch_edit_polygon_rejects_non_finite_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    import math as _math
    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": _math.nan, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "finite" in result["error"].lower()

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": _math.inf, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )
    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "finite" in result["error"].lower()


def test_sketch_edit_polygon_rejects_collinear_vertices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "zero area" in result["error"].lower() or "degenerate" in result["error"].lower()


def test_sketch_edit_polygon_missing_sketch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "NoSuchSketch",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "not found" in result["error"].lower()


def test_sketch_edit_polygon_wrong_object_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    doc.addObject("Part::Box", "Box1")

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Box1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "Sketcher::SketchObject" in result["error"]


def test_sketch_edit_polygon_rejects_non_line_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    # Put a Circle where a line segment should be, then 2 more lines (3 total).
    sketch.Geometry.append(Circle(_FakePoint(0, 0, 0), 1.0))
    sketch.Geometry.append(LineSegment(_FakePoint(1, 0, 0), _FakePoint(2, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(2, 0, 0), _FakePoint(1, 0, 0)))

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "LineSegment" in result["error"]


def test_sketch_edit_polygon_rejects_malformed_existing_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    # Lines that do NOT form a connected closed loop.
    sketch.Geometry.append(LineSegment(_FakePoint(0, 0, 0), _FakePoint(1, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(2, 0, 0), _FakePoint(3, 0, 0)))
    sketch.Geometry.append(LineSegment(_FakePoint(4, 0, 0), _FakePoint(5, 0, 0)))

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "connected" in result["error"].lower()


def test_sketch_edit_polygon_rejects_constrained_geometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    class _FakeConstraint:
        def __init__(self, First=None, Second=None, Third=None):
            self.First = First
            self.Second = Second
            self.Third = Third

    sketch.Constraints.append(_FakeConstraint(First=2))

    result = runner._op_sketch_edit_polygon(
        {
            "document_id": "edit",
            "workspace": str(tmp_path),
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 0.0},
                {"x": 4.0, "y": 3.0},
                {"x": 0.0, "y": 3.0},
            ],
        }
    )

    assert result["ok"] is False
    assert result["op"] == "sketch_edit_polygon"
    assert "constraint" in result["error"].lower()


def test_handle_request_dispatches_sketch_edit_polygon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_sketch(
        {"document_id": "edit", "workspace": str(tmp_path), "sketch_name": "Sketch1"}
    )
    sketch = doc.objects["Sketch1"]
    _make_polygon(sketch, [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)])

    response = runner.handle_request(
        {
            "op": "sketch_edit_polygon",
            "params": {
                "document_id": "edit",
                "workspace": str(tmp_path),
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "vertices": [
                    {"x": 10.0, "y": 10.0},
                    {"x": 16.0, "y": 10.0},
                    {"x": 16.0, "y": 15.0},
                    {"x": 10.0, "y": 15.0},
                ],
            },
        }
    )

    assert response["ok"] is True
    assert response["op"] == "sketch_edit_polygon"
    assert response["result"]["geometry_index"] == 0
    assert len(response["result"]["vertices"]) == 4
