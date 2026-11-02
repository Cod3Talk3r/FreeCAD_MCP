"""Parent-side client for the isolated Taiga-S1 runtime.

Each operation launches the explicitly configured Taiga Python interpreter with
:mod:`freecad_mcp.integrations.taiga.runner` and exchanges one JSON request and
one JSON response through temporary files. This mirrors the FreeCAD bridge's
one-shot child-process model: local-only, bounded, explicit, and with no
sockets or persistent worker.

The parent process never imports ``freecad_s1`` or ``torch``. Those packages are
imported only inside the child interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..base import UnavailableReason
from .runner import DEFAULT_DEVICE, REQUEST_ENV, RESPONSE_ENV

__all__ = [
    "DEFAULT_DEVICE",
    "DEFAULT_TIMEOUT_SECONDS",
    "TaigaRuntime",
    "TaigaRuntimeError",
    "TaigaRuntimeExecutionError",
    "TaigaRuntimeNotConfiguredError",
    "TaigaRuntimeNotFoundError",
    "TaigaRuntimeOperationError",
    "TaigaRuntimeRequestError",
    "TaigaRuntimeResponseError",
    "TaigaRuntimeTimeoutError",
    "unavailable_detail",
    "unavailable_reason_for",
]

#: Default maximum time for one Taiga child process, including Torch import.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: Trim captured streams before embedding them in error messages.
_STREAM_TAIL = 4000


class TaigaRuntimeError(RuntimeError):
    """Base class for every Taiga runtime failure."""


class TaigaRuntimeNotConfiguredError(TaigaRuntimeError):
    """No Taiga Python interpreter was configured."""


class TaigaRuntimeNotFoundError(TaigaRuntimeError):
    """The configured interpreter or the runner script is missing."""


class TaigaRuntimeTimeoutError(TaigaRuntimeError):
    """The Taiga child process did not finish within the allotted time."""

    def __init__(self, message: str, *, timeout: float) -> None:
        super().__init__(message)
        self.timeout = timeout


class TaigaRuntimeRequestError(TaigaRuntimeError):
    """The request could not be serialized or written."""


class TaigaRuntimeExecutionError(TaigaRuntimeError):
    """The child process exited without producing a usable response."""

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TaigaRuntimeResponseError(TaigaRuntimeError):
    """The child produced a missing, malformed, or inconsistent response."""


class TaigaRuntimeOperationError(TaigaRuntimeError):
    """The child reported a structured operation failure."""

    def __init__(self, message: str, *, error_type: str = "RuntimeError") -> None:
        super().__init__(message)
        self.error_type = error_type


class TaigaRuntime:
    """Run one explicit operation in the configured Taiga interpreter."""

    def __init__(
        self,
        python: str | Path | None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        runner_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.python = Path(python).expanduser() if python else None
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("Taiga runtime timeout must be positive.")
        self.runner_path = (
            Path(runner_path)
            if runner_path is not None
            else Path(__file__).with_name("runner.py")
        )
        self._environ = dict(environ) if environ is not None else None

        if self.python is not None and not self.python.is_file():
            raise TaigaRuntimeNotFoundError(
                f"Taiga Python interpreter not found: {self.python}"
            )
        if not self.runner_path.is_file():
            raise TaigaRuntimeNotFoundError(
                f"Taiga runtime runner missing: {self.runner_path}"
            )

    def run(
        self,
        op: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Execute ``op`` in the Taiga child and return its result payload."""
        if self.python is None:
            raise TaigaRuntimeNotConfiguredError(
                "No Taiga Python interpreter is configured. Set "
                "FREECAD_MCP_TAIGA_PYTHON to the Python executable of an "
                "environment containing Taiga-S1 and its dependencies."
            )

        request = {"op": op, "params": dict(params or {})}
        effective_timeout = self.timeout if timeout is None else float(timeout)
        if effective_timeout <= 0:
            raise ValueError("Taiga runtime timeout must be positive.")

        with tempfile.TemporaryDirectory(prefix="freecad-mcp-taiga-") as workdir:
            work = Path(workdir)
            request_path = work / "request.json"
            response_path = work / "response.json"

            try:
                request_path.write_text(
                    json.dumps(request, ensure_ascii=True),
                    encoding="utf-8",
                )
            except (OSError, TypeError, ValueError) as exc:
                raise TaigaRuntimeRequestError(
                    f"Could not serialize the Taiga request: {exc}"
                ) from exc

            env = dict(os.environ if self._environ is None else self._environ)
            env[REQUEST_ENV] = str(request_path)
            env[RESPONSE_ENV] = str(response_path)

            try:
                completed = subprocess.run(  # noqa: S603 - explicit argv, no shell
                    [str(self.python), str(self.runner_path)],
                    env=env,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=effective_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TaigaRuntimeTimeoutError(
                    f"Taiga did not finish operation {op!r} within "
                    f"{effective_timeout:.0f}s.",
                    timeout=effective_timeout,
                ) from exc
            except OSError as exc:
                raise TaigaRuntimeExecutionError(
                    f"Could not start the Taiga interpreter: {exc}"
                ) from exc

            if not response_path.is_file():
                raise TaigaRuntimeExecutionError(
                    f"Taiga produced no response for operation {op!r} "
                    f"(exit code {completed.returncode}).",
                    returncode=completed.returncode,
                    stdout=_tail(completed.stdout),
                    stderr=_tail(completed.stderr),
                )

            try:
                envelope = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise TaigaRuntimeResponseError(
                    f"Could not parse the Taiga response for {op!r}: {exc}"
                ) from exc

        return _decode_envelope(op, envelope)

    def status(self, *, model: str | None = None) -> dict[str, Any]:
        """Return runtime availability without loading model weights."""
        params: dict[str, Any] = {}
        if model:
            params["model"] = model
        return self.run("status", params)

    def score_actions(
        self,
        state: Mapping[str, Any],
        goal: Mapping[str, Any],
        actions: list[str],
        *,
        model: str,
        device: str = DEFAULT_DEVICE,
    ) -> dict[str, Any]:
        """Score actions in the child process; never executes a command."""
        return self.run(
            "score_actions",
            {
                "state": dict(state),
                "goal": dict(goal),
                "actions": list(actions),
                "model": model,
                "device": device,
            },
        )


def _decode_envelope(op: str, envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, dict) or not isinstance(envelope.get("ok"), bool):
        raise TaigaRuntimeResponseError(
            f"Unexpected Taiga response envelope for {op!r}: {envelope!r}"
        )

    if "op" in envelope and envelope["op"] != op:
        raise TaigaRuntimeResponseError(
            f"Taiga response op {envelope['op']!r} does not match request {op!r}."
        )

    if not envelope["ok"]:
        error = envelope.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "Taiga reported a failure."))
            error_type = str(error.get("type", "RuntimeError"))
        else:
            message = str(error or "Taiga reported a failure.")
            error_type = "RuntimeError"
        raise TaigaRuntimeOperationError(message, error_type=error_type)

    if "result" not in envelope:
        raise TaigaRuntimeResponseError(
            f"Successful Taiga response for {op!r} has no 'result': {envelope!r}"
        )

    result = envelope.get("result")
    return result if isinstance(result, dict) else {"value": result}


def _tail(text: str | None) -> str:
    if not text:
        return ""
    return text[-_STREAM_TAIL:]


_CHILD_REASONS = {
    "notinstalled": UnavailableReason.NOT_INSTALLED,
    "missingdependency": UnavailableReason.MISSING_DEPENDENCY,
    "incompatible": UnavailableReason.INCOMPATIBLE,
    "invalidrequest": UnavailableReason.INVALID_REQUEST,
    "timeout": UnavailableReason.TIMEOUT,
    "runtimeerror": UnavailableReason.RUNTIME_ERROR,
}


def unavailable_reason_for(exc: BaseException) -> UnavailableReason:
    """Map a runtime failure to the integration contract's structured reason."""
    if isinstance(exc, TaigaRuntimeTimeoutError):
        return UnavailableReason.TIMEOUT
    if isinstance(exc, (TaigaRuntimeNotConfiguredError, TaigaRuntimeNotFoundError)):
        return UnavailableReason.NOT_INSTALLED
    if isinstance(exc, TaigaRuntimeRequestError):
        return UnavailableReason.INVALID_REQUEST
    if isinstance(exc, TaigaRuntimeOperationError):
        key = exc.error_type.replace("_", "").replace("-", "").lower()
        return _CHILD_REASONS.get(key, UnavailableReason.RUNTIME_ERROR)
    return UnavailableReason.RUNTIME_ERROR


def unavailable_detail(exc: BaseException) -> str:
    """Return a concise, client-safe explanation for a runtime failure."""
    if isinstance(exc, TaigaRuntimeExecutionError) and not str(exc):
        return "The Taiga runtime process failed."
    return str(exc) or type(exc).__name__
