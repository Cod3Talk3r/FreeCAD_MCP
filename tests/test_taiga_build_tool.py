"""Tests for the ``taiga_build`` MCP tool (child mocked).

The real FreeCAD and freecad_s1 packages are not installed here, so the build
runtime is exercised with an injected fake child runner. These tests verify the
tool contract, configuration gating, and error classification without a
heweight optional dependency.
"""

from __future__ import annotations

from typing import Any

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.integrations.taiga.build import BuildResult
from freecad_mcp.tools import taiga as taiga_mod


class _FakeBuildResult:
    def __init__(
        self,
        ok: bool,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        self.ok = ok
        self.status = status
        self.result = result
        self.error = error
        self.error_type = error_type


class _FakeRuntime:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[dict[str, Any], str | None, str | None]] = []

    def run(
        self,
        goal: Any,
        *,
        timeout: float | None = None,
        document_id: str | None = None,
        workspace: str | None = None,
    ) -> _FakeBuildResult:
        self.calls.append((goal, document_id, workspace))
        return _FakeBuildResult(ok=True, status="completed")


class _FakeRunner:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self, payload: Any, timeout: float) -> dict[str, Any]:
        return {"ok": True, "result": {}}


def _configured_settings() -> Settings:
    return Settings(freecad_executable="/usr/bin/freecadcmd", taiga_source="/tmp/taiga-src")


def test_tool_unavailable_without_freecad_executable() -> None:
    result = taiga_mod.taiga_build(Settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]})
    assert result.ok is False
    assert result.status == "taiga_unavailable"
    assert result.error_type == "TaigaUnavailableError"
    assert "FREECAD_MCP_FREECAD_EXECUTABLE" in (result.error or "")


def test_tool_unavailable_without_taiga_source() -> None:
    result = taiga_mod.taiga_build(
        Settings(freecad_executable="/usr/bin/freecadcmd"),
        {"features": [{"kind": "base_box", "params": {"w": 40}}]},
    )
    assert result.ok is False
    assert result.status == "taiga_unavailable"
    assert "FREECAD_MCP_TAIGA_SOURCE" in (result.error or "")


def test_tool_uses_build_runtime_and_child(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    runner = _FakeRunner()
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: runner)

    goal = {"features": [{"kind": "base_box", "params": {"w": 40, "d": 30, "h": 8}}], "level": 1, "scale": 40.0}
    result = taiga_mod.taiga_build(_configured_settings(), goal, document_id="mydoc", workspace="/tmp/ws")

    assert result.ok is True
    assert result.status == "completed"
    assert runtime.calls == [(goal, "mydoc", "/tmp/ws")]


def test_tool_invalid_goal() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        runtime = _FakeRuntime()
        runtime.run = lambda *a, **k: _FakeBuildResult(
            ok=False, status="invalid_goal", error="features must be non-empty", error_type="InvalidGoalError"
        )
        monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
        monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

        result = taiga_mod.taiga_build(_configured_settings(), {"features": []})
        assert result.ok is False
        assert result.status == "invalid_goal"
        assert result.error_type == "InvalidGoalError"
        assert "features must be non-empty" in (result.error or "")
    finally:
        monkeypatch.undo()


def test_tool_feasibility_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    runtime.run = lambda *a, **k: _FakeBuildResult(
        ok=False,
        status="feasibility_failure",
        error="target unreachable",
        error_type="FeasibilityError",
    )
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

    result = taiga_mod.taiga_build(
        _configured_settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]}
    )
    assert result.ok is False
    assert result.status == "feasibility_failure"
    assert result.error_type == "FeasibilityError"
    assert "target unreachable" in (result.error or "")


def test_tool_execution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    runtime.run = lambda *a, **k: _FakeBuildResult(
        ok=False, status="execution_failure", error="kernel crash", error_type="ExecutionError"
    )
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

    result = taiga_mod.taiga_build(
        _configured_settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]}
    )
    assert result.ok is False
    assert result.status == "execution_failure"
    assert result.error_type == "ExecutionError"


def test_tool_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    runtime.run = lambda *a, **k: _FakeBuildResult(ok=False, status="timeout", error="timed out", error_type="TimeoutError")
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

    result = taiga_mod.taiga_build(
        _configured_settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]}
    )
    assert result.ok is False
    assert result.status == "timeout"
    assert result.error_type == "TimeoutError"


def test_tool_child_process_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    runtime.run = lambda *a, **k: _FakeBuildResult(
        ok=False, status="child_process_failure", error="child died", error_type="OSError"
    )
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

    result = taiga_mod.taiga_build(
        _configured_settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]}
    )
    assert result.ok is False
    assert result.status == "child_process_failure"


def test_tool_preserves_child_result_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    runtime.run = lambda *a, **k: _FakeBuildResult(
        ok=True,
        status="completed",
        result={
            "document_id": "taiga_deadbeef",
            "saved_path": "/ws/taiga_deadbeef.fcstd",
            "artifact_written": True,
            "metadata_written": True,
            "iou": 1.0,
            "match": True,
            "steps": 7,
            "budget": 13,
            "done": True,
            "feasible": True,
            "validation_ok": True,
            "validation_errors": [],
        },
    )
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

    result = taiga_mod.taiga_build(
        _configured_settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]}
    )
    assert result.document_id == "taiga_deadbeef"
    assert result.saved_path == "/ws/taiga_deadbeef.fcstd"
    assert result.artifact_written is True
    assert result.metadata_written is True
    assert result.iou == 1.0
    assert result.match is True
    assert result.steps == 7
    assert result.budget == 13
    assert result.done is True
    assert result.feasible is True
    assert result.validation_ok is True
    assert result.validation_errors == []


def test_tool_never_leaks_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> _FakeBuildResult:
        raise RuntimeError("unexpected child explosion")

    runtime = _FakeRuntime()
    runtime.run = boom
    monkeypatch.setattr(taiga_mod, "BuildRuntime", lambda *a, **k: runtime)
    monkeypatch.setattr(taiga_mod, "_make_child_runner", lambda settings: _FakeRunner())

    result = taiga_mod.taiga_build(
        _configured_settings(), {"features": [{"kind": "base_box", "params": {"w": 40}}]}
    )
    assert result.ok is False
    assert result.status == "child_process_failure"
    assert "unexpected child explosion" in (result.error or "")
    assert "Traceback" not in (result.error or "")
