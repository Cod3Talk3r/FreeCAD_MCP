"""Tests for the parent-side Taiga child runner (FreeCAD transport).

The real FreeCAD and freecad_s1 packages are not installed here, so the child
subprocess is faked. These tests exercise the request/response transport,
path translation, timeout, and error classification without a heavyweight
optional dependency.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import freecad_mcp.integrations.taiga.child as child_mod
from freecad_mcp.integrations.taiga.child import TaigaChildRunner
from freecad_mcp.freecad_bridge import taiga_runner as taiga_runner_mod


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _make_runner(tmp_path: Path, *, freecad_name: str = "freecadcmd", timeout: float = 60.0) -> TaigaChildRunner:
    exe = tmp_path / freecad_name
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    runner = tmp_path / "taiga_runner.py"
    runner.write_text("print('runner')\n")
    return TaigaChildRunner(
        exe, taiga_runner_path=runner, taiga_source="/tmp/taiga-src", timeout=timeout
    )


def _install_fake_runner(monkeypatch: pytest.MonkeyPatch, response: dict[str, Any], *, crash: bool = False) -> dict[str, Any]:
    captured: dict[str, Any] = {"cmd": None, "request": None}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = kwargs
        if crash:
            return _Completed(returncode=1, stderr="child died")
        # Read the request while the temp directory still exists.  Request and
        # response paths are passed positionally after the --pass delimiter.
        if "--pass" in captured["cmd"]:
            idx = captured["cmd"].index("--pass")
            remaining = captured["cmd"][idx + 1:]
            if len(remaining) >= 2:
                captured["request"] = json.loads(Path(remaining[0]).read_text(encoding="utf-8"))
                resp_path = Path(remaining[1])
                resp_path.parent.mkdir(parents=True, exist_ok=True)
                resp_path.write_text(json.dumps(response), encoding="utf-8")
        return _Completed()

    monkeypatch.setattr(child_mod.subprocess, "run", fake_run)
    return captured


def test_run_invokes_child_with_pass_positional_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)
    captured = _install_fake_runner(monkeypatch, {"ok": True, "result": {"iou": 1.0}})

    payload = {"op": "build", "params": {"goal": {"features": [{"kind": "base_box", "params": {"w": 40}}]}}}
    response = runner.run(payload, timeout=30.0)

    assert response == {"ok": True, "result": {"iou": 1.0}}
    cmd = captured["cmd"]
    assert cmd[0] == str(runner.freecad_executable)
    assert cmd[1] == "-c"
    assert "--pass" in cmd
    pass_idx = cmd.index("--pass")
    assert cmd[pass_idx + 1] != ""
    assert cmd[pass_idx + 2] != ""
    assert captured["kwargs"]["timeout"] == 30.0
    assert "shell" not in captured["kwargs"]
    assert captured["request"]["op"] == "build"
    assert captured["request"]["params"]["goal"] == payload["params"]["goal"]
    assert captured["request"]["params"]["taiga_source"] == "/tmp/taiga-src"


def test_run_taiga_source_is_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)
    captured = _install_fake_runner(monkeypatch, {"ok": True})

    payload = {"op": "build", "params": {"goal": {"features": []}}}
    runner.run(payload)

    assert captured["request"]["params"]["taiga_source"] == "/tmp/taiga-src"
    assert captured["request"]["params"]["goal"] == {"features": []}


def test_run_translates_paths_for_windows_freecad_from_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _make_runner(tmp_path)
    translated: dict[str, str] = {}

    def fake_for_target(path: str, target: str) -> str:
        translated[(path, target)] = True
        return str(Path(path))  # real path so the fake runner can write it

    monkeypatch.setattr(child_mod, "is_windows_path", lambda p: True)
    monkeypatch.setattr(child_mod, "for_target", fake_for_target)
    monkeypatch.setattr(child_mod, "os", SimpleNamespace(name="posix", environ=child_mod.os.environ))
    _install_fake_runner(monkeypatch, {"ok": True})

    runner.run({"op": "build", "params": {"goal": {"features": []}}})

    assert any(t == "windows" for (_, t) in translated)


def test_run_returns_child_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)
    _install_fake_runner(
        monkeypatch,
        {"ok": False, "error": {"type": "FeasibilityError", "message": "target unreachable"}},
    )
    response = runner.run({"op": "build", "params": {"goal": {"features": []}}})
    assert response["ok"] is False
    assert response["error"]["type"] == "FeasibilityError"


def test_run_timeout_raises_timeout_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5.0)

    monkeypatch.setattr(child_mod.subprocess, "run", fake_run)
    with pytest.raises(TimeoutError):
        runner.run({"op": "build", "params": {"goal": {"features": []}}})


def test_run_missing_response_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)

    def fake_run(cmd, **kwargs):
        return _Completed(returncode=0)

    monkeypatch.setattr(child_mod.subprocess, "run", fake_run)
    with pytest.raises(FileNotFoundError):
        runner.run({"op": "build", "params": {"goal": {"features": []}}})


def test_run_malformed_response_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)

    def fake_run(cmd, **kwargs):
        if "--pass" in cmd:
            idx = cmd.index("--pass")
            remaining = cmd[idx + 1:]
            if len(remaining) >= 2:
                Path(remaining[1]).write_text("not json {", encoding="utf-8")
        return _Completed()

    monkeypatch.setattr(child_mod.subprocess, "run", fake_run)
    with pytest.raises(ValueError):
        runner.run({"op": "build", "params": {"goal": {"features": []}}})


def test_runner_rejects_missing_executable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        TaigaChildRunner(tmp_path / "no-such-freecadcmd")


def test_runner_rejects_missing_script(tmp_path: Path) -> None:
    exe = tmp_path / "freecadcmd"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    with pytest.raises(FileNotFoundError):
        TaigaChildRunner(exe, taiga_runner_path=tmp_path / "missing.py")


def test_non_mapping_response_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _make_runner(tmp_path)

    def fake_run(cmd, **kwargs):
        if "--pass" in cmd:
            idx = cmd.index("--pass")
            remaining = cmd[idx + 1:]
            if len(remaining) >= 2:
                Path(remaining[1]).write_text("[1, 2, 3]", encoding="utf-8")
        return _Completed()

    monkeypatch.setattr(child_mod.subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="not a mapping"):
        runner.run({"op": "build", "params": {"goal": {"features": []}}})


def test_child_module_imports_without_freecad_or_taiga() -> None:
    """The child-runner module must import on the host interpreter without
    FreeCAD or freecad_s1 (they are imported only inside the child)."""
    source = (
        "import sys\n"
        "class Blocker:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == \"FreeCAD\" or name == \"freecad_s1\" or name.startswith(\"freecad_s1.\"):\n"
        "            raise ImportError(\"blocked: \" + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Blocker())\n"
        "import freecad_mcp.integrations.taiga.child\n"
        "assert \"FreeCAD\" not in sys.modules\n"
        "assert \"freecad_s1\" not in sys.modules\n"
    )
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_taiga_runner_main_pass_args(tmp_path: Path) -> None:
    """main() with --pass delimiter reads request and writes response."""
    req_path = tmp_path / "request.json"
    resp_path = tmp_path / "response.json"
    req_path.write_text(
        json.dumps({"op": "build", "params": {"goal": {"features": []}}}),
        encoding="utf-8",
    )

    exit_code = taiga_runner_mod.main(["--pass", str(req_path), str(resp_path)])

    assert exit_code == 0
    assert resp_path.is_file()
    response = json.loads(resp_path.read_text(encoding="utf-8"))
    assert "ok" in response


def test_taiga_runner_main_pass_missing_args(tmp_path: Path) -> None:
    """main() with --pass but missing paths returns envelope error."""
    exit_code = taiga_runner_mod.main(["--pass"])
    assert exit_code == taiga_runner_mod.EXIT_ENVELOPE_ERROR


def test_taiga_runner_main_pass_only_one_arg(tmp_path: Path) -> None:
    """main() with --pass and only one path returns envelope error."""
    req_path = tmp_path / "request.json"
    exit_code = taiga_runner_mod.main(["--pass", str(req_path)])
    assert exit_code == taiga_runner_mod.EXIT_ENVELOPE_ERROR


def test_taiga_runner_main_request_response_options(tmp_path: Path) -> None:
    """main() with --request/--response options (backward compatibility)."""
    req_path = tmp_path / "request.json"
    resp_path = tmp_path / "response.json"
    req_path.write_text(
        json.dumps({"op": "build", "params": {"goal": {"features": []}}}),
        encoding="utf-8",
    )

    exit_code = taiga_runner_mod.main(["--request", str(req_path), "--response", str(resp_path)])

    assert exit_code == 0
    assert resp_path.is_file()


def test_taiga_runner_main_no_args(tmp_path: Path) -> None:
    """main() with no args returns envelope error."""
    exit_code = taiga_runner_mod.main([])
    assert exit_code == taiga_runner_mod.EXIT_ENVELOPE_ERROR
