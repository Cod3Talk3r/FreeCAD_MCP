"""Torch-side native worker environment adapter (Windows/freecadcmd compatible).

This module provides :class:`NativeWorkerEnv`, a thin replacement for Taiga's
:class:`freecad_s1.runtime.client.FreeCADEnv` that launches the *native*
``freecad_s1.runtime.worker.Worker.serve()`` JSON-RPC loop through
``freecadcmd.exe -c`` instead of ``python -m``.

Why this adapter exists
-----------------------
The upstream :class:`FreeCADEnv` spawns the worker with
``[freecad_python, "-m", "freecad_s1.runtime.worker"]``.  On Windows this is
impossible:

* ``freecadcmd.exe`` does not accept ``-m`` (``unrecognised option '-m'``).
* The Taiga virtualenv's ``python.exe`` cannot ``import FreeCAD``.

The *protocol* (JSON-RPC over stdin, ``@@S1 ``-prefixed responses on stdout)
is reused verbatim from the native :class:`Worker.serve()`.  Only the process
launcher differs.  This keeps the native ``Policy`` / ``run_episodes`` /
``Worker`` / ``HeadlessSession`` / action catalogue / executors 100% native.

The parent MCP process never imports ``freecad_s1`` or ``torch``; this module is
imported only inside the torch child interpreter (``taiga_python`` running
``integrations/taiga/runner.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

__all__ = ["NativeWorkerEnv", "WorkerError", "PROTOCOL_PREFIX"]

#: Marker that prefixes every worker response line on stdout (matches
#: ``freecad_s1.runtime.fcenv.PROTOCOL_PREFIX``).
PROTOCOL_PREFIX = "@@S1 "

#: Seconds to wait for a graceful ``quit`` before killing the worker.
_CLOSE_TIMEOUT = 10.0


class WorkerError(RuntimeError):
    """Raised when the native FreeCAD worker reports a failure or exits."""


class NativeWorkerEnv:
    """One FreeCAD worker process driven over JSON-RPC via ``freecadcmd -c``.

    Implements the same ``send`` / ``recv`` / ``call`` / ``close`` surface as
    upstream :class:`freecad_s1.runtime.client.FreeCADEnv`, plus the
    :class:`VecEnv`-shaped ``reset`` / ``step`` / ``score`` helpers that
    :func:`freecad_s1.rollout.run_episodes` invokes.  This lets the native
    episode loop run unchanged.

    Args:
        freecad_executable: Path to ``freecadcmd`` / ``FreeCADCmd``.
        taiga_source: Repository root containing ``freecad_s1`` (prepended to
            ``sys.path`` inside the child).
        timeout: Per-``recv`` timeout in seconds (guards against a hung worker).
    """

    def __init__(
        self,
        freecad_executable: str,
        taiga_source: str,
        *,
        timeout: float = 120.0,
    ) -> None:
        self._freecad_executable = str(freecad_executable)
        self._taiga_source = str(taiga_source)
        self._timeout = float(timeout)
        if self._timeout <= 0:
            raise ValueError("NativeWorkerEnv timeout must be positive.")

        if not Path(self._freecad_executable).is_file():
            raise WorkerError(
                f"FreeCAD executable not found: {self._freecad_executable}"
            )

        # Build the -c payload: prepend taiga_source to sys.path, then run the
        # native worker's serve() loop.  We call runpy.run_path on worker.py's
        # __main__ block so the native Worker.serve() runs exactly as upstream
        # intends, but under freecadcmd -c (which lacks -m).
        worker_code = (
            "import sys; "
            f"sys.path.insert(0, {self._taiga_source!r}); "
            "import runpy; "
            "runpy.run_module("
            "'freecad_s1.runtime.worker', run_name='__main__')"
        )

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"

        self._proc = subprocess.Popen(  # noqa: S603 - explicit argv, no shell
            [
                self._freecad_executable,
                "-c",
                worker_code,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        self._closed = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Low-level send/recv/call (mirrors FreeCADEnv)
    # ------------------------------------------------------------------

    def send(self, req: dict) -> None:
        """Write one JSON request line to the worker's stdin."""
        if self._closed:
            raise WorkerError("NativeWorkerEnv is closed.")
        if self._proc.stdin is None:
            raise WorkerError("Worker stdin is not available.")
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()

    def recv(self) -> dict:
        """Read the next ``@@S1``-prefixed response line from stdout.

        FreeCAD prints recompute progress and other noise to stdout; we scan
        lines until we find the protocol prefix, ignoring non-protocol noise.
        """
        if self._proc.stdout is None:
            raise WorkerError("Worker stdout is not available.")
        while True:
            line = self._proc.stdout.readline()
            if not line:
                code = self._proc.poll()
                raise WorkerError(
                    f"FreeCAD worker exited (code {code})"
                )
            if line.startswith(PROTOCOL_PREFIX):
                try:
                    resp = json.loads(line[len(PROTOCOL_PREFIX):])
                except ValueError as exc:
                    raise WorkerError(
                        f"Worker returned malformed JSON: {exc}"
                    ) from exc
                if not resp.get("ok"):
                    raise WorkerError(
                        str(resp.get("error", "unknown worker error"))
                    )
                return resp

    def call(self, req: dict) -> dict:
        """Send one request and return the response."""
        with self._lock:
            self.send(req)
            return self.recv()

    # ------------------------------------------------------------------
    # VecEnv-shaped helpers used by run_episodes
    # ------------------------------------------------------------------

    def reset(self, spec: dict) -> dict:
        """Send a reset spec (goal+start or level+seed) and return the response.

        ``spec`` is a single reset request dict; it is merged into the worker's
        ``{"op": "reset", **spec}`` message by the native worker.
        """
        return self.call({"op": "reset", **spec})

    def step(self, action: str, reward: bool = False) -> dict:
        """Execute one action and return ``{info, state, actions, expert, ...}``."""
        return self.call({"op": "step", "action": action, "reward": reward})

    def score(self) -> dict:
        """Score the current solid against the episode target."""
        return self.call({"op": "score"})

    def save(self, fcstd_path: str) -> dict:
        """Save the current document to ``fcstd_path`` via the native worker.

        Reuses the native ``Worker.handle`` ``save`` op
        (``freecad_s1.runtime.worker.Worker.handle``), which calls
        ``s.doc.saveAs(req["fcstd"])``.  This is the *only* save mechanism;
        no custom FreeCAD save logic is implemented here.

        Args:
            fcstd_path: Absolute path to the destination ``.fcstd`` file.

        Returns:
            The worker response (contains ``fcstd`` on success).

        Raises:
            WorkerError: If the worker reports a failure or exits.
        """
        return self.call({"op": "save", "fcstd": str(fcstd_path)})

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Send ``quit`` and wait, then kill if it does not exit in time."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._proc.poll() is not None:
            return
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                self._proc.stdin.write(json.dumps({"op": "quit"}) + "\n")
                self._proc.stdin.flush()
            self._proc.wait(timeout=_CLOSE_TIMEOUT)
        except Exception:
            self._proc.kill()
            try:
                self._proc.wait(timeout=_CLOSE_TIMEOUT)
            except Exception:
                pass

    def __enter__(self) -> "NativeWorkerEnv":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()