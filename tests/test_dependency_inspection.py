"""Focused tests for object dependency inspection via InList/OutList.

These tests run without FreeCAD or Taiga unless marked otherwise: the runner
is driven by fakes imported from ``tests.test_runner`` and the MCP tool /
backend are driven by mocks.  Real-FreeCAD integration tests are skipped when
the freecad console executable is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from freecad_mcp.app.inspection import (
    DirectInspectionBackend,
    InspectionRequest,
    ObjectInspection,
)
from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.freecad_bridge import runner
from freecad_mcp.pathing import for_target
from freecad_mcp.tools import modeling

from tests.test_modeling_tools import _FakeBridge
from tests.test_runner import (
    _FakeDoc,
    _install_fake_freecad,
)


def _get_inspect_object_tool():
    """Retrieve the nested ``inspect_object_tool`` from the tool registry.

    The tool is defined inside ``_register_tools`` and is not importable at
    module level, so we register it into a throwaway server and pluck it out.
    """
    from freecad_mcp.config import Settings
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
    return server.tools["inspect_object"]

DOC_ID = "depdoc"
SKETCH_NAME = "Sketch"
PAD_NAME = "Pad"
POCKET_NAME = "Pocket"


def _make_dependency_doc(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> tuple[_FakeDoc, Any, Any, Any]:
    """Saved document with Sketch -> Pad -> Pocket dependency chain.

    Pad.OutList = [Sketch]   (Pad depends on Sketch)
    Pocket.OutList = [Sketch, Pad]  (Pocket depends on Sketch and Pad)
    Sketch.InList = [Pad, Pocket]   (Sketch is depended on by Pad and Pocket)
    Pad.InList = [Pocket]           (Pad is depended on by Pocket)
    """
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})

    sketch = doc.addObject("Sketcher::SketchObject", SKETCH_NAME)
    pad = doc.addObject("PartDesign::Pad", PAD_NAME)
    pocket = doc.addObject("PartDesign::Pocket", POCKET_NAME)

    # Native dependency links.
    sketch.OutList = []
    sketch.InList = [pad, pocket]
    pad.OutList = [sketch]
    pad.InList = [pocket]
    pocket.OutList = [sketch, pad]
    pocket.InList = []

    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))
    return doc, sketch, pad, pocket


# ---------------------------------------------------------------------------
# Runner: _op_inspect_object exposes depends_on / depended_on_by
# ---------------------------------------------------------------------------


def test_inspect_object_returns_outlist_as_depends_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _doc, _sketch, pad, _pocket = _make_dependency_doc(monkeypatch, tmp_path)

    result = runner._op_inspect_object(
        {"document_id": DOC_ID, "object_name": PAD_NAME, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "inspect_object"
    info = result["result"]["info"]
    assert info["depends_on"] == [SKETCH_NAME]
    assert info["depended_on_by"] == [POCKET_NAME]


def test_inspect_object_returns_inlist_as_depended_on_by(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _doc, sketch, _pad, _pocket = _make_dependency_doc(monkeypatch, tmp_path)

    result = runner._op_inspect_object(
        {"document_id": DOC_ID, "object_name": SKETCH_NAME, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    info = result["result"]["info"]
    # Sketch is depended on by both Pad and Pocket.
    assert set(info["depended_on_by"]) == {PAD_NAME, POCKET_NAME}
    assert info["depends_on"] == []


def test_inspect_object_missing_outlist_inlist_defaults_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    box = doc.addObject("Part::Box", "Box1")
    box.Name = "Box1"
    box.Label = "Box1"
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_inspect_object(
        {"document_id": DOC_ID, "object_name": "Box1", "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    info = result["result"]["info"]
    assert info["depends_on"] == []
    assert info["depended_on_by"] == []


def test_inspect_object_rejects_missing_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_inspect_object(
        {"document_id": DOC_ID, "object_name": "NoSuchObject", "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "inspect_object"
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# MCP tool: inspect_object_tool populates dependency fields
# ---------------------------------------------------------------------------


def test_inspect_object_tool_populates_dependency_fields() -> None:
    inspect_object_tool = _get_inspect_object_tool()

    bridge = _FakeBridge(
        result={
            "ok": True,
            "op": "inspect_object",
            "result": {
                "document_id": DOC_ID,
                "object_name": PAD_NAME,
                "info": {
                    "name": PAD_NAME,
                    "type": "PartDesign::Pad",
                    "depends_on": [SKETCH_NAME],
                    "depended_on_by": [POCKET_NAME],
                },
            },
        }
    )

    with patch.object(modeling, "_make_bridge", return_value=bridge):
        info = inspect_object_tool(
            modeling.InspectObjectParams(document_id=DOC_ID, object_name=PAD_NAME)
        )

    assert info.name == PAD_NAME
    assert info.depends_on == [SKETCH_NAME]
    assert info.depended_on_by == [POCKET_NAME]
    assert bridge.calls[0][0] == "inspect_object"
    assert bridge.calls[0][1]["object_name"] == PAD_NAME


def test_inspect_object_tool_defaults_dependency_fields_when_absent() -> None:
    inspect_object_tool = _get_inspect_object_tool()

    bridge = _FakeBridge(
        result={
            "ok": True,
            "op": "inspect_object",
            "result": {
                "document_id": DOC_ID,
                "object_name": "Box1",
                "info": {"name": "Box1", "type": "Part::Box"},
            },
        }
    )

    with patch.object(modeling, "_make_bridge", return_value=bridge):
        info = inspect_object_tool(
            modeling.InspectObjectParams(document_id=DOC_ID, object_name="Box1")
        )

    assert info.depends_on == []
    assert info.depended_on_by == []


# ---------------------------------------------------------------------------
# App contract: ObjectInspection and DirectInspectionBackend
# ---------------------------------------------------------------------------


def test_object_inspection_has_dependency_fields() -> None:
    obj = ObjectInspection(
        name=PAD_NAME,
        type="PartDesign::Pad",
        depends_on=[SKETCH_NAME],
        depended_on_by=[POCKET_NAME],
    )
    assert obj.depends_on == [SKETCH_NAME]
    assert obj.depended_on_by == [POCKET_NAME]


def test_object_inspection_dependency_fields_default_empty() -> None:
    obj = ObjectInspection(name="Box1", type="Part::Box")
    assert obj.depends_on == []
    assert obj.depended_on_by == []


def test_direct_inspection_backend_populates_dependency_fields() -> None:
    def fake_run(op: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
        if op == "list_objects":
            return {"document_id": DOC_ID, "objects": [{"name": PAD_NAME, "type": "PartDesign::Pad"}]}
        if op == "inspect_object":
            return {
                "document_id": DOC_ID,
                "object_name": PAD_NAME,
                "info": {
                    "name": PAD_NAME,
                    "type": "PartDesign::Pad",
                    "depends_on": [SKETCH_NAME],
                    "depended_on_by": [POCKET_NAME],
                },
            }
        raise AssertionError(f"unexpected op {op!r}")

    executor = type("E", (), {"run": staticmethod(fake_run)})()
    backend = DirectInspectionBackend(executor)
    result = backend.inspect(InspectionRequest(document_id=DOC_ID))

    assert result.ok is True
    assert result.document is not None
    by_name = {o.name: o for o in result.document.objects}
    pad = by_name[PAD_NAME]
    assert pad.depends_on == [SKETCH_NAME]
    assert pad.depended_on_by == [POCKET_NAME]


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


def _build_dependency_doc_code(fcstd_win: str) -> str:
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "import Part\n"
        "from Part import LineSegment\n"
        "from FreeCAD import Vector\n"
        "doc = App.newDocument(\"DepLife\")\n"
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
        "pocket = doc.addObject(\"PartDesign::Pocket\", \"Pocket\")\n"
        "body.addObject(pocket)\n"
        "pocket.Profile = sketch\n"
        "pocket.Length = 5.0\n"
        "doc.recompute()\n"
        "doc.saveAs(" + path + ")\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"BUILD_OK\")\n"
    )


@requires_freecad
def test_real_dependency_directions_and_persistence(tmp_path: Path) -> None:
    """Verify depends_on (OutList) / depended_on_by (InList) against real FreeCAD 1.1.4.

    Builds Sketch -> Pad -> Pocket, then inspects each object through the MCP
    layer and confirms:
      - Pad depends_on Sketch; Sketch depended_on_by Pad
      - Pocket depends_on Sketch and Pad
      - Dependency info persists across save/close/reopen
    """
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "deplife.fcstd", target)
    doc_id = "deplife"

    bridge = FreeCadBridge.from_settings(_settings())

    proc = _freecad_run_code(_build_dependency_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    # Inspect each object through the shared MCP layer.
    pad = bridge.run(
        "inspect_object",
        {"document_id": doc_id, "object_name": PAD_NAME, "workspace": str(tmp_path)},
    )
    assert pad["info"]["depends_on"] == [SKETCH_NAME], pad
    assert POCKET_NAME in pad["info"]["depended_on_by"], pad

    pocket = bridge.run(
        "inspect_object",
        {"document_id": doc_id, "object_name": POCKET_NAME, "workspace": str(tmp_path)},
    )
    assert set(pocket["info"]["depends_on"]) == {SKETCH_NAME, PAD_NAME}, pocket

    sketch = bridge.run(
        "inspect_object",
        {"document_id": doc_id, "object_name": SKETCH_NAME, "workspace": str(tmp_path)},
    )
    assert sketch["info"]["depends_on"] == [], sketch
    # Sketch is depended on by Pad and Pocket (Body also appears because
    # Body.Group references the sketch).  Assert the expected dependents are
    # present without requiring exact equality.
    assert {PAD_NAME, POCKET_NAME} <= set(sketch["info"]["depended_on_by"]), sketch

    # Persistence: reopen the same .fcstd in a fresh process and re-inspect.
    proc = _freecad_run_code(
        (
            "import FreeCAD as App\n"
            "doc = App.openDocument(" + repr(fcstd_win) + ")\n"
            "pad = doc.getObject(\"Pad\")\n"
            "sketch = doc.getObject(\"Sketch\")\n"
            "assert pad.OutList is not None and len(pad.OutList) >= 1\n"
            "assert sketch.InList is not None and len(sketch.InList) >= 1\n"
            "print(\"REOPEN_OK\")\n"
        )
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "REOPEN_OK" in proc.stdout, proc.stdout + proc.stderr
