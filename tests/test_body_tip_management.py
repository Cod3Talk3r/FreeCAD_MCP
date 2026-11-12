"""Focused tests for PartDesign Body.Tip management.

Mock tests run without FreeCAD: the runner is driven by fakes imported from
``tests.test_runner``.  Real-FreeCAD integration tests are skipped when the
freecad console executable is unavailable.
"""

from __future__ import annotations

import subprocess
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
    _FakePad,
    _install_fake_freecad,
)

_DISCOVERED = find_freecad_executable()

requires_freecad = pytest.mark.skipif(
    _DISCOVERED is None,
    reason="FreeCAD console executable not found (install FreeCAD or set FREECAD_MCP_FREECAD_EXECUTABLE).",
)


def _settings() -> Settings:
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)

DOC_ID = "tipdoc"
BODY_NAME = "Body"
PAD_NAME = "Pad"
SKETCH_NAME = "Sketch"


def _make_body_doc(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[_FakeDoc, _FakeBody, _FakePad]:
    """Create a saved document containing a Body with a Pad as its Tip."""
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    body = doc.addObject("PartDesign::Body", BODY_NAME)
    body.Tip = None
    pad = doc.addObject("PartDesign::Pad", PAD_NAME)
    body.addObject(pad)
    body.Tip = pad
    # Persist so _open_existing_document finds the .fcstd file on reopen.
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))
    return doc, body, pad


# ---------------------------------------------------------------------------
# get_body_tip
# ---------------------------------------------------------------------------


def test_get_body_tip_returns_current_tip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body, pad = _make_body_doc(monkeypatch, tmp_path)

    result = runner._op_get_body_tip(
        {"document_id": DOC_ID, "body_name": BODY_NAME, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "get_body_tip"
    assert result["result"]["document_id"] == DOC_ID
    assert result["result"]["body_name"] == BODY_NAME
    assert result["result"]["tip_name"] == PAD_NAME
    assert result["result"]["tip_type"] == "PartDesign::Pad"
    assert result["result"]["tip_shape"]["valid"] is True
    assert result["result"]["tip_shape"]["solids_count"] == 1


def test_get_body_tip_no_tip_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    body = doc.addObject("PartDesign::Body", BODY_NAME)
    body.Tip = None
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_get_body_tip(
        {"document_id": DOC_ID, "body_name": BODY_NAME, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["result"]["tip_name"] is None
    assert result["result"]["tip_type"] is None
    assert result["result"]["tip_shape"] is None


def test_get_body_tip_rejects_missing_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_get_body_tip(
        {"document_id": DOC_ID, "body_name": "NoSuchBody", "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "get_body_tip"
    assert "not found" in result["error"]


def test_get_body_tip_rejects_non_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_get_body_tip(
        {"document_id": DOC_ID, "body_name": "Box1", "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "get_body_tip"
    assert "PartDesign::Body" in result["error"]


# ---------------------------------------------------------------------------
# set_body_tip
# ---------------------------------------------------------------------------


def test_set_body_tip_success(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    body = doc.addObject("PartDesign::Body", BODY_NAME)
    body.Tip = None
    pad = doc.addObject("PartDesign::Pad", PAD_NAME)
    body.addObject(pad)
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_set_body_tip(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "tip_name": PAD_NAME,
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is True
    assert result["op"] == "set_body_tip"
    assert result["result"]["tip_name"] == PAD_NAME
    assert result["result"]["previous_tip_name"] is None
    assert body.Tip is pad


def test_set_body_tip_rejects_missing_tip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body, pad = _make_body_doc(monkeypatch, tmp_path)
    previous_tip = body.Tip

    result = runner._op_set_body_tip(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "tip_name": "NoSuchTip",
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "set_body_tip"
    assert "not found" in result["error"]
    # Previous Tip preserved.
    assert body.Tip is previous_tip


def test_set_body_tip_rejects_foreign_tip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body, pad = _make_body_doc(monkeypatch, tmp_path)
    # A sketch that exists in the document but is NOT a member of the Body's Group.
    doc.addObject("Sketcher::SketchObject", SKETCH_NAME)
    previous_tip = body.Tip

    result = runner._op_set_body_tip(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "tip_name": SKETCH_NAME,
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "set_body_tip"
    assert "not a member" in result["error"]
    # Previous Tip preserved.
    assert body.Tip is previous_tip


def test_set_body_tip_rejects_non_solid_tip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body, pad = _make_body_doc(monkeypatch, tmp_path)
    # Add a sketch to the document AND to the Body's Group (a member, but not a
    # solid-producing Tip).
    sketch = doc.addObject("Sketcher::SketchObject", SKETCH_NAME)
    body.addObject(sketch)
    previous_tip = body.Tip

    result = runner._op_set_body_tip(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "tip_name": SKETCH_NAME,
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "set_body_tip"
    assert "no shape" in result["error"] or "invalid shape" in result["error"]
    # Previous Tip preserved (rollback).
    assert body.Tip is previous_tip
    assert body.Tip is pad


def test_set_body_tip_rejects_non_body_target(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_set_body_tip(
        {
            "document_id": DOC_ID,
            "body_name": "Box1",
            "tip_name": "Box1",
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "set_body_tip"
    assert "PartDesign::Body" in result["error"]


# ---------------------------------------------------------------------------
# Real-FreeCAD 1.1.4 integration tests (skipped without freecadcmd)
# ---------------------------------------------------------------------------


def _freecad_run_code(code: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a snippet of FreeCAD Python in a fresh freecadcmd process."""
    return subprocess.run(
        [str(_DISCOVERED), "-c", code],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _build_body_pad_code(fcstd_win: str) -> str:
    """FreeCAD snippet: new doc with Body + sketch + Pad, save, close."""
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "import Part\n"
        "from Part import LineSegment\n"
        "from FreeCAD import Vector\n"
        "doc = App.newDocument(\"TipLife\")\n"
        "body = doc.addObject(\"PartDesign::Body\", \"Body\")\n"
        "sketch = doc.addObject(\"Sketcher::SketchObject\", \"Sketch\")\n"
        "sketch.addGeometry(LineSegment(Vector(0,0,0), Vector(10,0,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(10,0,0), Vector(10,10,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(10,10,0), Vector(0,10,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(0,10,0), Vector(0,0,0)))\n"
        "body.addObject(sketch)\n"
        "pad = doc.addObject(\"PartDesign::Pad\", \"Pad1\")\n"
        "body.addObject(pad)\n"
        "pad.Profile = sketch\n"
        "pad.Length = 10.0\n"
        "doc.recompute()\n"
        "doc.saveAs(" + path + ")\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"BUILD_OK\")\n"
    )


def _add_second_pad_code(fcstd_win: str) -> str:
    """FreeCAD snippet: open doc, add a second Pad, verify Tip advances, save, close."""
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "import Part\n"
        "from Part import LineSegment\n"
        "from FreeCAD import Vector\n"
        "doc = App.openDocument(" + path + ")\n"
        "body = doc.getObject(\"Body\")\n"
        "pad1 = doc.getObject(\"Pad1\")\n"
        "sketch = doc.getObject(\"Sketch\")\n"
        "pad2 = doc.addObject(\"PartDesign::Pad\", \"Pad2\")\n"
        "body.addObject(pad2)\n"
        "pad2.Profile = sketch\n"
        "pad2.Length = 5.0\n"
        "doc.recompute()\n"
        "assert body.Tip is pad2, \"expected Tip=Pad2\"\n"
        "assert pad2.Shape is not None and pad2.Shape.isValid()\n"
        "assert len(list(pad2.Shape.Solids)) == 1\n"
        "doc.saveAs(" + path + ")\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"ADVANCE_OK\")\n"
    )


def _restore_tip_code(fcstd_win: str) -> str:
    """FreeCAD snippet: open doc, set Tip back to Pad1, save, close."""
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "doc = App.openDocument(" + path + ")\n"
        "body = doc.getObject(\"Body\")\n"
        "pad1 = doc.getObject(\"Pad1\")\n"
        "body.Tip = pad1\n"
        "doc.recompute()\n"
        "assert body.Tip is pad1, \"expected Tip=Pad1\"\n"
        "assert pad1.Shape is not None and pad1.Shape.isValid()\n"
        "doc.saveAs(" + path + ")\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"RESTORE_OK\")\n"
    )


def _verify_tip_code(fcstd_win: str, expected_tip: str) -> str:
    """FreeCAD snippet: open doc, verify Tip name and that its shape is a valid solid."""
    path = repr(fcstd_win)
    tip_repr = repr(expected_tip)
    return (
        "import FreeCAD as App\n"
        "doc = App.openDocument(" + path + ")\n"
        "body = doc.getObject(\"Body\")\n"
        "tip = body.Tip\n"
        "assert tip is not None, \"Body.Tip is None\"\n"
        "assert tip.Name == " + tip_repr + ", \"expected Tip=\" + " + tip_repr + "\n"
        "assert tip.Shape is not None and tip.Shape.isValid(), \"Tip shape is null or invalid\"\n"
        "assert len(list(tip.Shape.Solids)) == 1, \"Tip shape has no solids\"\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"VERIFY_OK\")\n"
    )


@requires_freecad
def test_real_body_tip_lifecycle_and_persistence(tmp_path: Path) -> None:
    """End-to-end Body.Tip lifecycle against real FreeCAD 1.1.4.

    a. Create a Body and Pad.
    b. Verify the initial Tip.
    c. Add another valid PartDesign feature and verify automatic Tip advancement.
    d. Explicitly restore the previous valid Tip.
    e. Verify Tip and Shape after recompute.
    f. Save, close and reopen; verify Tip persistence.
    """
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "tiplifecycle.fcstd", target)
    doc_id = "tiplifecycle"

    bridge = FreeCadBridge.from_settings(_settings())

    # a + b: build Body + Pad; initial Tip is the Pad.
    proc = _freecad_run_code(_build_body_pad_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    tip = bridge.run(
        "get_body_tip",
        {"document_id": doc_id, "body_name": BODY_NAME, "workspace": str(tmp_path)},
    )
    assert tip["tip_name"] == "Pad1", tip
    assert tip["tip_type"] == "PartDesign::Pad"
    assert tip["tip_shape"]["valid"] is True
    assert tip["tip_shape"]["solids_count"] == 1

    # c: add a second valid PartDesign feature; Tip advances automatically.
    proc = _freecad_run_code(_add_second_pad_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ADVANCE_OK" in proc.stdout, proc.stdout + proc.stderr

    tip = bridge.run(
        "get_body_tip",
        {"document_id": doc_id, "body_name": BODY_NAME, "workspace": str(tmp_path)},
    )
    assert tip["tip_name"] == "Pad2", tip

    # d: explicitly restore the previous valid Tip (Pad1).
    result = bridge.run(
        "set_body_tip",
        {
            "document_id": doc_id,
            "body_name": BODY_NAME,
            "tip_name": "Pad1",
            "workspace": str(tmp_path),
        },
    )
    assert result["tip_name"] == "Pad1"
    assert result["previous_tip_name"] == "Pad2"

    # e: verify Tip and Shape after recompute.
    tip = bridge.run(
        "get_body_tip",
        {"document_id": doc_id, "body_name": BODY_NAME, "workspace": str(tmp_path)},
    )
    assert tip["tip_name"] == "Pad1"
    assert tip["tip_shape"]["valid"] is True
    assert tip["tip_shape"]["solids_count"] == 1

    proc = _freecad_run_code(_verify_tip_code(fcstd_win, "Pad1"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY_OK" in proc.stdout, proc.stdout + proc.stderr

    # f: save, close and reopen; verify Tip persistence.
    proc = _freecad_run_code(_verify_tip_code(fcstd_win, "Pad1"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY_OK" in proc.stdout, proc.stdout + proc.stderr

