"""Focused tests for document persistence / reopen validation.

These tests run without FreeCAD or Taiga: the runner is driven by fakes
imported from ``tests.test_runner`` and the MCP tool is driven by a mock
bridge.  Real-FreeCAD integration tests are skipped when the freecad console
executable is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.freecad_bridge import runner
from freecad_mcp.pathing import for_target

from tests.test_runner import (
    _FakeBody,
    _FakeDoc,
    _FakeFreeCad,
    _FakePad,
    _FakeSketch,
    _install_fake_freecad,
)

DOC_ID = "reopen_doc"


class _ReopenDropsObjectFreeCad:
    """Fake FreeCAD whose second ``openDocument`` returns a doc missing an object."""

    def __init__(self, first_doc: _FakeDoc, second_doc: _FakeDoc) -> None:
        self.first_doc = first_doc
        self.second_doc = second_doc
        self.open_count = 0
        self.close_calls: list[str] = []
        self.ActiveDocument = first_doc

    def openDocument(self, path: str) -> _FakeDoc:
        self.open_count += 1
        return self.first_doc if self.open_count == 1 else self.second_doc

    def closeDocument(self, name: str) -> None:
        self.close_calls.append(name)


def _make_reopen_doc(
    monkeypatch: pytest.MonkeyPatch, tmp_path, doc_id: str = DOC_ID
) -> tuple[_FakeDoc, _FakeBody, _FakePad, _FakeSketch]:
    """Saved document with Body (Tip=Pad), Pad (depends on Sketch) and Sketch."""
    doc = _FakeDoc()
    doc.Name = doc_id
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document(
        {"document_id": doc_id, "workspace": str(tmp_path)}
    )

    body = doc.addObject("PartDesign::Body", "Body")
    sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
    pad = doc.addObject("PartDesign::Pad", "Pad")

    body.addObject(sketch)
    body.addObject(pad)

    # Wire up dependency relationships and Body.Tip.
    body.Tip = pad
    pad.OutList = [sketch]
    sketch.InList = [pad]
    pad.Length = 10.0

    # Persist the .fcstd file so _open_existing_document finds it.
    (tmp_path / f"{doc_id}.fcstd").write_bytes(b"fake fcstd")
    return doc, body, pad, sketch


def _get_validate_reopen_tool():
    """Retrieve the nested ``validate_reopen_tool`` from the tool registry.

    The tool is defined inside ``_register_tools`` and is not importable at
    module level, so we register it into a throwaway server and pluck it out.
    """
    from freecad_mcp.tools.modeling import _register_tools

    class _FakeServer:
        def __init__(self) -> None:
            self.tools: dict[str, Any] = {}

        def tool(self, name: str, **kw: Any):
            def deco(fn):
                self.tools[name] = fn
                return fn

            return deco

    server = _FakeServer()
    _register_tools(server, Settings(freecad_timeout_seconds=120))
    return server.tools["validate_reopen"]


# ---------------------------------------------------------------------------
# Runner: _op_validate_reopen
# ---------------------------------------------------------------------------


def test_validate_reopen_success_all_checks_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Successful save/close/reopen returns ok=True with all_passed=True."""
    _doc, _body, _pad, _sketch = _make_reopen_doc(monkeypatch, tmp_path)

    result = runner._op_validate_reopen(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "validate_reopen"
    report = result["result"]
    assert report["document_id"] == DOC_ID
    assert report["all_passed"] is True, report["failures"]
    assert report["failures"] == []
    assert report["object_count"] == 3
    assert report["before_object_count"] == 3
    assert report["after_object_count"] == 3

    check_names = {c["name"] for c in report["checks"]}
    expected = {
        "objects_preserved",
        "types_preserved",
        "dependencies_preserved",
        "geometry_valid_after_reopen",
        "body_tip_preserved",
        "parametric_properties_preserved",
    }
    assert check_names == expected, check_names
    for check in report["checks"]:
        assert check["passed"] is True, check


def test_validate_reopen_missing_document_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Reopen validation fails when no persisted .fcstd file exists."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    wks = ws_mod.Workspace(root=tmp_path)
    assert not wks.document_path(DOC_ID).is_file()

    result = runner._op_validate_reopen(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "validate_reopen"
    assert "not found" in result["error"].lower()


def test_validate_reopen_open_failure_returns_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Reopen validation fails when opening the document raises."""
    from freecad_mcp.freecad_bridge import workspace as ws_mod
    import sys

    wks = ws_mod.Workspace(root=tmp_path)
    (tmp_path / f"{DOC_ID}.fcstd").write_bytes(b"fake fcstd")
    fc = _FakeFreeCad(open_error=OSError("Invalid project file"))
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)

    result = runner._op_validate_reopen(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "validate_reopen"
    assert "Invalid project file" in result["error"]


def test_validate_reopen_object_removed_after_reopen_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """If reopen loses an object, objects_preserved check fails."""
    import sys

    doc = _FakeDoc()
    doc.Name = DOC_ID
    body = doc.addObject("PartDesign::Body", "Body")
    sketch = doc.addObject("Sketcher::SketchObject", "Sketch")
    pad = doc.addObject("PartDesign::Pad", "Pad")
    body.addObject(sketch)
    body.addObject(pad)
    body.Tip = pad
    (tmp_path / f"{DOC_ID}.fcstd").write_bytes(b"fake fcstd")

    # Second doc: same body+sketch but pad removed.
    doc_after = _FakeDoc()
    doc_after.Name = DOC_ID
    body_after = doc_after.addObject("PartDesign::Body", "Body")
    sketch_after = doc_after.addObject("Sketcher::SketchObject", "Sketch")
    body_after.addObject(sketch_after)

    fc = _ReopenDropsObjectFreeCad(doc, doc_after)
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)

    result = runner._op_validate_reopen(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True  # operation itself succeeds, reports a failure
    assert result["op"] == "validate_reopen"
    report = result["result"]
    assert report["all_passed"] is False
    objects_check = next(c for c in report["checks"] if c["name"] == "objects_preserved")
    assert objects_check["passed"] is False
    assert any("Pad" in f for f in report["failures"]), report["failures"]


# ---------------------------------------------------------------------------
# MCP tool: validate_reopen_tool populates ValidateReopenResult
# ---------------------------------------------------------------------------


def test_validate_reopen_tool_populates_report_fields() -> None:
    from freecad_mcp.tools.modeling import ValidateReopenParams

    validate_reopen_tool = _get_validate_reopen_tool()

    bridge_result = {
        "ok": True,
        "op": "validate_reopen",
        "result": {
            "document_id": DOC_ID,
            "checks": [
                {"name": "objects_preserved", "passed": True, "detail": "3/3 objects present after reopen"},
                {"name": "body_tip_preserved", "passed": True, "detail": "Body.Tip identities unchanged after reopen"},
            ],
            "all_passed": True,
            "failures": [],
            "object_count": 3,
            "before_object_count": 3,
            "after_object_count": 3,
            "meta_before": {"revision": 1},
        },
    }

    class _FakeBridge:
        def __init__(self, result: Any) -> None:
            self.result = result
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def run(self, op: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
            self.calls.append((op, params))
            return self.result

    bridge = _FakeBridge(bridge_result)
    from freecad_mcp.tools import modeling

    with pytest.MonkeyPatch().context() as m:
        m.setattr(modeling, "_make_bridge", lambda settings: bridge)
        report = validate_reopen_tool(ValidateReopenParams(document_id=DOC_ID))

    assert report.document_id == DOC_ID
    assert report.all_passed is True
    assert report.failures == []
    assert report.object_count == 3
    assert report.before_object_count == 3
    assert report.after_object_count == 3
    assert report.meta_before == {"revision": 1}
    assert len(report.checks) == 2
    assert report.checks[0]["name"] == "objects_preserved"
    assert bridge.calls[0][0] == "validate_reopen"
    assert bridge.calls[0][1]["document_id"] == DOC_ID


# ---------------------------------------------------------------------------
# Real-FreeCAD 1.1.4 integration test (skipped without freecadcmd)
# ---------------------------------------------------------------------------

_DISCOVERED = find_freecad_executable()

requires_freecad = pytest.mark.skipif(
    _DISCOVERED is None,
    reason="FreeCAD console executable not found (install FreeCAD or set FREECAD_MCP_FREECAD_EXECUTABLE).",
)


def _settings() -> Settings:
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)


def _freecad_run_code(code: str, timeout: int = 120) -> Any:
    import subprocess

    return subprocess.run(
        [str(_DISCOVERED), "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _build_and_save_body_doc_code(fcstd_win: str) -> str:
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "import Part\n"
        "from Part import LineSegment\n"
        "from FreeCAD import Vector\n"
        "doc = App.newDocument(\"ReopenLife\")\n"
        "body = doc.addObject(\"PartDesign::Body\", \"Body\")\n"
        "sketch = doc.addObject(\"Sketcher::SketchObject\", \"Sketch\")\n"
        "sketch.addGeometry(LineSegment(Vector(0,0,0), Vector(10,0,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(10,0,0), Vector(10,10,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(10,10,0), Vector(0,10,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(0,10,0), Vector(0,0,0)))\n"
        "body.addObject(sketch)\n"
        "pad = doc.addObject(\"PartDesign::Pad\", \"Pad\")\n"
        "body.addObject(pad)\n"
        "pad.Profile = sketch\n"
        "pad.Length = 10.0\n"
        "doc.recompute()\n"
        "doc.saveAs(" + path + ")\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"BUILD_OK\")\n"
    )


@requires_freecad
def test_real_persistence_and_reopen(tmp_path: Path) -> None:
    """Verify a Body/Pad/Sketch document survives save, close and reopen.

    Builds the document and saves it, closes it, then calls validate_reopen
    through the shared MCP layer and confirms every persistence check passes:
    object identities, types, dependencies, geometry validity, Body.Tip and
    parametric properties are all preserved after reopen.
    """
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "reopenlife.fcstd", target)
    doc_id = "reopenlife"

    bridge = FreeCadBridge.from_settings(_settings())

    proc = _freecad_run_code(_build_and_save_body_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    report = bridge.run(
        "validate_reopen",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )

    assert report["all_passed"] is True, report
    assert report["failures"] == [], report["failures"]
    # Object count must be stable across close/reopen (no objects lost or
    # gained).  FreeCAD creates the Body plus its Origin axes/planes in
    # addition to Sketch/Pad, so the exact count is >3 but must match.
    assert report["object_count"] >= 3, report
    assert report["before_object_count"] == report["after_object_count"] == report["object_count"], report

    check_names = {c["name"] for c in report["checks"]}
    expected = {
        "objects_preserved",
        "types_preserved",
        "dependencies_preserved",
        "geometry_valid_after_reopen",
        "body_tip_preserved",
        "parametric_properties_preserved",
    }
    assert check_names == expected, check_names
    for check in report["checks"]:
        assert check["passed"] is True, check
