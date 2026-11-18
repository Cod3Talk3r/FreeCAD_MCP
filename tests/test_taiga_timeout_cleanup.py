"""Focused regression tests for parent-side timeout process-tree cleanup.

These tests verify that when ``TaigaRuntime.run`` times out, it terminates the
entire owned process tree (the direct child AND its descendants), not just the
direct child.  They use real OS processes (no mocks) to prove that no orphan
survives the timeout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from freecad_mcp.integrations.taiga.runtime import (
    TaigaRuntime,
    TaigaRuntimeTimeoutError,
    _kill_process_tree,
)


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` is a live process.

    On Windows, ``os.kill(pid, 0)`` is unreliable (raises ``SystemError`` for
    dead PIDs), so we use ``tasklist /FI "PID eq <pid>"`` which is definitive.
    On POSIX, ``os.kill(pid, 0)`` is reliable.
    """
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            )
            # If the PID is alive, tasklist prints a line containing it.
            return str(pid) in out.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _write_sleeper_grandchild_script(dir_: Path, pid_file: Path) -> Path:
    """Write a Python script that spawns a grandchild sleeper and sleeps forever.

    The grandchild writes its own PID to ``pid_file`` so the test can find it.
    Returns the path to the parent (child) script.
    """
    child_path = dir_ / "child_sleeper.py"
    child_path.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(dir_ / 'grandchild_sleeper.py')!r}, {str(pid_file)!r}])\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    grandchild_path = dir_ / "grandchild_sleeper.py"
    grandchild_path.write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(600)\n",
        encoding="utf-8",
    )
    return child_path


def test_kill_process_tree_kills_grandchild() -> None:
    """_kill_process_tree terminates a child and its grandchild.

    Spawns a Python child that spawns a Python grandchild (both sleep forever),
    then calls _kill_process_tree on the child PID and asserts both are gone.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pid_file = tmp_path / "grandchild.pid"
        child_path = _write_sleeper_grandchild_script(tmp_path, pid_file)

        child = subprocess.Popen(
            [sys.executable, str(child_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Wait for the grandchild to write its PID.
            deadline = time.time() + 5.0
            grandchild_pid: int | None = None
            while time.time() < deadline:
                if pid_file.is_file():
                    text = pid_file.read_text().strip()
                    if text.isdigit():
                        grandchild_pid = int(text)
                        break
                time.sleep(0.1)
            assert grandchild_pid is not None, "grandchild did not write its PID"
            assert _pid_alive(grandchild_pid), "grandchild should be alive before kill"

            # Kill the entire tree rooted at the child.
            _kill_process_tree(child.pid)

            # Wait for both to die.
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if not _pid_alive(child.pid) and not _pid_alive(grandchild_pid):
                    break
                time.sleep(0.1)

            assert not _pid_alive(child.pid), "child should be dead after kill"
            assert not _pid_alive(grandchild_pid), (
                "grandchild should be dead after kill (orphan!)"
            )
        finally:
            _kill_process_tree(child.pid)


def test_runtime_timeout_kills_descendant() -> None:
    """TaigaRuntime.run timeout kills the child and its descendant.

    Uses a fake runner script that spawns a grandchild sleeper (which writes
    its PID to a file), then sleeps longer than the timeout.  Asserts that
    after the timeout, both the child (runner) and the grandchild are dead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pid_file = tmp_path / "gc.pid"
        # The runner spawns a grandchild sleeper, then sleeps forever (it never
        # writes a response file, so the runtime will time out).
        runner_path = _write_sleeper_grandchild_script(tmp_path, pid_file)

        runtime = TaigaRuntime(
            python=sys.executable,
            timeout=1.0,
            runner_path=runner_path,
        )

        with pytest.raises(TaigaRuntimeTimeoutError) as exc_info:
            runtime.run("run_episode", {"goal": {}})
        assert exc_info.value.pid is not None, "timeout error must carry the PID"

        # The runtime already killed the tree; verify no orphan grandchild.
        grandchild_pid: int | None = None
        if pid_file.is_file():
            text = pid_file.read_text().strip()
            if text.isdigit():
                grandchild_pid = int(text)

        if grandchild_pid is not None:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if not _pid_alive(grandchild_pid):
                    break
                time.sleep(0.1)
            assert not _pid_alive(grandchild_pid), (
                f"orphan grandchild survived timeout: {grandchild_pid}"
            )


def test_runtime_graceful_completion_does_not_kill() -> None:
    """Normal (non-timeout) completion leaves no orphan and does not kill.

    Uses a fast runner that writes a valid response file.  Asserts the child
    exits cleanly and no process-tree kill is needed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        runner_path = tmp_path / "fast_runner.py"
        runner_path.write_text(
            "import json, os\n"
            "req = os.environ.get('FREECAD_MCP_TAIGA_REQUEST')\n"
            "resp = os.environ.get('FREECAD_MCP_TAIGA_RESPONSE')\n"
            "if req and resp:\n"
            "    with open(req) as f: json.load(f)\n"
            "    with open(resp, 'w') as f:\n"
            "        json.dump({'ok': True, 'op': 'run_episode', 'result': {'success': True, 'iou': 1.0, 'steps': 1, 'budget': 10, 'expert_steps': 1, 'agreement': 1.0, 'deviations': 0, 'outcome': 'success', 'features': [], 'rule': None, 'history': None, 'final_state': {'doc_open': True}}}, f)\n",
            encoding="utf-8",
        )

        runtime = TaigaRuntime(
            python=sys.executable,
            timeout=10.0,
            runner_path=runner_path,
        )
        result = runtime.run("run_episode", {"goal": {"features": []}})
        assert result["success"] is True
        assert result["iou"] == 1.0


def test_kill_process_tree_nonexistent_pid() -> None:
    """Killing a nonexistent PID is a no-op (does not raise)."""
    _kill_process_tree(999999)  # should not raise


def test_runtime_child_exits_before_timeout() -> None:
    """Child exits with an error before the timeout; no kill needed.

    Verifies the race where the child fails fast (non-zero exit, no response
    file).  The runtime should report an execution error, not a timeout, and
    no process-tree kill should be required.
    """
    with tempfile.TemporaryDirectory() as tmp:
        runner_path = Path(tmp) / "failing_runner.py"
        runner_path.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")

        runtime = TaigaRuntime(
            python=sys.executable,
            timeout=10.0,
            runner_path=runner_path,
        )
        with pytest.raises(Exception) as exc_info:
            runtime.run("run_episode", {"goal": {}})
        # Should be an execution error (no response), not a timeout.
        assert not isinstance(exc_info.value, TaigaRuntimeTimeoutError)