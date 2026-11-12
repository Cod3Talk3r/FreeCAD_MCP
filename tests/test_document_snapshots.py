"""Focused tests for revision snapshots.

Tests cover snapshot creation, listing, retention policy, and integration
with save_document.  Mock tests run without FreeCAD using fakes from
``tests.test_runner``.  Real-FreeCAD integration tests are skipped when the
freecad console executable is unavailable.

Test filenames, function names, and descriptions use feature-based names;
no milestone identifiers appear anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.freecad_bridge import runner
from freecad_mcp.freecad_bridge import workspace as ws_mod
from freecad_mcp.pathing import for_target

from tests.test_runner import (
    _FakeDoc,
    _FakeFreeCad,
    _install_fake_freecad,
    _make_save_doc,
)

DOC_ID = "snapshot_doc"


# ---------------------------------------------------------------------------
# Workspace-level snapshot helpers
# ---------------------------------------------------------------------------


def test_workspace_create_snapshot_success(tmp_path: Path) -> None:
    """create_snapshot copies .fcstd and meta sidecar to snapshots dir."""
    wks = ws_mod.Workspace(root=tmp_path)
    fcstd_path = wks.document_path(DOC_ID)
    fcstd_path.write_bytes(b"fake fcstd content")
    meta_path = wks.meta_path(DOC_ID)
    ws_mod.write_meta(meta_path, {"revision": 3, "document_id": DOC_ID})

    result = ws_mod.create_snapshot(wks, DOC_ID, 3)

    assert result["ok"] is True
    assert result["document_id"] == DOC_ID
    assert result["revision"] == 3
    snapshot_path = wks.root / "snapshots" / f"{DOC_ID}.r3.fcstd"
    snapshot_meta = wks.root / "snapshots" / f"{DOC_ID}.r3.meta.json"
    assert snapshot_path.is_file()
    assert snapshot_meta.is_file()
    assert snapshot_path.read_bytes() == b"fake fcstd content"
    snap_meta = json.loads(snapshot_meta.read_text())
    assert snap_meta["revision"] == 3
    assert snap_meta["document_id"] == DOC_ID
    assert snap_meta["source_revision"] == 3


def test_workspace_create_snapshot_missing_document(tmp_path: Path) -> None:
    """create_snapshot returns error when no .fcstd file exists."""
    wks = ws_mod.Workspace(root=tmp_path)
    result = ws_mod.create_snapshot(wks, DOC_ID, 1)
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_workspace_list_snapshots_empty(tmp_path: Path) -> None:
    """list_snapshots returns empty list when no snapshots exist."""
    wks = ws_mod.Workspace(root=tmp_path)
    snapshots = ws_mod.list_snapshots(wks, DOC_ID)
    assert snapshots == []


def test_workspace_list_snapshots_sorted_ascending(tmp_path: Path) -> None:
    """list_snapshots returns snapshots sorted by revision ascending."""
    wks = ws_mod.Workspace(root=tmp_path)
    snaps_dir = ws_mod.snapshots_dir(wks)
    snaps_dir.mkdir(parents=True, exist_ok=True)

    # Create snapshots out of order: r5, r1, r3
    for rev in (5, 1, 3):
        fcstd = ws_mod.snapshot_path(wks, DOC_ID, rev)
        fcstd.write_bytes(b"data")
        meta = ws_mod.snapshot_meta_path(wks, DOC_ID, rev)
        ws_mod.write_meta(meta, {"revision": rev, "document_id": DOC_ID})

    snapshots = ws_mod.list_snapshots(wks, DOC_ID)
    assert [s["revision"] for s in snapshots] == [1, 3, 5]
    assert len(snapshots) == 3
    for s in snapshots:
        assert s["document_id"] == DOC_ID
        assert Path(s["snapshot_path"]).is_file()
        assert Path(s["meta_path"]).is_file()


def test_workspace_create_snapshot_retention_deletes_oldest(tmp_path: Path) -> None:
    """Creating more than MAX_SNAPSHOTS_PER_DOCUMENT deletes the oldest."""
    wks = ws_mod.Workspace(root=tmp_path)
    fcstd_path = wks.document_path(DOC_ID)
    fcstd_path.write_bytes(b"fake fcstd")
    meta_path = wks.meta_path(DOC_ID)
    ws_mod.write_meta(meta_path, {"revision": 1, "document_id": DOC_ID})

    max_snaps = ws_mod.MAX_SNAPSHOTS_PER_DOCUMENT
    # Create snapshots at revisions 1..max_snaps+1
    for rev in range(1, max_snaps + 2):
        ws_mod.create_snapshot(wks, DOC_ID, rev)

    snapshots = ws_mod.list_snapshots(wks, DOC_ID)
    revisions = [s["revision"] for s in snapshots]
    assert len(revisions) == max_snaps
    # Oldest (revision 1) should have been deleted
    assert 1 not in revisions
    # The rest should be present
    assert set(revisions) == set(range(2, max_snaps + 2))


# ---------------------------------------------------------------------------
# Runner-level operation handlers
# ---------------------------------------------------------------------------


def _install_and_save(doc_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revision: int = 0) -> _FakeDoc:
    """Helper: install fake FreeCAD, create saved doc with given revision."""
    doc = _FakeDoc()
    doc.Name = doc_id
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": doc_id, "workspace": str(tmp_path)})
    # Write the .fcstd file and meta at the given revision
    fcstd_path = tmp_path / f"{doc_id}.fcstd"
    fcstd_path.write_bytes(b"fake fcstd")
    ws_mod.write_meta(tmp_path / f"{doc_id}.meta.json", {"revision": revision, "document_id": doc_id})
    return doc


def test_op_create_snapshot_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_create_snapshot returns ok=True with snapshot info."""
    _install_and_save(DOC_ID, tmp_path, monkeypatch, revision=3)

    result = runner._op_create_snapshot(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "create_snapshot"
    assert result["result"]["document_id"] == DOC_ID
    assert result["result"]["revision"] == 3
    snapshot_path = Path(result["result"]["snapshot_path"])
    meta_path = Path(result["result"]["meta_path"])
    assert snapshot_path.is_file()
    assert meta_path.is_file()


def test_op_create_snapshot_missing_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_create_snapshot returns error when no .fcstd file exists."""
    doc = _FakeDoc()
    doc.Name = DOC_ID
    _install_fake_freecad(monkeypatch, doc)
    # No .fcstd file written

    result = runner._op_create_snapshot(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "create_snapshot"
    assert "not found" in result["error"].lower()


def test_op_list_snapshots_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_list_snapshots returns list of snapshots sorted by revision."""
    _install_and_save(DOC_ID, tmp_path, monkeypatch, revision=1)
    # Manually create snapshots at revisions 1 and 3
    ws_mod.create_snapshot(ws_mod.Workspace(root=tmp_path), DOC_ID, 1)
    ws_mod.create_snapshot(ws_mod.Workspace(root=tmp_path), DOC_ID, 3)

    result = runner._op_list_snapshots(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "list_snapshots"
    assert result["result"]["document_id"] == DOC_ID
    assert result["result"]["count"] == 2
    revisions = [s["revision"] for s in result["result"]["snapshots"]]
    assert revisions == [1, 3]


def test_op_list_snapshots_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_list_snapshots returns empty list when no snapshots exist."""
    _install_and_save(DOC_ID, tmp_path, monkeypatch, revision=1)

    result = runner._op_list_snapshots(
        {"document_id": DOC_ID, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["result"]["snapshots"] == []
    assert result["result"]["count"] == 0


# ---------------------------------------------------------------------------
# Integration with save_document: snapshot created before overwrite
# ---------------------------------------------------------------------------


def test_save_document_creates_snapshot_of_previous_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_document snapshots the current revision before overwriting."""
    doc = _FakeDoc()
    doc.Name = DOC_ID
    fc = _FakeFreeCad(doc=doc)
    fc.ActiveDocument = doc
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document({"document_id": DOC_ID, "workspace": str(tmp_path)})

    # After create_document, revision is already 1. First save_document:
    # current_revision is 1, so snapshot at r1 is created before overwriting.
    result1 = runner._op_save_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    assert result1["ok"] is True
    meta1 = json.loads((tmp_path / f"{DOC_ID}.meta.json").read_text())
    assert meta1["revision"] == 2

    # Snapshot at revision 1 should exist (preserved before overwrite)
    snapshot_fcstd = tmp_path / "snapshots" / f"{DOC_ID}.r1.fcstd"
    snapshot_meta = tmp_path / "snapshots" / f"{DOC_ID}.r1.meta.json"
    assert snapshot_fcstd.is_file()
    assert snapshot_meta.is_file()
    snap_meta = json.loads(snapshot_meta.read_text())
    assert snap_meta["revision"] == 1
    assert snap_meta["source_revision"] == 1

    # Second save_document: current_revision is 2, snapshot at r2.
    result2 = runner._op_save_document({"document_id": DOC_ID, "workspace": str(tmp_path)})
    assert result2["ok"] is True
    meta2 = json.loads((tmp_path / f"{DOC_ID}.meta.json").read_text())
    assert meta2["revision"] == 3

    snapshot_fcstd_r2 = tmp_path / "snapshots" / f"{DOC_ID}.r2.fcstd"
    assert snapshot_fcstd_r2.is_file()


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
        "doc = App.newDocument(\"SnapshotLife\")\n"
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
def test_real_snapshot_creation_and_listing(tmp_path: Path) -> None:
    """Verify snapshots are created on save and listed correctly via MCP.

    Builds a Body/Pad document with FreeCAD, writes a meta sidecar at
    revision 1, then calls save_document through the MCP layer twice.
    Each save snapshots the previous revision before overwriting.  Then
    lists snapshots and confirms revision 1 snapshot exists and persists.
    """
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "snapshotlife.fcstd", target)
    doc_id = "snapshotlife"

    bridge = FreeCadBridge.from_settings(_settings())

    # Build and save the document directly with FreeCAD (creates .fcstd only)
    proc = _freecad_run_code(_build_box_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    # Write meta sidecar at revision 1 (as create_document would)
    ws_mod.write_meta(
        tmp_path / f"{doc_id}.meta.json",
        {"revision": 1, "document_id": doc_id, "created_at": "2026-01-01T00:00:00Z"},
    )

    # First save_document: current_revision=1, snapshot at r1, then save -> revision 2
    result1 = bridge.run(
        "save_document",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )
    assert result1 is not None
    meta_after_1 = json.loads((tmp_path / f"{doc_id}.meta.json").read_text())
    assert meta_after_1["revision"] == 2

    # Verify snapshot at revision 1 was created
    snapshot_r1 = tmp_path / "snapshots" / f"{doc_id}.r1.fcstd"
    snapshot_r1_meta = tmp_path / "snapshots" / f"{doc_id}.r1.meta.json"
    assert snapshot_r1.is_file(), f"snapshot r1 .fcstd missing: {snapshot_r1}"
    assert snapshot_r1_meta.is_file(), f"snapshot r1 .meta.json missing: {snapshot_r1_meta}"
    snap_meta = json.loads(snapshot_r1_meta.read_text())
    assert snap_meta["revision"] == 1
    assert snap_meta["source_revision"] == 1

    # Second save_document: current_revision=2, snapshot at r2, then save -> revision 3
    result2 = bridge.run(
        "save_document",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )
    assert result2 is not None
    meta_after_2 = json.loads((tmp_path / f"{doc_id}.meta.json").read_text())
    assert meta_after_2["revision"] == 3

    snapshot_r2 = tmp_path / "snapshots" / f"{doc_id}.r2.fcstd"
    assert snapshot_r2.is_file(), f"snapshot r2 .fcstd missing: {snapshot_r2}"

    # List snapshots via MCP
    report = bridge.run(
        "list_snapshots",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )

    assert report is not None
    snapshots = report["snapshots"]
    assert len(snapshots) >= 2, f"expected at least 2 snapshots, got {snapshots}"
    revs = [s["revision"] for s in snapshots]
    assert revs == sorted(revs), f"snapshots not sorted: {revs}"
    assert 1 in revs, f"expected revision 1 snapshot, got {revs}"
    assert 2 in revs, f"expected revision 2 snapshot, got {revs}"


@requires_freecad
def test_real_retention_keeps_recent_snapshots(tmp_path: Path) -> None:
    """Verify retention policy: older snapshots deleted when limit exceeded."""
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "retention.fcstd", target)
    doc_id = "retention"

    bridge = FreeCadBridge.from_settings(_settings())

    # Build document and save it directly with FreeCAD
    proc = _freecad_run_code(_build_box_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    # Write meta sidecar at revision 1
    ws_mod.write_meta(
        tmp_path / f"{doc_id}.meta.json",
        {"revision": 1, "document_id": doc_id, "created_at": "2026-01-01T00:00:00Z"},
    )

    # Save multiple times through MCP to create multiple revisions (and snapshots)
    max_snaps = ws_mod.MAX_SNAPSHOTS_PER_DOCUMENT
    for i in range(2, max_snaps + 3):  # revisions 2..max_snaps+2
        result = bridge.run(
            "save_document",
            {"document_id": doc_id, "workspace": str(tmp_path)},
        )
        # bridge.run raises FreeCadOperationError on failure; success returns result dict
        assert result is not None

    # List snapshots - should be at most MAX_SNAPSHOTS_PER_DOCUMENT
    report = bridge.run(
        "list_snapshots",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )

    assert report is not None
    snapshots = report["snapshots"]
    assert len(snapshots) <= max_snaps, (
        f"expected <= {max_snaps} snapshots, got {len(snapshots)}: {snapshots}"
    )

    # Verify snapshots are sorted ascending
    revs = [s["revision"] for s in snapshots]
    assert revs == sorted(revs), f"snapshots not sorted: {revs}"

    # The oldest snapshot (revision 1) should have been deleted
    assert 1 not in revs, f"expected revision 1 to be pruned, got {revs}"
