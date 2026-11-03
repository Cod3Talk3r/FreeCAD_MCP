"""Parent-side Build Runtime contract for Taiga (Phase 2).

The actual FreeCAD child build runner is Phase 3. This module defines the
request/result contract and orchestrates an injected child runner.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .goal import InvalidGoalError, validate_goal


class BuildRuntimeError(Exception):
    """Base for build runtime errors."""


class TaigaUnavailableError(BuildRuntimeError):
    pass


class FeasibilityError(BuildRuntimeError):
    pass


class ExecutionError(BuildRuntimeError):
    pass


class TimeoutError(BuildRuntimeError):
    pass


class ChildProcessError(BuildRuntimeError):
    pass


class InvalidResponseError(BuildRuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BuildRequest:
    goal: dict[str, Any]
    timeout: float


@dataclass(frozen=True, slots=True)
class BuildResult:
    ok: bool
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None


ChildRunner = Callable[[Mapping[str, Any], float], Mapping[str, Any]]


class BuildRuntime:
    def __init__(self, *, child_runner: ChildRunner | None = None, timeout: float = 120.0) -> None:
        self._child_runner = child_runner
        self._timeout = float(timeout)
        if self._timeout <= 0:
            raise ValueError("build timeout must be positive.")

    def prepare_request(self, goal: Any) -> BuildRequest:
        validated = validate_goal(goal)
        return BuildRequest(goal=validated, timeout=self._timeout)

    def run(self, goal: Any, *, timeout: float | None = None) -> BuildResult:
        try:
            request = self.prepare_request(goal)
        except InvalidGoalError as exc:
            return BuildResult(ok=False, status="invalid_goal", error=str(exc), error_type="InvalidGoalError")

        if self._child_runner is None:
            return BuildResult(
                ok=False,
                status="taiga_unavailable",
                error="No Taiga child runner configured.",
                error_type="TaigaUnavailableError",
            )

        effective_timeout = self._timeout if timeout is None else float(timeout)
        if effective_timeout <= 0:
            return BuildResult(ok=False, status="invalid_timeout", error="timeout must be positive", error_type="ValueError")

        payload = {"op": "build", "params": {"goal": request.goal}}

        try:
            response = self._child_runner(payload, effective_timeout)
        except builtins.TimeoutError as exc:
            return BuildResult(ok=False, status="timeout", error=str(exc), error_type="TimeoutError")
        except Exception as exc:  # noqa: BLE001
            return BuildResult(ok=False, status="child_process_failure", error=str(exc), error_type=type(exc).__name__)

        if not isinstance(response, Mapping):
            return BuildResult(ok=False, status="invalid_response", error="Child response is not a mapping.", error_type="InvalidResponseError")
        if not isinstance(response.get("ok"), bool):
            return BuildResult(ok=False, status="invalid_response", error="Child response missing boolean 'ok'.", error_type="InvalidResponseError")

        if not response["ok"]:
            error = response.get("error")
            if isinstance(error, dict):
                msg = str(error.get("message", "build failed"))
                etype = str(error.get("type", "Error"))
            else:
                msg = str(error or "build failed")
                etype = "Error"
            status = "feasibility_failure" if etype.lower().startswith("feasib") else "execution_failure"
            return BuildResult(ok=False, status=status, error=msg, error_type=etype)

        result = response.get("result")
        if not isinstance(result, Mapping):
            return BuildResult(ok=False, status="invalid_response", error="Successful response has no result.", error_type="InvalidResponseError")
        return BuildResult(ok=True, status="completed", result=dict(result))