"""Parent-side episode runtime contract for Taiga (Phase 4A).

Orchestrates one native Taiga episode per MCP request through the isolated
:class:`~freecad_mcp.integrations.taiga.runtime.TaigaRuntime`.  This module
owns:

* input validation (reuses :func:`integrations.taiga.goal.validate_goal`);
* timeout enforcement with full process-tree cleanup;
* response envelope decoding and result mapping.

The actual native ``run_episodes`` loop, ``Policy`` and ``Worker`` run inside
the torch child (``integrations/taiga/runner.py``) and the FreeCAD child
(``freecadcmd`` worker); this module never imports ``freecad_s1``, ``torch`` or
``FreeCAD``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .goal import InvalidGoalError, validate_goal
from .runtime import (
    TaigaRuntimeError,
    TaigaRuntimeOperationError,
    TaigaRuntimeTimeoutError,
    _kill_process_tree,
    unavailable_detail,
)

__all__ = [
    "EpisodeRuntime",
    "EpisodeRuntimeError",
    "EpisodeRequest",
    "EpisodeResult",
]

#: Default per-episode timeout (model load + worker spawn + episode).
DEFAULT_EPISODE_TIMEOUT_SECONDS = 300.0

#: Valid episode outcome strings (from native EpisodeResult.outcome plus
#: envelope-level failures).
_VALID_OUTCOMES = frozenset(
    {
        "success",
        "wrong_geometry",
        "budget",
        "unrecoverable",
        "crashed",
        "done",
        "done_clean",
    }
)


@dataclass(frozen=True)
class EpisodeRequest:
    """Validated request for one native episode."""

    goal: dict[str, Any]
    start: dict[str, Any]
    timeout: float


@dataclass(frozen=True)
class EpisodeResult:
    """Structured result of one native episode."""

    ok: bool
    status: str
    goal: dict[str, Any] | None = None
    start: dict[str, Any] | None = None
    level: int | None = None
    success: bool = False
    clean: bool = False
    iou: float = 0.0
    match: bool = False
    steps: int = 0
    budget: int = 0
    expert_steps: int = 0
    agreement: float = 0.0
    deviations: int = 0
    outcome: str | None = None
    features: list[str] | None = None
    rule: str | None = None
    history: list[str] | None = None
    final_state: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None


class EpisodeRuntimeError(Exception):
    """Base for episode runtime failures."""


class EpisodeRuntime:
    """Run one native Taiga episode through an injected child runner.

    Args:
        child_runner: Callable that executes one operation in the Taiga child
            and returns the response mapping.  Typically
            :meth:`TaigaRuntime.run`.
        timeout: Default per-episode timeout in seconds.
    """

    def __init__(
        self,
        *,
        child_runner: Callable[..., Mapping[str, Any]],
        timeout: float = DEFAULT_EPISODE_TIMEOUT_SECONDS,
    ) -> None:
        self._child_runner = child_runner
        self._timeout = float(timeout)
        if self._timeout <= 0:
            raise ValueError("episode timeout must be positive.")

    def prepare_request(
        self,
        goal: Any,
        start: Any = None,
    ) -> EpisodeRequest:
        """Validate goal and start; return a frozen request.

        Raises:
            InvalidGoalError: If the goal is structurally invalid.
        """
        validated = validate_goal(goal)
        start_dict: dict[str, Any] = {}
        if start:
            if not isinstance(start, Mapping):
                raise InvalidGoalError("start must be a JSON object.")
            allowed = {"doc_open", "workbench", "body"}
            unknown = set(start) - allowed
            if unknown:
                raise InvalidGoalError(
                    f"start has unsupported fields: {sorted(unknown)}."
                )
            start_dict = dict(start)
        return EpisodeRequest(
            goal=validated,
            start=start_dict,
            timeout=self._timeout,
        )

    def run(
        self,
        goal: Any,
        start: Any = None,
        *,
        timeout: float | None = None,
        freecad_executable: str | None = None,
        taiga_source: str | None = None,
        model: str | None = None,
        device: str | None = None,
    ) -> EpisodeResult:
        """Run one episode and return a structured result. Never raises."""
        try:
            request = self.prepare_request(goal, start)
        except InvalidGoalError as exc:
            return EpisodeResult(
                ok=False,
                status="invalid_goal",
                error=str(exc),
                error_type="InvalidGoalError",
            )

        if not freecad_executable:
            return EpisodeResult(
                ok=False,
                status="taiga_unavailable",
                error="FREECAD_MCP_FREECAD_EXECUTABLE is not configured.",
                error_type="TaigaUnavailableError",
            )
        if not taiga_source:
            return EpisodeResult(
                ok=False,
                status="taiga_unavailable",
                error="FREECAD_MCP_TAIGA_SOURCE is not configured.",
                error_type="TaigaUnavailableError",
            )
        if not model:
            return EpisodeResult(
                ok=False,
                status="taiga_unavailable",
                error="FREECAD_MCP_TAIGA_MODEL is not configured.",
                error_type="TaigaUnavailableError",
            )

        effective_timeout = request.timeout if timeout is None else float(timeout)
        if effective_timeout <= 0:
            return EpisodeResult(
                ok=False,
                status="invalid_timeout",
                error="timeout must be positive",
                error_type="ValueError",
            )

        params: dict[str, Any] = {
            "goal": request.goal,
            "start": request.start,
            "freecad_executable": freecad_executable,
            "taiga_source": taiga_source,
            "model": model,
        }
        if device:
            params["device"] = device

        try:
            response = self._child_runner(
                "run_episode", params, timeout=effective_timeout
            )
        except subprocess.TimeoutExpired as exc:
            return EpisodeResult(
                ok=False,
                status="timeout",
                error=str(exc) or "episode timed out",
                error_type="TimeoutError",
            )
        except TaigaRuntimeTimeoutError as exc:
            # Belt-and-suspenders: if the child_runner raised a timeout error
            # that carries a PID, ensure the process tree is gone.  The primary
            # cleanup already happens inside TaigaRuntime.run before it raises;
            # this guards against custom child runners that skip that step.
            pid = getattr(exc, "pid", None)
            if pid is not None:
                _kill_process_tree(pid)
            return EpisodeResult(
                ok=False,
                status="timeout",
                error=str(exc),
                error_type="TimeoutError",
            )
        except TaigaRuntimeOperationError as exc:
            return EpisodeResult(
                ok=False,
                status=_classify_error(exc.error_type),
                error=str(exc),
                error_type=exc.error_type,
            )
        except TaigaRuntimeError as exc:
            return EpisodeResult(
                ok=False,
                status="child_process_failure",
                error=unavailable_detail(exc),
                error_type=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001
            return EpisodeResult(
                ok=False,
                status="child_process_failure",
                error=f"Episode runtime failed: {exc}",
                error_type=type(exc).__name__,
            )

        # TaigaRuntime.run decodes the envelope and returns the result mapping
        # directly on success (raising on failure, handled above).
        if not isinstance(response, Mapping):
            return EpisodeResult(
                ok=False,
                status="invalid_response",
                error="Child result is not a mapping.",
                error_type="InvalidResponseError",
            )
        return _to_episode_result(response, request)


def _classify_error(error_type: str) -> str:
    """Map a child error type to an episode status string."""
    et = error_type.lower()
    if et.startswith("invalid"):
        return "invalid_goal"
    if et.startswith("timeout"):
        return "timeout"
    if et.startswith("worker"):
        return "crashed"
    if et.startswith("notinstalled") or et.startswith("missingdependency"):
        return "taiga_unavailable"
    return "execution_failure"


def _to_episode_result(
    result: Mapping[str, Any],
    request: EpisodeRequest,
) -> EpisodeResult:
    """Map a successful child result mapping to an :class:`EpisodeResult`."""
    outcome = result.get("outcome")
    success = bool(result.get("success", False))
    iou = float(result.get("iou", 0.0) or 0.0)
    match = bool(result.get("success", False)) or iou >= 0.99

    # Derive status from the native outcome.  success => "success";
    # done-but-low-iou => "wrong_geometry"; else the outcome string.
    if success:
        status = "success"
    elif outcome in _VALID_OUTCOMES:
        status = str(outcome)
    else:
        status = "execution_failure"

    history = result.get("history")
    if history is not None and not isinstance(history, list):
        history = None

    features = result.get("features")
    if features is not None and not isinstance(features, list):
        features = None

    final_state = result.get("final_state")
    if final_state is not None and not isinstance(final_state, Mapping):
        final_state = None

    return EpisodeResult(
        ok=bool(success),
        status=status,
        goal=request.goal,
        start=request.start,
        level=result.get("level"),
        success=success,
        clean=bool(result.get("clean", False)),
        iou=iou,
        match=match,
        steps=int(result.get("steps", 0) or 0),
        budget=int(result.get("budget", 0) or 0),
        expert_steps=int(result.get("expert_steps", 0) or 0),
        agreement=float(result.get("agreement", 0.0) or 0.0),
        deviations=int(result.get("deviations", 0) or 0),
        outcome=str(outcome) if outcome else None,
        features=features,
        rule=result.get("rule"),
        history=history,
        final_state=dict(final_state) if final_state else None,
        error=None,
        error_type=None,
    )