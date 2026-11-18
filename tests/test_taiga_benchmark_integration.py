"""Opt-in real Taiga + FreeCAD integration test for the benchmark infrastructure.

Skipped unless ``FREECAD_MCP_FREECAD_EXECUTABLE``, ``FREECAD_MCP_TAIGA_SOURCE``,
``FREECAD_MCP_TAIGA_PYTHON`` and ``FREECAD_MCP_TAIGA_MODEL`` are set.  Exercises
the full M20 Phase 4B.1 path: ``taiga_benchmark`` → isolated torch child
(``runner.py`` ``run_episode`` op with ``save_path``) → ``NativeWorkerEnv`` →
``freecadcmd`` native ``Worker.serve()`` → native ``run_episodes`` loop →
native ``save`` op → fresh-process ``validate_reopen`` → independent validation.

The parent test module never imports ``freecad_s1``, ``torch`` or ``FreeCAD``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from freecad_mcp.config import Settings, load_settings
from freecad_mcp.tools.taiga import taiga_benchmark


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _skip_unless_configured() -> Settings:
    missing = [
        name
        for name in (
            "FREECAD_MCP_FREECAD_EXECUTABLE",
            "FREECAD_MCP_TAIGA_SOURCE",
            "FREECAD_MCP_TAIGA_PYTHON",
            "FREECAD_MCP_TAIGA_MODEL",
        )
        if not _env(name)
    ]
    if missing:
        pytest.skip(
            "Set " + ", ".join(missing) + " to run the M20 Phase 4B.1 native "
            "benchmark integration test."
        )
    return load_settings()


def _box_goal() -> dict:
    """B1: base_box 40x30x8, volume=9600, bbox=(40,30,8)."""
    return {
        "features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}],
        "scale": 40.0,
    }


def test_benchmark_geometry_success(tmp_path: Path) -> None:
    """B1 base_box reaches geometry_success with save/reopen validation."""
    settings = _skip_unless_configured()
    result = taiga_benchmark(
        settings,
        "b1_box",
        _box_goal(),
        expected_volume_mm3=9600.0,
        expected_bbox_mm=[40.0, 30.0, 8.0],
        workspace=str(tmp_path),
    )

    assert result.classification in ("geometry_success", "save_reopen_failure"), (
        f"unexpected classification: {result.classification}; "
        f"episode={result.episode}; validation={result.independent_validation}; "
        f"save_reopen={result.save_reopen}"
    )
    assert result.episode["success"] is True
    assert result.episode["iou"] >= 0.99
    assert result.independent_validation["checks_passed"] is True
    assert result.independent_validation["shape_valid"] is True
    assert result.independent_validation["solid_count"] == 1
    assert result.independent_validation["volume_mm3"] is not None
    # Volume should be within 1% of 9600.
    vol = result.independent_validation["volume_mm3"]
    assert abs(vol - 9600.0) / 9600.0 * 100.0 < 1.0
    # Save/reopen
    assert result.save_reopen["attempted"] is True
    assert result.save_reopen["artifact_written"] is True
    if result.save_reopen["reopened_in_fresh_process"]:
        assert result.save_reopen["reopen_matches_in_memory"] is True


def test_benchmark_invalid_goal(tmp_path: Path) -> None:
    """A malformed goal is classified as invalid_goal."""
    settings = _skip_unless_configured()
    result = taiga_benchmark(
        settings,
        "bad_goal",
        {"features": []},
        workspace=str(tmp_path),
    )
    assert result.classification == "invalid_goal"
    assert result.episode["status"] == "invalid_goal"


def test_benchmark_artifact_saved_in_workspace(tmp_path: Path) -> None:
    """The benchmark saves an artifact inside the workspace benchmarks/ dir."""
    settings = _skip_unless_configured()
    result = taiga_benchmark(
        settings,
        "b1_box_save",
        _box_goal(),
        expected_volume_mm3=9600.0,
        expected_bbox_mm=[40.0, 30.0, 8.0],
        workspace=str(tmp_path),
    )

    assert result.save_reopen["artifact_written"] is True
    saved = result.save_reopen["saved_path"]
    assert saved is not None
    saved_path = Path(saved)
    assert saved_path.is_file(), f"artifact not found at {saved_path}"
    assert saved_path.suffix == ".fcstd"
    # The artifact must be inside a benchmarks/ subdir of the workspace.
    assert "benchmarks" in saved_path.parts
    assert str(tmp_path) in str(saved_path)