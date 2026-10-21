"""Parent-side FreeCAD bridge.

Launches the FreeCAD console executable with :mod:`freecad_bridge.runner` and
exchanges a single JSON request/response envelope per operation. This is:

* **local only** - a child process on this machine; no sockets, no network;
* **explicit** - only the fixed operations in ``runner.OPERATIONS`` can run;
* **bounded** - every operation has a timeout, and the child is killed on expiry.

The parent process never imports ``FreeCAD`` or ``Part``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .discovery import find_freecad_executable
from .errors import (
    FreeCadExecutionError,
    FreeCadNotFoundError,
    FreeCadOperationError,
    FreeCadResponseError,
    FreeCadTimeoutError,
)
from .runner import OP_GET_VERSION, OP_PING, REQUEST_ENV, RESPONSE_ENV

#: Trim captured streams before embedding them in error messages.
_STREAM_TAIL = 4000


def default_runner_path() -> Path:
    """Absolute path to the FreeCAD-side runner script shipped in this package."""
    return Path(__file__).with_name("runner.py")


class FreeCadBridge:
    """Run explicit operations in a FreeCAD child process."""

    def __init__(
        self,
        executable: str | Path,
        *,
        timeout: float = 60.0,
        runner_path: str | Path | None = None,
    ) -> None:
        self.executable = Path(executable)
        self.timeout = float(timeout)
        self.runner_path = Path(runner_path) if runner_path is not None else default_runner_path()

        if not self.executable.is_file():
            raise FreeCadNotFoundError(f"FreeCAD executable not found: {self.executable}")
        if not self.runner_path.is_file():
            raise FreeCadNotFoundError(f"FreeCAD bridge runner missing: {self.runner_path}")

    @classmethod
    def from_settings(cls, settings: Any, *, environ: Mapping[str, str] | None = None) -> FreeCadBridge:
        """Build a bridge from project settings, discovering FreeCAD if needed.

        Raises:
            FreeCadNotFoundError: If no FreeCAD console executable is available.
        """
        executable = find_freecad_executable(getattr(settings, "freecad_executable", None), environ=environ)
        if executable is None:
            raise FreeCadNotFoundError(
                "No FreeCAD console executable found. Install FreeCAD or set "
                "FREECAD_MCP_FREECAD_EXECUTABLE to the freecadcmd/FreeCADCmd path."
            )
        return cls(executable, timeout=getattr(settings, "freecad_timeout_seconds", 60.0))

    def run(self, op: str, params: Mapping[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        """Execute ``op`` in FreeCAD and return its result payload.

        Raises:
            FreeCadTimeoutError: If FreeCAD does not finish in time.
            FreeCadExecutionError: If FreeCAD exits without producing a response.
            FreeCadResponseError: If the response envelope is malformed.
            FreeCadOperationError: If FreeCAD reports a structured failure.
        """
        request = {"op": op, "params": dict(params or {})}
        effective_timeout = self.timeout if timeout is None else float(timeout)

        with tempfile.TemporaryDirectory(prefix="freecad-mcp-") as workdir:
            work = Path(workdir)
            request_path = work / "request.json"
            response_path = work / "response.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")

            env = dict(os.environ)
            env[REQUEST_ENV] = str(request_path)
            env[RESPONSE_ENV] = str(response_path)

            try:
                completed = subprocess.run(  # noqa: S603 - explicit argv, no shell
                    [str(self.executable), str(self.runner_path)],
                    env=env,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=effective_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise FreeCadTimeoutError(
                    f"FreeCAD did not finish operation {op!r} within {effective_timeout:.0f}s.",
                    timeout=effective_timeout,
                ) from exc

            if not response_path.is_file():
                raise FreeCadExecutionError(
                    f"FreeCAD produced no response for operation {op!r} (exit code {completed.returncode}).",
                    returncode=completed.returncode,
                    stdout=_tail(completed.stdout),
                    stderr=_tail(completed.stderr),
                )

            try:
                envelope = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise FreeCadResponseError(f"Could not parse FreeCAD response for {op!r}: {exc}") from exc

        if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
            raise FreeCadResponseError(f"Unexpected FreeCAD response envelope for {op!r}: {envelope!r}")

        if not envelope["ok"]:
            error = envelope.get("error")
            if isinstance(error, dict):
                message = str(error.get("message", "FreeCAD reported a failure."))
                error_type = str(error.get("type", "Error"))
            else:
                message = str(error or "FreeCAD reported a failure.")
                error_type = "Error"
            raise FreeCadOperationError(message, error_type=error_type)

        if "result" not in envelope:
            raise FreeCadResponseError(
                f"Successful FreeCAD response for {op!r} has no 'result': {envelope!r}"
            )
        result = envelope.get("result")
        return result if isinstance(result, dict) else {"value": result}

    def ping(self, message: str = "pong") -> dict[str, Any]:
        """Round-trip a trivial operation to prove the bridge works."""
        return self.run(OP_PING, {"message": message})

    def get_version(self) -> dict[str, Any]:
        """Return FreeCAD version and interpreter details."""
        return self.run(OP_GET_VERSION)


def _tail(text: str | None) -> str:
    if not text:
        return ""
    return text[-_STREAM_TAIL:]
