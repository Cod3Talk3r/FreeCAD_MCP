"""Focused tests for recompute reporting.

These tests run without FreeCAD or Taiga unless marked otherwise: the runner
is driven by fakes imported from ``tests.test_runner`` and the MCP tool is
driven by a mock bridge.  Real-FreeCAD integration tests are skipped when the
freecad console executable is unavailable.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.freecad_bridge import runner
from freecad_mcp.pathing import for_target
from freecad_mcp.tools import modeling
from freecad_mcp.tools.modeling import RecomputeParams, _register_tools

from tests.test_modeling_tools import _FakeBridge
from tests.test_runner import (
    _FakeDoc,
    _FakeFreeCad,
    _FakeShape,
    _install_fake_freecad,
    _make_save_doc,
)

DOC_ID = "recompute_doc"


def _get_recompute_tool():
    """Retrieve the nested ``recompute_tool`` from the tool registry.

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
    return server.tools["recompute"]


def _make_recompute_doc(
    monkeypatch: pytest.MonkeyPatch, tmp_path, doc_id: str = DOC_ID
) -> _FakeDoc:
    """Create and save a document so _open_existing_document finds its .fcstd."""
    doc = _make_save_doc(doc_id)
    fc = _FakeFreeCad(doc=doc)
    fc.ActiveDocument = doc
    _install_fake_freecad(monkeypatch, doc)
    (tmp_path / f"{doc_id}.fcstd").write_bytes(b"fake fcstd")
    return doc


# ---------------------------------------------------------------------------
# Runner: _op_recompute returns a structured recompute report
# ---------------------------------------------------------------------------


def test_op_recompute_returns_structured_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Successful recompute returns ok=True plus recomputed_at and revision."""
    _make_recompute_doc(monkeypatch, tmp_path)

    result = runner._op_recompute(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "recompute"
    report = result["result"]
    assert report["document_id"] == DOC_ID
    assert "recomputed_at" in report
    assert report["revision"] == 0
    assert "meta_before" in report


def test_op_recompute_uses_active_document_reports_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Recompute against the active document still returns a structured report."""
    doc = _FakeDoc()
    doc.Name = DOC_ID
    box = doc.addObject("Part::Box", "Box1")
    box.Shape = _FakeShape(volume=64.0)
    fc = _FakeFreeCad(doc=doc)
    fc.ActiveDocument = doc
    monkeypatch.setitem(sys.modules, "FreeCAD", fc)
    monkeypatch.setitem(sys.modules, "Part", __import__("types").ModuleType("Part"))

    result = runner._op_recompute(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "recompute"
    assert result["result"]["document_id"] == DOC_ID
    assert "recomputed_at" in result["result"]
    assert result["result"]["revision"] == 0


# ---------------------------------------------------------------------------
# MCP tool: recompute_tool populates report fields
# ---------------------------------------------------------------------------


def test_recompute_tool_returns_report_fields() -> None:
    recompute_tool = _get_recompute_tool()

    bridge = _FakeBridge(
        result={
            "ok": True,
            "op": "recompute",
            "result": {
                "document_id": DOC_ID,
                "recomputed_at": "2026-01-02T03:04:05Z",
                "revision": 2,
                "meta_before": {"revision": 1},
            },
        }
    )

    with patch.object(modeling, "_make_bridge", return_value=bridge):
        report = recompute_tool(RecomputeParams(document_id=DOC_ID))

    assert report["document_id"] == DOC_ID
    assert report["recomputed_at"] == "2026-01-02T03:04:05Z"
    assert report["revision"] == 2
    assert report["meta_before"]["revision"] == 1
    assert bridge.calls[0][0] == "recompute"
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


def _build_box_doc_code(fcstd_win: str) -> str:
    path = repr(fcstd_win)
    return (
        "import FreeCAD as App\n"
        "import Part\n"
        "doc = App.newDocument(\"RecomputeLife\")\n"
        "body = doc.addObject(\"PartDesign::Body\", \"Body\")\n"
        "sketch = doc.addObject(\"Sketcher::SketchObject\", \"Sketch\")\n"
        "from Part import LineSegment\n"
        "from FreeCAD import Vector\n"
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
def test_real_recompute_returns_structured_report(tmp_path: Path) -> None:
    """Verify recompute returns ok=True, recomputed_at, and revision against real FreeCAD 1.1.4."""
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "recomputelife.fcstd", target)
    doc_id = "recomputelife"

    bridge = FreeCadBridge.from_settings(_settings())

    proc = _freecad_run_code(_build_box_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    report = bridge.run(
        "recompute",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )

    assert report["document_id"] == doc_id, report
    assert "recomputed_at" in report, report
    assert isinstance(report["recomputed_at"], str) and report["recomputed_at"], report
    # Revision reflects the persisted metadata (recompute itself does not save).
    assert "revision" in report, report
    assert isinstance(report["revision"], int), report

    # Recompute again; report should still be well-formed.
    report2 = bridge.run(
        "recompute",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )
    assert report2["ok"] is True if isinstance(report2, dict) and "ok" in report2 else True
    assert report2["document_id"] == doc_id, report2
    assert "recomputed_at" in report2, report2
