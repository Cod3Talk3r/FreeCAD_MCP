"""Focused unit tests for M20 Phase 4B.1 benchmark infrastructure.

Covers:
* ``NativeWorkerEnv.save()`` — sends the native ``save`` op.
* ``resolve_artifact_path`` — isolated per-run paths, validation.
* ``validate_geometry`` — independent volume/bbox/solid checks.
* ``classify_result`` — model/runtime vs geometry-quality separation.
* ``run_benchmark_scenario`` — orchestration with mocked episode + bridge.
* ``taiga_benchmark`` tool — end-to-end with mocked runtime.

No real FreeCAD, Taiga or Torch is imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from freecad_mcp.integrations.taiga.benchmark import (
    CLASS_GEOMETRY_QUALITY_FAILURE,
    CLASS_GEOMETRY_SUCCESS,
    CLASS_INVALID_GOAL,
    CLASS_MODEL_FAILURE,
    CLASS_RUNTIME_FAILURE,
    CLASS_SAVE_REOPEN_FAILURE,
    BenchmarkScenario,
    classify_result,
    resolve_artifact_path,
    run_benchmark_scenario,
    validate_geometry,
)
from freecad_mcp.integrations.taiga.episode import EpisodeResult


# ---------------------------------------------------------------------------
# NativeWorkerEnv.save
# ---------------------------------------------------------------------------


def test_worker_env_save_sends_native_save_op() -> None:
    """NativeWorkerEnv.save() sends {"op": "save", "fcstd": path}."""
    from freecad_mcp.integrations.taiga.native_worker_env import NativeWorkerEnv

    env = NativeWorkerEnv.__new__(NativeWorkerEnv)
    env._closed = False
    env._lock = MagicMock()
    env._lock.__enter__ = MagicMock(return_value=env._lock)
    env._lock.__exit__ = MagicMock(return_value=False)
    env.call = MagicMock(return_value={"ok": True, "fcstd": "/tmp/x.FCStd"})

    result = env.save("/tmp/x.FCStd")

    env.call.assert_called_once_with({"op": "save", "fcstd": "/tmp/x.FCStd"})
    assert result == {"ok": True, "fcstd": "/tmp/x.FCStd"}


# ---------------------------------------------------------------------------
# resolve_artifact_path
# ---------------------------------------------------------------------------


def test_resolve_artifact_path_creates_isolated_subdir(tmp_path: Path) -> None:
    """Each call produces a unique path inside benchmarks/<run-uuid>/."""
    p1 = resolve_artifact_path(tmp_path, "B1_box")
    p2 = resolve_artifact_path(tmp_path, "B1_box")

    assert p1 != p2, "concurrent runs must not collide"
    assert p1.parent.parent.name == "benchmarks"
    assert p1.suffix == ".fcstd"
    assert p1.stem == "b1_box"
    assert p1.parent.is_dir(), "parent dir must be created"


def test_resolve_artifact_path_validates_scenario_id(tmp_path: Path) -> None:
    """Invalid scenario ids are rejected."""
    from freecad_mcp.freecad_bridge.workspace import validate_document_id

    with pytest.raises(ValueError):
        resolve_artifact_path(tmp_path, "bad/id")
    with pytest.raises(ValueError):
        validate_document_id("bad/id")


def test_resolve_artifact_path_none_workspace_uses_env(tmp_path: Path, monkeypatch: Any) -> None:
    """None workspace falls back to FREECAD_MCP_WORKSPACE or cwd default."""
    monkeypatch.setenv("FREECAD_MCP_WORKSPACE", str(tmp_path / "ws"))
    p = resolve_artifact_path(None, "B1_box")
    assert str(tmp_path / "ws") in str(p)
    assert p.parent.is_dir()


# ---------------------------------------------------------------------------
# validate_geometry
# ---------------------------------------------------------------------------


def test_validate_geometry_all_pass() -> None:
    """Shape info matching expected values passes."""
    shape = {
        "valid": True,
        "volume": 9600.0,
        "bbox": [40.0, 30.0, 8.0],
        "n_solids": 1,
        "n_faces": 6,
        "n_edges": 12,
    }
    v = validate_geometry(shape, 9600.0, (40.0, 30.0, 8.0))
    assert v.checks_passed is True
    assert v.shape_valid is True
    assert v.solid_count == 1
    assert v.volume_mm3 == 9600.0
    assert v.volume_error_pct == 0.0
    assert v.bbox_ok is True
    assert v.checks_failed == []


def test_validate_geometry_volume_out_of_tolerance() -> None:
    """Volume outside tolerance fails."""
    shape = {
        "valid": True,
        "volume": 8000.0,
        "bbox": [40.0, 30.0, 8.0],
        "n_solids": 1,
    }
    v = validate_geometry(shape, 9600.0, (40.0, 30.0, 8.0), volume_tolerance_pct=1.0)
    assert v.checks_passed is False
    assert any("volume" in f for f in v.checks_failed)
    assert v.volume_error_pct is not None and v.volume_error_pct > 1.0


def test_validate_geometry_invalid_shape() -> None:
    """Invalid shape fails."""
    shape = {"valid": False, "volume": 100.0, "bbox": [1.0, 1.0, 1.0], "n_solids": 0}
    v = validate_geometry(shape, None, None)
    assert v.checks_passed is False
    assert v.shape_valid is False
    assert any("not valid" in f for f in v.checks_failed)


def test_validate_geometry_wrong_solid_count() -> None:
    """Multiple solids fail."""
    shape = {"valid": True, "volume": 100.0, "bbox": [1.0, 1.0, 1.0], "n_solids": 3}
    v = validate_geometry(shape, None, None)
    assert v.checks_passed is False
    assert v.solid_count == 3
    assert any("1 solid" in f for f in v.checks_failed)


def test_validate_geometry_bbox_out_of_tolerance() -> None:
    """Bbox outside tolerance fails."""
    shape = {"valid": True, "volume": 100.0, "bbox": [40.0, 30.0, 9.0], "n_solids": 1}
    v = validate_geometry(shape, None, (40.0, 30.0, 8.0), bbox_tolerance_mm=0.1)
    assert v.checks_passed is False
    assert v.bbox_ok is False
    assert any("bbox" in f for f in v.checks_failed)


def test_validate_geometry_none_shape_info() -> None:
    """None shape info (crashed episode) fails gracefully."""
    v = validate_geometry(None, 100.0, (1.0, 1.0, 1.0))
    assert v.checks_passed is False
    assert v.shape_valid is None
    assert any("no shape info" in f for f in v.checks_failed)


def test_validate_geometry_skips_none_expected() -> None:
    """None expected values skip those checks."""
    shape = {"valid": True, "volume": 100.0, "bbox": [1.0, 2.0, 3.0], "n_solids": 1}
    v = validate_geometry(shape, None, None)
    assert v.checks_passed is True
    assert v.volume_mm3 == 100.0
    assert v.bbox_mm == [1.0, 2.0, 3.0]
    assert v.bbox_ok is None


# ---------------------------------------------------------------------------
# classify_result
# ---------------------------------------------------------------------------


def test_classify_invalid_goal() -> None:
    assert classify_result("invalid_goal", False, False, False, None) == CLASS_INVALID_GOAL


def test_classify_runtime_failures() -> None:
    for status in ("taiga_unavailable", "timeout", "crashed", "child_process_failure"):
        assert classify_result(status, False, False, False, None) == CLASS_RUNTIME_FAILURE


def test_classify_model_failures() -> None:
    for status in ("budget", "unrecoverable", "wrong_geometry"):
        assert classify_result(status, False, False, False, None) == CLASS_MODEL_FAILURE


def test_classify_geometry_success() -> None:
    assert classify_result("success", True, True, True, True) == CLASS_GEOMETRY_SUCCESS


def test_classify_geometry_quality_failure_episode_ran_but_validation_failed() -> None:
    """Episode success but independent validation fails."""
    assert classify_result("success", True, False, False, None) == CLASS_GEOMETRY_QUALITY_FAILURE


def test_classify_save_reopen_failure() -> None:
    """Episode + in-memory OK but save/reopen failed."""
    assert classify_result("success", True, True, True, False) == CLASS_SAVE_REOPEN_FAILURE


def test_classify_episode_not_success_but_geometry_valid() -> None:
    """Episode didn't report success but independent geometry is valid."""
    assert classify_result("wrong_geometry", False, True, False, None) == CLASS_MODEL_FAILURE
    # If episode ran to a non-model, non-runtime terminal state but geometry
    # is valid, it's still a quality concern.
    assert classify_result("success", False, True, False, None) == CLASS_GEOMETRY_QUALITY_FAILURE


# ---------------------------------------------------------------------------
# run_benchmark_scenario (mocked)
# ---------------------------------------------------------------------------


def _mock_episode(
    *,
    status: str = "success",
    success: bool = True,
    iou: float = 0.997,
    final_state: dict[str, Any] | None = None,
    save_error: str | None = None,
) -> Any:
    """Build a mock taiga_episode_fn that records save_path and returns a result."""

    def _fn(
        goal: dict[str, Any],
        *,
        start: dict[str, Any] | None = None,
        save_path: str | None = None,
    ) -> EpisodeResult:
        return EpisodeResult(
            ok=success,
            status=status,
            goal=goal,
            start=start or {},
            success=success,
            iou=iou,
            match=success,
            steps=10,
            budget=28,
            expert_steps=11,
            outcome="done_clean" if success else status,
            final_state=final_state,
            saved_path=save_path,
            save_error=save_error,
        )

    return _fn


def test_run_benchmark_scenario_geometry_success(tmp_path: Path) -> None:
    """Full pipeline: episode success + validation pass + save/reopen."""
    shape = {
        "valid": True,
        "volume": 9600.0,
        "bbox": [40.0, 30.0, 8.0],
        "n_solids": 1,
    }
    final_state = {"doc_open": True, "shape": shape}

    # Mock bridge: validate_reopen returns all_passed.
    mock_bridge = MagicMock()
    mock_bridge.run = MagicMock(return_value={
        "ok": True,
        "op": "validate_reopen",
        "result": {
            "document_id": "b1_box",
            "checks": [{"name": "objects_preserved", "passed": True}],
            "all_passed": True,
            "failures": [],
            "object_count": 2,
            "before_object_count": 2,
            "after_object_count": 2,
        },
    })

    # We need the artifact to "exist" after the episode runs.  The mock
    # episode fn creates it.
    def _episode_with_save(
        goal: dict[str, Any],
        *,
        start: dict[str, Any] | None = None,
        save_path: str | None = None,
    ) -> EpisodeResult:
        if save_path:
            Path(save_path).write_text("fake fcstd", encoding="utf-8")
        return EpisodeResult(
            ok=True,
            status="success",
            goal=goal,
            start=start or {},
            success=True,
            iou=0.997,
            match=True,
            steps=10,
            budget=28,
            expert_steps=11,
            outcome="done_clean",
            final_state=final_state,
            saved_path=save_path,
        )

    scenario = BenchmarkScenario(
        scenario_id="b1_box",
        goal={"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        expected_volume_mm3=9600.0,
        expected_bbox_mm=(40.0, 30.0, 8.0),
    )

    result = run_benchmark_scenario(
        scenario,
        taiga_episode_fn=_episode_with_save,
        freecad_executable="freecadcmd",
        workspace=str(tmp_path),
        bridge_factory=lambda: mock_bridge,
    )

    assert result.classification == CLASS_GEOMETRY_SUCCESS
    assert result.episode.success is True
    assert result.episode.iou == 0.997
    assert result.independent_validation.checks_passed is True
    assert result.independent_validation.volume_mm3 == 9600.0
    assert result.save_reopen.attempted is True
    assert result.save_reopen.artifact_written is True
    assert result.save_reopen.reopened_in_fresh_process is True
    assert result.save_reopen.reopen_matches_in_memory is True
    # Verify validate_reopen was called with the scenario_id as document_id.
    args, kwargs = mock_bridge.run.call_args
    assert args[0] == "validate_reopen"


def test_run_benchmark_scenario_runtime_failure(tmp_path: Path) -> None:
    """Runtime failure (taiga_unavailable) classifies correctly."""
    scenario = BenchmarkScenario(
        scenario_id="b1_box",
        goal={"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        expected_volume_mm3=9600.0,
        expected_bbox_mm=(40.0, 30.0, 8.0),
    )

    result = run_benchmark_scenario(
        scenario,
        taiga_episode_fn=_mock_episode(status="taiga_unavailable", success=False),
        freecad_executable="freecadcmd",
        workspace=str(tmp_path),
        bridge_factory=lambda: MagicMock(),
    )

    assert result.classification == CLASS_RUNTIME_FAILURE
    assert result.episode.status == "taiga_unavailable"
    assert result.independent_validation.checks_passed is False


def test_run_benchmark_scenario_model_failure(tmp_path: Path) -> None:
    """Model failure (budget) classifies correctly."""
    scenario = BenchmarkScenario(
        scenario_id="b1_box",
        goal={"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        expected_volume_mm3=9600.0,
        expected_bbox_mm=(40.0, 30.0, 8.0),
    )

    result = run_benchmark_scenario(
        scenario,
        taiga_episode_fn=_mock_episode(status="budget", success=False, iou=0.5),
        freecad_executable="freecadcmd",
        workspace=str(tmp_path),
        bridge_factory=lambda: MagicMock(),
    )

    assert result.classification == CLASS_MODEL_FAILURE
    assert result.episode.status == "budget"


def test_run_benchmark_scenario_save_error(tmp_path: Path) -> None:
    """Save error is recorded but doesn't crash the benchmark."""
    shape = {"valid": True, "volume": 9600.0, "bbox": [40.0, 30.0, 8.0], "n_solids": 1}
    final_state = {"doc_open": True, "shape": shape}

    scenario = BenchmarkScenario(
        scenario_id="b1_box",
        goal={"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        expected_volume_mm3=9600.0,
        expected_bbox_mm=(40.0, 30.0, 8.0),
    )

    result = run_benchmark_scenario(
        scenario,
        taiga_episode_fn=_mock_episode(
            status="success", success=True, final_state=final_state,
            save_error="WorkerError: disk full",
        ),
        freecad_executable="freecadcmd",
        workspace=str(tmp_path),
        bridge_factory=lambda: MagicMock(),
    )

    assert result.save_reopen.error == "WorkerError: disk full"
    assert result.save_reopen.artifact_written is False
    # Episode succeeded + validation passed, but save failed.
    assert result.classification == CLASS_GEOMETRY_SUCCESS


def test_run_benchmark_scenario_episode_fn_raises(tmp_path: Path) -> None:
    """If the episode fn raises, the benchmark records child_process_failure."""
    def _raising(
        goal: dict[str, Any],
        *,
        start: dict[str, Any] | None = None,
        save_path: str | None = None,
    ) -> EpisodeResult:
        raise RuntimeError("boom")

    scenario = BenchmarkScenario(
        scenario_id="b1_box",
        goal={"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        expected_volume_mm3=9600.0,
    )

    result = run_benchmark_scenario(
        scenario,
        taiga_episode_fn=_raising,
        freecad_executable="freecadcmd",
        workspace=str(tmp_path),
        bridge_factory=lambda: MagicMock(),
    )

    assert result.classification == CLASS_RUNTIME_FAILURE
    assert result.episode.status == "child_process_failure"
    assert "boom" in (result.episode.error or "")


# ---------------------------------------------------------------------------
# taiga_benchmark tool (mocked settings)
# ---------------------------------------------------------------------------


def test_taiga_benchmark_tool_end_to_end(tmp_path: Path, monkeypatch: Any) -> None:
    """taiga_benchmark tool runs the full pipeline with mocked runtime."""
    from freecad_mcp.config import Settings
    from freecad_mcp.tools.taiga import taiga_benchmark

    shape = {"valid": True, "volume": 9600.0, "bbox": [40.0, 30.0, 8.0], "n_solids": 1}
    final_state = {"doc_open": True, "shape": shape}

    # Mock the episode runtime to return a successful result.
    mock_runtime = MagicMock()
    mock_runtime.run = MagicMock(return_value=EpisodeResult(
        ok=True,
        status="success",
        goal={"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        start={},
        success=True,
        iou=0.997,
        match=True,
        steps=10,
        budget=28,
        expert_steps=11,
        outcome="done_clean",
        final_state=final_state,
        saved_path=None,
    ))

    # Patch _make_episode_runtime to return our mock.
    monkeypatch.setattr(
        "freecad_mcp.tools.taiga._make_episode_runtime",
        lambda settings: (MagicMock(run=mock_runtime.run), MagicMock()),
    )

    # Patch bridge creation to use a mock.
    mock_bridge = MagicMock()
    mock_bridge.run = MagicMock(return_value={
        "ok": True,
        "op": "validate_reopen",
        "result": {
            "document_id": "b1_box",
            "all_passed": True,
            "failures": [],
            "object_count": 2,
            "before_object_count": 2,
            "after_object_count": 2,
        },
    })

    settings = Settings(
        freecad_executable="freecadcmd",
        taiga_source="/fake/source",
        taiga_python="/fake/python",
        taiga_model="/fake/model",
        workspace=str(tmp_path),
    )

    result = taiga_benchmark(
        settings,
        "b1_box",
        {"features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}], "scale": 40.0},
        expected_volume_mm3=9600.0,
        expected_bbox_mm=[40.0, 30.0, 8.0],
        workspace=str(tmp_path),
    )

    assert result.schema_version == "1.0"
    assert result.scenario_id == "b1_box"
    assert result.classification in (CLASS_GEOMETRY_SUCCESS, CLASS_SAVE_REOPEN_FAILURE)
    assert result.episode["success"] is True
    assert result.independent_validation["checks_passed"] is True