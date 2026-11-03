"""Tests for the parent-side Taiga Build Runtime (Phase 2, child mocked)."""

from __future__ import annotations

import builtins
from typing import Any, Mapping

import pytest

from freecad_mcp.integrations.taiga.build import (
    BuildRequest,
    BuildResult,
    BuildRuntime,
    ChildProcessError,
    ExecutionError,
    FeasibilityError,
    InvalidResponseError,
    TaigaUnavailableError,
    TimeoutError,
)


def _valid_goal() -> dict[str, Any]:
    return {"features": [{"kind": "base_box", "params": {"w": 40, "d": 30, "h": 8}}]}


def _make_runtime(child_runner=None, timeout: float = 120.0) -> BuildRuntime:
    return BuildRuntime(child_runner=child_runner, timeout=timeout)


def _success_response(result: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": True, "result": result or {"status": "done"}}


def test_prepare_request_constructs_build_request() -> None:
    runtime = _make_runtime(timeout=60.0)
    request = runtime.prepare_request(_valid_goal())
    assert isinstance(request, BuildRequest)
    assert request.timeout == 60.0
    assert request.goal["features"][0]["kind"] == "base_box"


def test_prepare_request_propagates_invalid_goal() -> None:
    runtime = _make_runtime()
    with pytest.raises(ValueError, match="non-empty"):
        runtime.prepare_request({"features": []})


def test_run_invalid_goal_returns_invalid_result() -> None:
    runtime = _make_runtime()
    result = runtime.run({"features": []})
    assert isinstance(result, BuildResult)
    assert result.ok is False
    assert result.status == "invalid_goal"
    assert result.error_type == "InvalidGoalError"


def test_run_child_unavailable() -> None:
    runtime = _make_runtime(child_runner=None)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "taiga_unavailable"
    assert result.error_type == "TaigaUnavailableError"


def test_run_successful_mocked_child() -> None:
    def child(payload: Mapping[str, Any], timeout: float) -> dict[str, Any]:
        assert payload["op"] == "build"
        assert "goal" in payload["params"]
        assert timeout == 60.0
        return _success_response({"solid_count": 1})

    runtime = _make_runtime(child_runner=child, timeout=60.0)
    result = runtime.run(_valid_goal())
    assert result.ok is True
    assert result.status == "completed"
    assert result.result == {"solid_count": 1}


def test_run_child_missing_boolean_ok_is_invalid_response() -> None:
    def child(payload, timeout):
        return {"ok": "yes"}

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "invalid_response"
    assert result.error_type == "InvalidResponseError"


def test_run_child_non_mapping_response_is_invalid_response() -> None:
    def child(payload, timeout):
        return "not a mapping"

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "invalid_response"
    assert result.error_type == "InvalidResponseError"


def test_run_successful_child_without_result_is_invalid_response() -> None:
    def child(payload, timeout):
        return {"ok": True}

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "invalid_response"


def test_run_feasibility_failure_is_distinguishable() -> None:
    def child(payload, timeout):
        return {"ok": False, "error": {"type": "FeasibilityError", "message": "target unreachable"}}

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "feasibility_failure"
    assert result.error_type == "FeasibilityError"
    assert "target unreachable" in result.error


def test_run_execution_failure_is_distinguishable() -> None:
    def child(payload, timeout):
        return {"ok": False, "error": {"type": "ExecutionError", "message": "kernel crash"}}

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "execution_failure"
    assert result.error_type == "ExecutionError"


def test_run_string_error_becomes_execution_failure() -> None:
    def child(payload, timeout):
        return {"ok": False, "error": "boom"}

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.status == "execution_failure"
    assert result.error_type == "Error"


def test_run_timeout_is_reported_as_timeout() -> None:
    def child(payload, timeout):
        raise builtins.TimeoutError("timed out")

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "timeout"
    assert result.error_type == "TimeoutError"


def test_run_child_process_failure_is_reported() -> None:
    def child(payload, timeout):
        raise RuntimeError("child died")

    runtime = _make_runtime(child_runner=child)
    result = runtime.run(_valid_goal())
    assert result.ok is False
    assert result.status == "child_process_failure"
    assert result.error_type == "RuntimeError"


def test_run_non_positive_timeout_returns_invalid_result() -> None:
    def child(payload, timeout):
        return _success_response()

    runtime = _make_runtime(child_runner=child, timeout=60.0)
    result = runtime.run(_valid_goal(), timeout=0)
    assert result.ok is False
    assert result.status == "invalid_timeout"


def test_run_per_call_timeout_overrides_default() -> None:
    captured = []

    def child(payload, timeout):
        captured.append(timeout)
        return _success_response()

    runtime = _make_runtime(child_runner=child, timeout=120.0)
    runtime.run(_valid_goal(), timeout=30.0)
    assert captured == [30.0]


def test_error_classes_are_distinct_subclasses() -> None:
    assert issubclass(TaigaUnavailableError, Exception)
    assert issubclass(FeasibilityError, Exception)
    assert issubclass(ExecutionError, Exception)
    assert issubclass(TimeoutError, Exception)
    assert issubclass(ChildProcessError, Exception)
    assert issubclass(InvalidResponseError, Exception)
