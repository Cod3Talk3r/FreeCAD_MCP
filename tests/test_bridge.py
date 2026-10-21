"""Tests for the parent-side bridge (FreeCAD-independent).

These drive the real subprocess + envelope plumbing using the host interpreter
(``sys.executable``) as a stand-in "FreeCAD executable" and small fake runners.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import (
    FreeCadBridge,
    FreeCadExecutionError,
    FreeCadNotFoundError,
    FreeCadOperationError,
    FreeCadResponseError,
    FreeCadTimeoutError,
    default_runner_path,
)
from freecad_mcp.freecad_bridge.runner import RESPONSE_ENV


def _write_runner(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_runner.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def _write_envelope_runner(tmp_path: Path, envelope: str) -> Path:
    return _write_runner(
        tmp_path,
        f"""
        import os
        with open(os.environ[{RESPONSE_ENV!r}], "w", encoding="utf-8") as handle:
            handle.write({envelope!r})
        """,
    )


def test_missing_executable_raises(tmp_path: Path) -> None:
    with pytest.raises(FreeCadNotFoundError):
        FreeCadBridge(tmp_path / "nope.exe")


def test_from_settings_with_bogus_override_raises() -> None:
    with pytest.raises(FreeCadNotFoundError):
        FreeCadBridge.from_settings(Settings(freecad_executable="/definitely/not/here"))


def test_successful_result_is_returned(tmp_path: Path) -> None:
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "op": "ping", "result": {"message": "pong"}}')
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    assert bridge.ping() == {"message": "pong"}


def test_error_envelope_becomes_operation_error(tmp_path: Path) -> None:
    runner = _write_envelope_runner(
        tmp_path, '{"ok": false, "op": "ping", "error": {"type": "Boom", "message": "boom happened"}}'
    )
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.ping()
    assert excinfo.value.error_type == "Boom"
    assert "boom happened" in str(excinfo.value)


def test_string_error_envelope_becomes_operation_error(tmp_path: Path) -> None:
    runner = _write_envelope_runner(
        tmp_path, '{"ok": false, "op": "ping", "error": "boom happened"}'
    )
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.ping()
    assert excinfo.value.error_type == "Error"
    assert "boom happened" in str(excinfo.value)


def test_success_envelope_without_result_is_response_error(tmp_path: Path) -> None:
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "op": "ping"}')
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    with pytest.raises(FreeCadResponseError):
        bridge.ping()


def test_non_boolean_ok_is_response_error(tmp_path: Path) -> None:
    runner = _write_envelope_runner(
        tmp_path, '{"ok": "false", "op": "ping", "error": "boom"}'
    )
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    with pytest.raises(FreeCadResponseError):
        bridge.ping()


def test_missing_response_becomes_execution_error(tmp_path: Path) -> None:
    runner = _write_runner(tmp_path, "import sys; sys.exit(3)")
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    with pytest.raises(FreeCadExecutionError) as excinfo:
        bridge.ping()
    assert excinfo.value.returncode == 3


def test_malformed_response_becomes_response_error(tmp_path: Path) -> None:
    runner = _write_envelope_runner(tmp_path, "not valid json {")
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    with pytest.raises(FreeCadResponseError):
        bridge.ping()


def test_timeout_raises_and_kills_child(tmp_path: Path) -> None:
    runner = _write_runner(tmp_path, "import time; time.sleep(30)")
    bridge = FreeCadBridge(sys.executable, timeout=0.5, runner_path=runner)
    with pytest.raises(FreeCadTimeoutError):
        bridge.ping()


def test_real_runner_reports_freecad_import_failure(tmp_path: Path) -> None:
    """The shipped runner, run on the host interpreter, fails to import FreeCAD."""
    bridge = FreeCadBridge(sys.executable, timeout=30, runner_path=default_runner_path())
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.get_version()
    assert excinfo.value.error_type == "ModuleNotFoundError"
