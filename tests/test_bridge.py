"""Tests for the parent-side bridge (FreeCAD-independent).

These drive the real subprocess + envelope plumbing using the host interpreter
(``sys.executable``) as a stand-in "FreeCAD executable" and small fake runners.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import bridge as bridge_module
from freecad_mcp.freecad_bridge import (
    FreeCadBridge,
    FreeCadExecutionError,
    FreeCadNotFoundError,
    FreeCadOperationError,
    FreeCadResponseError,
    FreeCadTimeoutError,
    default_runner_path,
)


def _write_runner(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_runner.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def _write_envelope_runner(tmp_path: Path, envelope: str) -> Path:
    return _write_runner(
        tmp_path,
        f"""
        import sys
        with open(sys.argv[sys.argv.index('--pass') + 2], "w", encoding="utf-8") as handle:
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


@pytest.mark.skipif(sys.platform == "win32", reason="WSL-to-Windows translation")
def test_windows_target_translates_transport_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from freecad_mcp.freecad_bridge import bridge as bridge_module

    executable = tmp_path / "freecadcmd.exe"
    executable.touch()
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "result": {}}')
    translated = []

    def translate(path: Path, target: str) -> str:
        translated.append((path, target))
        return str(path)

    monkeypatch.setattr(bridge_module, "for_target", translate)
    # Avoid a real ``wslpath`` call for the workspace default so only transport
    # path translation is exercised here.
    monkeypatch.setattr(bridge_module, "to_windows", lambda path: str(path))
    monkeypatch.setattr(
        bridge_module.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "")
    )
    with pytest.raises(FreeCadExecutionError):
        FreeCadBridge(executable, runner_path=runner).ping()
    assert [target for _, target in translated] == ["windows"] * 3
    assert translated[0][0] == runner.resolve()


def _capture_run(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Monkeypatch subprocess.run to capture argv/env and write a fake success response.

    ``for_target`` is faked to return POSIX paths (so the fake response file is
    writable from WSL); ``wslpath`` calls are still delegated to the real
    subprocess for any real :func:`freecad_mcp.pathing.to_windows` usage. The
    request envelope is captured while the temp directory still exists.
    """
    real_subprocess_run = subprocess.run
    captured: dict = {}
    translated_targets = []

    def fake_for_target(path, target):
        translated_targets.append((path, target))
        return str(path)

    def fake_run(argv, **kwargs):
        argv_list = list(argv)
        if argv_list and Path(argv_list[0]).name == "wslpath":
            return real_subprocess_run(argv, **kwargs)
        captured["argv"] = [str(a) for a in argv_list]
        captured["env"] = kwargs.get("env")
        try:
            idx = argv_list.index("--pass")
            request_path = argv_list[idx + 1]
            response_path = argv_list[idx + 2]
            try:
                captured["request_params"] = (
                    json.loads(Path(request_path).read_text(encoding="utf-8")).get("params", {})
                )
            except (OSError, ValueError, KeyError):
                captured["request_params"] = {}
            with open(response_path, "w", encoding="utf-8") as handle:
                handle.write('{"ok": true, "op": "ping", "result": {}}')
        except (ValueError, IndexError, OSError):
            captured.setdefault("request_params", {})
        return subprocess.CompletedProcess(argv_list, 0, "", "")

    monkeypatch.setattr(bridge_module, "for_target", fake_for_target)
    monkeypatch.setattr("subprocess.run", fake_run)
    captured["for_target"] = translated_targets
    return captured


@pytest.mark.skipif(sys.platform == "win32", reason="WSL-to-Windows translation only applies on POSIX hosts")
def test_windows_target_translates_explicit_workspace_param(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "freecadcmd.exe"
    executable.touch()
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "result": {}}')
    captured = _capture_run(monkeypatch)

    translated = []

    def fake_to_windows(path):
        translated.append(path)
        return "C:\\translated\\workspace"

    monkeypatch.setattr(bridge_module, "to_windows", fake_to_windows)

    bridge = FreeCadBridge(executable, runner_path=runner)
    assert bridge.run("ping", {"workspace": "/mnt/g/foo/workspace"}) == {}
    assert translated[0] == "/mnt/g/foo/workspace"
    assert captured["env"][bridge_module.WORKSPACE_ENV] == "C:\\translated\\workspace"
    assert captured["request_params"]["workspace"] == "C:\\translated\\workspace"


@pytest.mark.skipif(sys.platform == "win32", reason="WSL-to-Windows translation only applies on POSIX hosts")
def test_windows_target_translates_env_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "freecadcmd.exe"
    executable.touch()
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "result": {}}')
    captured = _capture_run(monkeypatch)

    translated = []

    def fake_to_windows(path):
        translated.append(path)
        return "C:\\env\\workspace"

    monkeypatch.setattr(bridge_module, "to_windows", fake_to_windows)
    monkeypatch.setenv("FREECAD_MCP_WORKSPACE", "/mnt/g/env/workspace")

    bridge = FreeCadBridge(executable, runner_path=runner)
    bridge.run("ping", {})
    assert translated == ["/mnt/g/env/workspace"]
    assert captured["env"][bridge_module.WORKSPACE_ENV] == "C:\\env\\workspace"


@pytest.mark.skipif(sys.platform == "win32", reason="WSL-to-Windows translation only applies on POSIX hosts")
def test_windows_target_translates_default_workspace_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "freecadcmd.exe"
    executable.touch()
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "result": {}}')
    captured = _capture_run(monkeypatch)

    translated = []

    def fake_to_windows(path):
        translated.append(str(path))
        return "C:\\default\\workspace"

    monkeypatch.setattr(bridge_module, "to_windows", fake_to_windows)
    monkeypatch.delenv("FREECAD_MCP_WORKSPACE", raising=False)

    bridge = FreeCadBridge(executable, runner_path=runner)
    bridge.run("ping", {})
    assert translated == [str(Path.cwd() / bridge_module.DEFAULT_WORKSPACE)]
    assert captured["env"][bridge_module.WORKSPACE_ENV] == "C:\\default\\workspace"


def test_native_target_does_not_translate_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "result": {}}')
    captured = _capture_run(monkeypatch)

    called = []

    def fake_to_windows(path):
        called.append(path)
        return "C:\\x"

    monkeypatch.setattr(bridge_module, "to_windows", fake_to_windows)
    monkeypatch.delenv("FREECAD_MCP_WORKSPACE", raising=False)

    # Non-.exe executable on a POSIX host -> target == "native".
    bridge = FreeCadBridge(sys.executable, runner_path=runner)
    bridge.run("ping", {"workspace": "/mnt/g/foo/workspace"})
    assert called == []
    assert bridge_module.WORKSPACE_ENV not in captured["env"]


@pytest.mark.skipif(sys.platform == "win32", reason="WSL-to-Windows translation only applies on POSIX hosts")
def test_windows_target_already_windows_workspace_not_double_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from freecad_mcp.pathing import to_windows as real_to_windows

    executable = tmp_path / "freecadcmd.exe"
    executable.touch()
    runner = _write_envelope_runner(tmp_path, '{"ok": true, "result": {}}')
    captured = _capture_run(monkeypatch)

    # Use the real to_windows so the already-Windows short-circuit is exercised.
    bridge = FreeCadBridge(executable, runner_path=runner)
    bridge.run("ping", {"workspace": "E:\\Softwares\\FreeCAD\\workspace"})

    assert real_to_windows("E:\\Softwares\\FreeCAD\\workspace") == "E:\\Softwares\\FreeCAD\\workspace"
    assert captured["request_params"]["workspace"] == "E:\\Softwares\\FreeCAD\\workspace"
