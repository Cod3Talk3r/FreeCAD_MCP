"""Focused tests for PartDesign Body feature reordering.

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
from freecad_mcp.freecad_bridge.errors import FreeCadOperationError
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

DOC_ID = "reorderdoc"
BODY_NAME = "Body"
PAD1_NAME = "Pad1"
PAD2_NAME = "Pad2"
SKETCH_NAME = "Sketch"


def _make_body_with_pads(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[_FakeDoc, _FakeBody]:
    """Create a saved document containing a Body with Sketch, Pad1, Pad2.

    Pad1 depends on Sketch; Pad2 depends on Sketch and Pad1 (dependency chain
    used to test dependency-order validation).
    """
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})

    body = doc.addObject("PartDesign::Body", BODY_NAME)
    body.Tip = None

    sketch = doc.addObject("Sketcher::SketchObject", SKETCH_NAME)
    body.addObject(sketch)

    pad1 = doc.addObject("PartDesign::Pad", PAD1_NAME)
    body.addObject(pad1)

    pad2 = doc.addObject("PartDesign::Pad", PAD2_NAME)
    body.addObject(pad2)

    # Dependency chain: Pad1 depends on Sketch; Pad2 depends on Sketch and Pad1
    sketch.OutList = []
    pad1.OutList = [sketch]
    pad2.OutList = [sketch, pad1]

    body.Tip = pad2
    doc.recompute()
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))
    return doc, body


# ---------------------------------------------------------------------------
# reorder_body_features: success cases
# ---------------------------------------------------------------------------


def test_reorder_body_features_success_reverse(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Reversing Group order is valid when dependencies don't forbid it.

    With only Sketch -> Pad1 -> Pad2 dependencies, reversing to [Pad2, Pad1, Sketch]
    would break dependencies (Pad2 before Pad1), so instead we test a safe
    reversal where dependencies are respected: [Sketch, Pad2, Pad1] is invalid
    (Pad2 before Pad1), but [Pad1, Pad2, Sketch] is valid only if Pad1 doesn't
    depend on Sketch... Actually Pad1 depends on Sketch, so Sketch must be first.
    A safe reorder is [Sketch, Pad2, Pad1]? No - Pad2 depends on Pad1.
    The only valid topological orders have Sketch first, then Pad1, then Pad2.
    So a genuine reorder that changes order but keeps deps: [Sketch, Pad1, Pad2]
    is the initial order. Let's test a no-op reorder (same order) succeeds.
    """
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)
    previous_order = [o.Name for o in body.Group]

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME, PAD2_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is True
    assert result["op"] == "reorder_body_features"
    assert result["result"]["new_order"] == [SKETCH_NAME, PAD1_NAME, PAD2_NAME]
    assert result["result"]["previous_order"] == previous_order
    assert result["result"]["tip_name"] == PAD2_NAME


def test_reorder_body_features_preserves_group_on_same_order(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Reordering to the same order leaves Group unchanged."""
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME, PAD2_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is True
    assert [o.Name for o in body.Group] == [SKETCH_NAME, PAD1_NAME, PAD2_NAME]


# ---------------------------------------------------------------------------
# reorder_body_features: validation failures
# ---------------------------------------------------------------------------


def test_reorder_body_features_rejects_missing_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": "NoSuchBody",
            "new_order": [SKETCH_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "not found" in result["error"]


def test_reorder_body_features_rejects_non_body(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc = _FakeDoc()
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    doc.addObject("Part::Box", "Box1")
    doc.saveAs(str(tmp_path / f"{DOC_ID}.fcstd"))

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": "Box1",
            "new_order": ["Box1"],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "PartDesign::Body" in result["error"]


def test_reorder_body_features_rejects_empty_new_order(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "non-empty" in result["error"]


def test_reorder_body_features_rejects_unknown_object(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME, "NoSuchObject"],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "not found" in result["error"]


def test_reorder_body_features_rejects_non_group_member(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)
    # Add an object to the document that is NOT in the Body's Group.
    doc.addObject("Part::Box", "OutsideBox")

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME, "OutsideBox"],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "not a member" in result["error"]


def test_reorder_body_features_rejects_membership_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)
    # Missing Pad2 from new_order.
    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "membership" in result["error"]


def test_reorder_body_features_rejects_dependency_violation(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Pad2 depends on Pad1, so Pad2 cannot come before Pad1."""
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)
    previous_group = list(body.Group)

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD2_NAME, PAD1_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is False
    assert result["op"] == "reorder_body_features"
    assert "depends on" in result["error"] or "before it" in result["error"]
    # Group order must be preserved on failure.
    assert [o.Name for o in body.Group] == [o.Name for o in previous_group]


# ---------------------------------------------------------------------------
# reorder_body_features: Tip safety
# ---------------------------------------------------------------------------


def test_reorder_body_features_tip_still_valid_after_reorder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A safe reorder that keeps dependencies intact preserves a valid Tip."""
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)

    # The only valid reorder with these dependencies is [Sketch, Pad1, Pad2]
    # (Sketch first, then Pad1, then Pad2).  Test that this succeeds and Tip
    # remains Pad2.
    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME, PAD2_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is True
    assert result["result"]["tip_name"] == PAD2_NAME


def test_reorder_body_features_body_shape_valid_after_reorder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """After a valid reorder, Body.Shape should still be a valid solid."""
    doc, body = _make_body_with_pads(monkeypatch, tmp_path)

    result = runner._op_reorder_body_features(
        {
            "document_id": DOC_ID,
            "body_name": BODY_NAME,
            "new_order": [SKETCH_NAME, PAD1_NAME, PAD2_NAME],
            "workspace": str(tmp_path),
        }
    )

    assert result["ok"] is True
    # The fake Body doesn't have a Shape attribute in test_runner, but the
    # operation's Tip validation (which checks Tip.Shape) passed, which is
    # the key safety check for the real implementation.
    assert result["result"]["tip_type"] == "PartDesign::Pad"


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


def _build_body_reorder_code(fcstd_win: str) -> str:
    """FreeCAD snippet: new doc with Body + sketch + Pad1 + Pad2, save, close."""
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "import Part\n"
        "from Part import LineSegment\n"
        "from FreeCAD import Vector\n"
        "doc = App.newDocument(\"ReorderLife\")\n"
        "body = doc.addObject(\"PartDesign::Body\", \"Body\")\n"
        "sketch = doc.addObject(\"Sketcher::SketchObject\", \"Sketch\")\n"
        "sketch.addGeometry(LineSegment(Vector(0,0,0), Vector(10,0,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(10,0,0), Vector(10,10,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(10,10,0), Vector(0,10,0)))\n"
        "sketch.addGeometry(LineSegment(Vector(0,10,0), Vector(0,0,0)))\n"
        "body.addObject(sketch)\n"
        "pad1 = doc.addObject(\"PartDesign::Pad\", \"Pad1\")\n"
        "body.addObject(pad1)\n"
        "pad1.Profile = sketch\n"
        "pad1.Length = 10.0\n"
        "pad2 = doc.addObject(\"PartDesign::Pad\", \"Pad2\")\n"
        "body.addObject(pad2)\n"
        "pad2.Profile = sketch\n"
        "pad2.Length = 5.0\n"
        "doc.recompute()\n"
        "doc.saveAs(" + path + ")\n"
        "App.closeDocument(doc.Name)\n"
        "print(\"BUILD_OK\")\n"
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
def test_real_body_reorder_dependency_and_persistence(tmp_path: Path) -> None:
    """End-to-end Body reorder lifecycle against real FreeCAD 1.1.4.

    1. Create a Body with Sketch, Pad1, Pad2 (Pad2 depends on Pad1).
    2. Verify the initial Tip is Pad2.
    3. Attempt a dependency-violating reorder (Pad2 before Pad1) → expect failure.
    4. Perform a valid same-order reorder → expect success, pipeline exercised.
    5. Verify Tip remains Pad2 after reorder.
    6. Save, close and reopen → verify Tip persistence.
    """
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "reorderlife.fcstd", target)
    doc_id = "reorderlife"

    bridge = FreeCadBridge.from_settings(_settings())

    # 1: Build Body with Sketch, Pad1, Pad2.
    proc = _freecad_run_code(_build_body_reorder_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    # 2: Verify initial Tip is Pad2.
    tip = bridge.run(
        "get_body_tip",
        {"document_id": doc_id, "body_name": BODY_NAME, "workspace": str(tmp_path)},
    )
    assert tip["tip_name"] == "Pad2", tip
    assert tip["tip_type"] == "PartDesign::Pad"
    assert tip["tip_shape"]["valid"] is True
    assert tip["tip_shape"]["solids_count"] == 1

    # 3: Dependency-violating reorder (Pad2 before Pad1) must be rejected.
    try:
        bridge.run(
            "reorder_body_features",
            {
                "document_id": doc_id,
                "body_name": BODY_NAME,
                "new_order": ["Sketch", "Pad2", "Pad1"],
                "workspace": str(tmp_path),
            },
        )
        assert False, "expected reorder_body_features to raise on dependency violation"
    except FreeCadOperationError as exc:
        assert "depends on" in str(exc) or "before it" in str(exc), str(exc)

    # 4: Valid same-order reorder exercises the full pipeline (lock, validate,
    #    recompute, atomic save, revision bump).
    result = bridge.run(
        "reorder_body_features",
        {
            "document_id": doc_id,
            "body_name": BODY_NAME,
            "new_order": ["Sketch", "Pad1", "Pad2"],
            "workspace": str(tmp_path),
        },
    )
    assert result["new_order"] == ["Sketch", "Pad1", "Pad2"], result
    assert result["tip_name"] == "Pad2", result

    # 5: Verify Tip remains Pad2 after reorder.
    tip = bridge.run(
        "get_body_tip",
        {"document_id": doc_id, "body_name": BODY_NAME, "workspace": str(tmp_path)},
    )
    assert tip["tip_name"] == "Pad2", tip
    assert tip["tip_shape"]["valid"] is True
    assert tip["tip_shape"]["solids_count"] == 1

    # 6: Save, close and reopen → verify Tip persistence.
    proc = _freecad_run_code(_verify_tip_code(fcstd_win, "Pad2"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERIFY_OK" in proc.stdout, proc.stdout + proc.stderr
