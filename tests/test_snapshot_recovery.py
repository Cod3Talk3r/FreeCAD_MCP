"""Focused tests for snapshot recovery / rollback.

Tests cover snapshot restoration, preservation snapshots, validation failure
rollback, missing/corrupt snapshots, and revision metadata consistency.
Mock tests run without FreeCAD using fakes from ``tests.test_runner``.
Real-FreeCAD integration tests are skipped when the freecad console
executable is unavailable.

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
from freecad_mcp.freecad_bridge.errors import FreeCadOperationError
from freecad_mcp.pathing import for_target

from tests.test_runner import (
    _FakeDoc,
    _FakeFreeCad,
    _install_fake_freecad,
    _make_save_doc,
)

DOC_ID = "restore_doc"


# ---------------------------------------------------------------------------
# Workspace-level restore_snapshot helper
# ---------------------------------------------------------------------------


def _setup_saved_doc(
    tmp_path: Path, doc_id: str = DOC_ID, revision: int = 1
) -> None:
    """Create a saved .fcstd file and meta sidecar at the given revision."""
    fcstd_path = tmp_path / f"{doc_id}.fcstd"
    fcstd_path.write_bytes(b"fake fcstd content")
    ws_mod.write_meta(
        tmp_path / f"{doc_id}.meta.json",
        {"revision": revision, "document_id": doc_id},
    )


def test_workspace_restore_snapshot_success(tmp_path: Path) -> None:
    """restore_snapshot atomically replaces current doc with snapshot."""
    wks = ws_mod.Workspace(root=tmp_path)
    _setup_saved_doc(tmp_path, DOC_ID, revision=5)

    # Create snapshots at revisions 3 and 5
    ws_mod.create_snapshot(wks, DOC_ID, 3)
    ws_mod.create_snapshot(wks, DOC_ID, 5)

    result = ws_mod.restore_snapshot(wks, DOC_ID, 3)

    assert result["ok"] is True
    assert result["document_id"] == DOC_ID
    assert result["previous_revision"] == 5
    assert result["restored_revision"] == 6
    assert result["snapshot_revision"] == 3
    assert result["restored_from_snapshot"] is True

    # Current .fcstd should now match snapshot r3
    fcstd = tmp_path / f"{DOC_ID}.fcstd"
    snapshot_fcstd = tmp_path / "snapshots" / f"{DOC_ID}.r3.fcstd"
    assert fcstd.read_bytes() == snapshot_fcstd.read_bytes()

    # Meta revision should be bumped to 6
    meta = json.loads((tmp_path / f"{DOC_ID}.meta.json").read_text())
    assert meta["revision"] == 6
    assert meta["restored_from_revision"] == 3
    assert meta["restored_from_snapshot"] is True


def test_workspace_restore_snapshot_missing_snapshot(tmp_path: Path) -> None:
    """restore_snapshot returns error when snapshot does not exist."""
    wks = ws_mod.Workspace(root=tmp_path)
    _setup_saved_doc(tmp_path, DOC_ID, revision=1)

    result = ws_mod.restore_snapshot(wks, DOC_ID, 99)

    assert result["ok"] is False
    assert "not found" in result["error"].lower()
    # Current document must be untouched
    fcstd = tmp_path / f"{DOC_ID}.fcstd"
    assert fcstd.read_bytes() == b"fake fcstd content"
    meta = json.loads((tmp_path / f"{DOC_ID}.meta.json").read_text())
    assert meta["revision"] == 1


def test_workspace_restore_snapshot_preserves_current_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """restore_snapshot creates a preservation snapshot before overwriting."""
    wks = ws_mod.Workspace(root=tmp_path)
    _setup_saved_doc(tmp_path, DOC_ID, revision=2)

    # Create a snapshot at revision 1 to restore from
    ws_mod.create_snapshot(wks, DOC_ID, 1)

    result = ws_mod.restore_snapshot(wks, DOC_ID, 1)

    assert result["ok"] is True
    preservation = result["preservation_snapshot"]
    assert preservation is not None
    assert preservation["ok"] is True
    assert preservation["revision"] == 2

    # Preservation snapshot file should exist
    pres_fcstd = tmp_path / "snapshots" / f"{DOC_ID}.r2.fcstd"
    assert pres_fcstd.is_file()


def test_workspace_restore_snapshot_no_current_document(tmp_path: Path) -> None:
    """restore_snapshot works when no current .fcstd exists (fresh restore)."""
    wks = ws_mod.Workspace(root=tmp_path)
    # Create .fcstd and meta first, then a snapshot, then remove .fcstd
    # to simulate no current document.
    fcstd_path = tmp_path / f"{DOC_ID}.fcstd"
    fcstd_path.write_bytes(b"fake fcstd content")
    ws_mod.write_meta(
        tmp_path / f"{DOC_ID}.meta.json", {"revision": 1, "document_id": DOC_ID}
    )
    ws_mod.create_snapshot(wks, DOC_ID, 1)
    fcstd_path.unlink()  # remove current document

    result = ws_mod.restore_snapshot(wks, DOC_ID, 1)

    assert result["ok"] is True
    assert result["previous_revision"] == 1
    assert result["restored_revision"] == 2

    # Document now exists
    fcstd = tmp_path / f"{DOC_ID}.fcstd"
    assert fcstd.is_file()


# ---------------------------------------------------------------------------
# Runner-level operation handler
# ---------------------------------------------------------------------------


def _install_and_save_with_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    doc_id: str = DOC_ID,
    current_revision: int = 1,
    snapshot_revisions: list[int] | None = None,
) -> None:
    """Install fake FreeCAD, create saved doc with snapshots."""
    doc = _FakeDoc()
    doc.Name = doc_id
    _install_fake_freecad(monkeypatch, doc)
    runner._op_create_document(
        {"document_id": doc_id, "workspace": str(tmp_path)}
    )
    # Write the .fcstd file and meta at the given revision
    fcstd_path = tmp_path / f"{doc_id}.fcstd"
    fcstd_path.write_bytes(b"fake fcstd")
    ws_mod.write_meta(
        tmp_path / f"{doc_id}.meta.json",
        {"revision": current_revision, "document_id": doc_id},
    )
    # Create snapshots
    for rev in snapshot_revisions or []:
        ws_mod.create_snapshot(ws_mod.Workspace(root=tmp_path), doc_id, rev)


def test_op_restore_snapshot_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_restore_snapshot returns ok=True with restoration info."""
    _install_and_save_with_snapshots(
        tmp_path, monkeypatch, current_revision=3, snapshot_revisions=[1, 2, 3]
    )

    result = runner._op_restore_snapshot(
        {"document_id": DOC_ID, "revision": 2, "workspace": str(tmp_path)}
    )

    assert result["ok"] is True
    assert result["op"] == "restore_snapshot"
    assert result["result"]["document_id"] == DOC_ID
    assert result["result"]["previous_revision"] == 3
    assert result["result"]["restored_revision"] == 4
    assert result["result"]["snapshot_revision"] == 2
    assert result["result"]["restored_from_snapshot"] is True
    assert result["result"]["validation"]["all_passed"] is True

    # Current .fcstd should match snapshot r2
    fcstd = tmp_path / f"{DOC_ID}.fcstd"
    snapshot_fcstd = tmp_path / "snapshots" / f"{DOC_ID}.r2.fcstd"
    assert fcstd.read_bytes() == snapshot_fcstd.read_bytes()


def test_op_restore_snapshot_missing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_restore_snapshot returns error when snapshot does not exist."""
    _install_and_save_with_snapshots(
        tmp_path, monkeypatch, current_revision=1, snapshot_revisions=[1]
    )

    result = runner._op_restore_snapshot(
        {"document_id": DOC_ID, "revision": 99, "workspace": str(tmp_path)}
    )

    assert result["ok"] is False
    assert result["op"] == "restore_snapshot"
    assert "not found" in result["error"].lower()
    # Current document untouched
    meta = json.loads((tmp_path / f"{DOC_ID}.meta.json").read_text())
    assert meta["revision"] == 1


def test_op_restore_snapshot_invalid_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_restore_snapshot returns error for invalid revision values."""
    _install_and_save_with_snapshots(
        tmp_path, monkeypatch, current_revision=1, snapshot_revisions=[1]
    )

    result = runner._op_restore_snapshot(
        {"document_id": DOC_ID, "revision": -1, "workspace": str(tmp_path)}
    )
    assert result["ok"] is False
    assert "non-negative" in result["error"].lower()

    result = runner._op_restore_snapshot(
        {"document_id": DOC_ID, "revision": "not_an_int", "workspace": str(tmp_path)}
    )
    assert result["ok"] is False


def test_op_restore_snapshot_rollback_on_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_op_restore_snapshot rolls back when validation fails."""
    _install_and_save_with_snapshots(
        tmp_path, monkeypatch, current_revision=2, snapshot_revisions=[1]
    )

    # Override _op_validate_reopen to return a failure
    import freecad_mcp.freecad_bridge.runner as runner_mod

    original_validate_reopen = runner_mod._op_validate_reopen

    def failing_validate(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "op": "validate_reopen",
            "result": {
                "all_passed": False,
                "failures": ["object 'Pad' type changed"],
                "object_count": 2,
            },
        }

    runner_mod._op_validate_reopen = failing_validate
    try:
        result = runner._op_restore_snapshot(
            {"document_id": DOC_ID, "revision": 1, "workspace": str(tmp_path)}
        )
    finally:
        runner_mod._op_validate_reopen = original_validate_reopen

    assert result["ok"] is False
    assert result["op"] == "restore_snapshot"
    assert result["rolled_back"] is True
    assert "validation" in result["error"].lower()

    # After rollback, revision is bumped (previous 3 + 1 = 4).
    # Document content is restored from preservation snapshot (r2 state).
    meta = json.loads((tmp_path / f"{DOC_ID}.meta.json").read_text())
    assert meta["revision"] == 4


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
        "doc = App.newDocument(\"RestoreLife\")\n"
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
def test_real_restore_snapshot_and_rollback(tmp_path: Path) -> None:
    """Verify snapshot restore and rollback against real FreeCAD 1.1.4.

    Builds a Body/Pad document, saves it twice (creating snapshots at r1
    and r2), then restores from r1 via MCP.  Verifies the restored document
    preserves the r1 state and that save/reopen still works afterward.
    """
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "restorelife.fcstd", target)
    doc_id = "restorelife"

    bridge = FreeCadBridge.from_settings(_settings())

    # Build document (creates .fcstd at revision 1 via create_document semantics)
    proc = _freecad_run_code(_build_box_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "BUILD_OK" in proc.stdout, proc.stdout + proc.stderr

    # Write meta sidecar at revision 1
    ws_mod.write_meta(
        tmp_path / f"{doc_id}.meta.json",
        {"revision": 1, "document_id": doc_id, "created_at": "2026-01-01T00:00:00Z"},
    )

    # Save again via MCP: snapshots r1, then saves -> revision 2
    result1 = bridge.run(
        "save_document",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )
    assert result1 is not None

    # Verify snapshot at r1 exists
    snapshot_r1 = tmp_path / "snapshots" / f"{doc_id}.r1.fcstd"
    assert snapshot_r1.is_file(), f"snapshot r1 missing: {snapshot_r1}"

    # Restore from snapshot r1
    restore_result = bridge.run(
        "restore_snapshot",
        {"document_id": doc_id, "revision": 1, "workspace": str(tmp_path)},
    )
    assert restore_result is not None
    assert restore_result.get("previous_revision") == 2, restore_result
    assert restore_result.get("restored_revision") == 3, restore_result
    assert restore_result.get("snapshot_revision") == 1, restore_result
    assert restore_result.get("restored_from_snapshot") is True, restore_result
    assert restore_result.get("validation", {}).get("all_passed") is True, restore_result

    # Verify current .fcstd matches snapshot r1
    current_fcstd = tmp_path / f"{doc_id}.fcstd"
    assert current_fcstd.read_bytes() == snapshot_r1.read_bytes()

    # Verify save/reopen still works after restore
    result2 = bridge.run(
        "save_document",
        {"document_id": doc_id, "workspace": str(tmp_path)},
    )
    assert result2 is not None
    meta = json.loads((tmp_path / f"{doc_id}.meta.json").read_text())
    assert meta["revision"] == 4


@requires_freecad
def test_real_restore_missing_snapshot_returns_error(tmp_path: Path) -> None:
    """Verify restoring a missing snapshot returns a controlled error."""
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd_win = for_target(tmp_path / "missinglife.fcstd", target)
    doc_id = "missinglife"

    bridge = FreeCadBridge.from_settings(_settings())

    proc = _freecad_run_code(_build_box_doc_code(fcstd_win))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    ws_mod.write_meta(
        tmp_path / f"{doc_id}.meta.json",
        {"revision": 1, "document_id": doc_id},
    )

    # No snapshots exist; restore from revision 5 should raise FreeCadOperationError
    with pytest.raises(FreeCadOperationError) as exc_info:
        bridge.run(
            "restore_snapshot",
            {"document_id": doc_id, "revision": 5, "workspace": str(tmp_path)},
        )
    assert "not found" in str(exc_info.value).lower()
