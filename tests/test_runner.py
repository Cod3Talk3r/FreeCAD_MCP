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

    def openDocument(self, path: str) -> object:
        self.open_calls.append(path)
        if self.open_error is not None:
            raise self.open_error
        return self.doc

    def newDocument(self, name: str) -> object:
        self.new_calls.append(name)
        return self.doc


class _FakeDoc:
    def __init__(self) -> None:
        self.objects: dict[str, object] = {}

    def getObject(self, name: str) -> object | None:
        return self.objects.get(name)

    def addObject(self, type_id: str, name: str) -> SimpleNamespace:
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
