"""Unit tests for the native worker env launcher (no FreeCAD required).

Exercises :class:`NativeWorkerEnv` against a fake ``subprocess.Popen`` that
speaks the ``@@S1 `` JSON-RPC protocol on stdout.  These tests do not require
FreeCAD or freecad_s1; they verify the launcher, protocol framing, error
handling, and cleanup in isolation.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from freecad_mcp.integrations.taiga.native_worker_env import (
    PROTOCOL_PREFIX,
    NativeWorkerEnv,
    WorkerError,
)


def _make_fake_proc(
    responses: list[dict[str, Any]] | None = None,
    *,
    exit_without_response: bool = False,
) -> MagicMock:
    """Build a fake Popen that emits queued @@S1 responses on stdout."""
    proc = MagicMock()
    proc.poll.return_value = None

    stdin_buffer: list[str] = []
    stdout_queue: list[str] = []
    if responses:
        for r in responses:
            stdout_queue.append(PROTOCOL_PREFIX + json.dumps(r) + "\n")
    if exit_without_response:
        proc.poll.return_value = 3

    def write_stdin(data: str) -> int:
        stdin_buffer.append(data)
        # When a full request line is written, queue the matching response.
        return len(data)

    def flush() -> None:
        pass

    def readline() -> str:
        # Simulate blocking readline: return queued lines, then EOF.
        if stdout_queue:
            return stdout_queue.pop(0)
        # No more output; simulate process exit.
        proc.poll.return_value = 3
        return ""

    proc.stdin = MagicMock()
    proc.stdin.write = write_stdin
    proc.stdin.flush = flush
    proc.stdin.closed = False
    proc.stdout = MagicMock()
    proc.stdout.readline = readline
    proc.wait = MagicMock(return_value=0)
    proc.kill = MagicMock()
    return proc


def _patch_popen(monkeypatch: pytest.MonkeyPatch, proc: MagicMock) -> None:
    monkeypatch.setattr(subprocess, "Popen", MagicMock(return_value=proc))


def _make_env(tmp_path: Path, proc: MagicMock, monkeypatch: pytest.MonkeyPatch) -> NativeWorkerEnv:
    # Create a dummy freecad executable so the is_file() check passes.
    fake = tmp_path / "freecadcmd.exe"
    fake.write_text("fake")
    _patch_popen(monkeypatch, proc)
    return NativeWorkerEnv(str(fake), str(tmp_path), timeout=30.0)


def test_native_worker_env_protocol_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """reset/step/score round-trip through the @@S1 protocol."""
    responses = [
        {"ok": True, "goal": {"features": []}, "budget": 10,
         "state": {"doc_open": True}, "actions": ["Done"], "expert": ["Done"]},
        {"ok": True, "info": {"done": True}, "state": {"doc_open": True}, "actions": ["Done"]},
        {"ok": True, "iou": 1.0, "match": True},
    ]
    proc = _make_fake_proc(responses)
    env = _make_env(tmp_path, proc, monkeypatch)
    try:
        r = env.reset({"goal": {"features": []}, "start": {}})
        assert r["ok"] is True
        assert r["budget"] == 10

        r = env.step("Done")
        assert r["ok"] is True
        assert r["info"]["done"] is True

        r = env.score()
        assert r["ok"] is True
        assert r["iou"] == 1.0
    finally:
        env.close()


def test_native_worker_env_ignores_noise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-protocol stdout noise is ignored; @@S1 lines are parsed."""
    proc = MagicMock()
    proc.poll.return_value = None
    noise = "Recompute... (50 %)\n"
    valid = PROTOCOL_PREFIX + json.dumps({"ok": True, "value": 42}) + "\n"
    lines = [noise, valid]

    def readline() -> str:
        if lines:
            return lines.pop(0)
        proc.poll.return_value = 3
        return ""

    proc.stdin = MagicMock()
    proc.stdin.closed = False
    proc.stdout = MagicMock()
    proc.stdout.readline = readline
    proc.wait = MagicMock(return_value=0)
    proc.kill = MagicMock()
    env = _make_env(tmp_path, proc, monkeypatch)
    try:
        r = env.call({"op": "anything"})
        assert r["ok"] is True
        assert r["value"] == 42
    finally:
        env.close()


def test_native_worker_env_error_response_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A worker ok:false response raises WorkerError."""
    responses = [{"ok": False, "error": "boom"}]
    proc = _make_fake_proc(responses)
    env = _make_env(tmp_path, proc, monkeypatch)
    try:
        with pytest.raises(WorkerError, match="boom"):
            env.call({"op": "step", "action": "Done"})
    finally:
        env.close()


def test_native_worker_env_eof_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worker exit without a response raises WorkerError."""
    proc = _make_fake_proc(exit_without_response=True)
    env = _make_env(tmp_path, proc, monkeypatch)
    try:
        with pytest.raises(WorkerError, match="exited"):
            env.call({"op": "reset", "goal": {}, "start": {}})
    finally:
        env.close()


def test_native_worker_env_missing_executable() -> None:
    """A missing freecadcmd raises WorkerError at construction."""
    with pytest.raises(WorkerError, match="not found"):
        NativeWorkerEnv("/nonexistent/freecadcmd", "/tmp", timeout=30.0)


def test_native_worker_env_close_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """close() can be called twice without error."""
    proc = _make_fake_proc([])
    env = _make_env(tmp_path, proc, monkeypatch)
    env.close()
    env.close()  # idempotent


def test_native_worker_env_close_sends_quit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """close() sends a quit request before waiting."""
    proc = _make_fake_proc([])
    written: list[str] = []
    proc.stdin.write = lambda d: written.append(d) or len(d)
    env = _make_env(tmp_path, proc, monkeypatch)
    env.close()
    assert any("quit" in w for w in written)


def test_native_worker_env_close_kills_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """close() kills the process if it does not exit in time."""
    proc = _make_fake_proc([])
    # Make wait() raise TimeoutExpired to simulate a hung worker.
    proc.wait = MagicMock(side_effect=[subprocess.TimeoutExpired("cmd", 10), 0])
    env = _make_env(tmp_path, proc, monkeypatch)
    env.close()
    proc.kill.assert_called()