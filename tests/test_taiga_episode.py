"""Unit tests for the parent-side EpisodeRuntime (no FreeCAD required).

Exercises :class:`EpisodeRuntime` with a mocked child runner: input validation,
timeout, process-failure classification, and result mapping.  The opt-in
real-Taiga integration test lives in ``test_taiga_native_episode_integration.py``.
"""

from __future__ import annotations

import subprocess
from typing import Any, Mapping

import pytest

from freecad_mcp.integrations.taiga.episode import (
    EpisodeRuntime,
    EpisodeResult,
)
from freecad_mcp.integrations.taiga.goal import InvalidGoalError
from freecad_mcp.integrations.taiga.runtime import (
    TaigaRuntimeExecutionError,
    TaigaRuntimeOperationError,
    TaigaRuntimeTimeoutError,
)


def _make_runtime(
    runner: Any,
    *,
    timeout: float = 30.0,
) -> EpisodeRuntime:
    return EpisodeRuntime(child_runner=runner, timeout=timeout)


def _valid_goal() -> dict[str, Any]:
    return {
        "features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}],
        "scale": 40.0,
    }


def test_prepare_request_valid() -> None:
    rt = _make_runtime(lambda *a, **k: {})
    req = rt.prepare_request(_valid_goal(), {"doc_open": True})
    assert req.goal["features"][0]["kind"] == "base_box"
    assert req.start["doc_open"] is True


def test_prepare_request_invalid_goal() -> None:
    rt = _make_runtime(lambda *a, **k: {})
    with pytest.raises(InvalidGoalError):
        rt.prepare_request({"features": []})


def test_run_invalid_goal_returns_status() -> None:
    rt = _make_runtime(lambda *a, **k: {})
    result = rt.run(
        {"features": []},
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.ok is False
    assert result.status == "invalid_goal"


def test_run_missing_config_returns_unavailable() -> None:
    rt = _make_runtime(lambda *a, **k: {})
    result = rt.run(_valid_goal(), freecad_executable=None, taiga_source="src", model="m")
    assert result.status == "taiga_unavailable"


def test_run_success_maps_result() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        assert op == "run_episode"
        return {
            "success": True,
            "clean": True,
            "iou": 0.997,
            "steps": 3,
            "budget": 200,
            "expert_steps": 3,
            "agreement": 0.66,
            "deviations": 1,
            "outcome": "success",
            "features": ["base_box"],
            "rule": None,
            "history": None,
            "final_state": {"doc_open": True, "tree": []},
        }

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        {"doc_open": True, "workbench": "PartDesignWorkbench"},
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert isinstance(result, EpisodeResult)
    assert result.ok is True
    assert result.status == "success"
    assert result.iou == 0.997
    assert result.match is True
    assert result.steps == 3
    assert result.budget == 200
    assert result.features == ["base_box"]
    assert result.final_state == {"doc_open": True, "tree": []}


def test_run_wrong_geometry_not_ok() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        return {
            "success": False,
            "clean": False,
            "iou": 0.1,
            "steps": 200,
            "budget": 200,
            "expert_steps": 3,
            "agreement": 0.0,
            "deviations": 200,
            "outcome": "wrong_geometry",
            "features": ["base_box"],
            "rule": None,
            "history": ["PartDesign_Body", "Done"],
            "final_state": {"doc_open": True},
        }

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.ok is False
    assert result.status == "wrong_geometry"
    assert result.match is False
    assert result.history == ["PartDesign_Body", "Done"]


def test_run_timeout_exception() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        raise subprocess.TimeoutExpired(cmd="taiga", timeout=timeout)

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.status == "timeout"
    assert result.error_type == "TimeoutError"


def test_run_runtime_timeout_exception() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        raise TaigaRuntimeTimeoutError("did not finish", timeout=timeout)

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.status == "timeout"


def test_run_operation_error_classified() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        raise TaigaRuntimeOperationError("Invalid goal from schema", error_type="InvalidRequest")

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.status == "invalid_goal"
    assert result.error_type == "InvalidRequest"


def test_run_execution_error() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        raise TaigaRuntimeExecutionError("child died", returncode=1, stderr="boom")

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.status == "child_process_failure"
    assert result.error_type == "TaigaRuntimeExecutionError"


def test_run_non_mapping_response() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> Any:
        return "not a dict"

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.status == "invalid_response"


def test_run_worker_error_classified_as_crashed() -> None:
    def fake_runner(op: str, params: Mapping[str, Any], *, timeout: float) -> dict[str, Any]:
        raise TaigaRuntimeOperationError("worker exited", error_type="WorkerError")

    rt = _make_runtime(fake_runner)
    result = rt.run(
        _valid_goal(),
        freecad_executable="fc",
        taiga_source="src",
        model="m",
    )
    assert result.status == "crashed"