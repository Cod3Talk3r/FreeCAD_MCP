"""Optional, import-safe Taiga-S1 adapter.

Taiga-S1 is an external research project that scores the next FreeCAD
PartDesign command for a live modeling state. This adapter runs Taiga in an
isolated child process and deliberately does not adopt Taiga's persistent
worker, sockets, or command execution:

* importing this module never imports ``freecad_s1`` or ``torch``;
* :meth:`TaigaBackend.plan` reports that a runtime context is required;
* :meth:`TaigaBackend.score_actions` exposes the one operation that the
  verified upstream API supports without executing commands: ranking a list
  of currently valid actions for a supplied state and goal.

No model-produced command is ever executed here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..base import (
    IntegrationStatus,
    PlanningBackend,
    PlanningResult,
    UnavailableReason,
)
from .runtime import (
    DEFAULT_DEVICE,
    DEFAULT_TIMEOUT_SECONDS,
    TaigaRuntime,
    TaigaRuntimeError,
    unavailable_detail,
    unavailable_reason_for,
)

__all__ = [
    "ActionScore",
    "ActionScoringResult",
    "DEFAULT_DEVICE",
    "DEFAULT_TIMEOUT_SECONDS",
    "TaigaBackend",
    "TaigaStatusReport",
]

#: There is no remote default: scoring requires an explicit local model
#: directory so the application never downloads weights implicitly.
DEFAULT_MODEL: str | None = None

_MODEL_ENV_HINT = (
    "Configure FREECAD_MCP_TAIGA_MODEL with a local directory containing "
    "config.json and model.safetensors."
)


@dataclass(frozen=True, slots=True)
class ActionScore:
    """One candidate command and its calibrated probability."""

    action: str
    probability: float

    def __post_init__(self) -> None:
        if not self.action.strip():
            raise ValueError("action id must not be empty")
        if not math.isfinite(self.probability) or not 0.0 <= self.probability <= 1.0:
            raise ValueError(f"probability for {self.action!r} must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ActionScoringResult:
    """Outcome of scoring candidate actions.

    ``scored`` is True only when the model returned a probability for every
    requested action. A failed result always carries a structured reason and
    no scores.
    """

    backend: str
    scored: bool
    scores: tuple[ActionScore, ...] = field(default_factory=tuple)
    reason: UnavailableReason | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.backend.strip():
            raise ValueError("scoring result backend must not be empty")
        if self.scored:
            if self.reason is not None:
                raise ValueError("a scored result must not carry an unavailable reason")
            if not self.scores:
                raise ValueError("a scored result must contain at least one score")
        else:
            if self.reason is None:
                raise ValueError("an unscored result must carry an unavailable reason")
            if self.scores:
                raise ValueError("an unscored result must not contain scores")

    @classmethod
    def scored_actions(
        cls,
        backend: str,
        scores: tuple[ActionScore, ...],
    ) -> ActionScoringResult:
        """Build a result for scores the model actually returned."""
        return cls(backend=backend, scored=True, scores=scores)

    @classmethod
    def unavailable(
        cls,
        backend: str,
        reason: UnavailableReason,
        *,
        detail: str = "",
    ) -> ActionScoringResult:
        """Build a result for scoring that could not be performed."""
        return cls(backend=backend, scored=False, reason=reason, detail=detail)


@dataclass(frozen=True, slots=True)
class TaigaStatusReport:
    """Detailed status for the MCP ``taiga_status`` tool.

    ``status`` keeps the existing integration contract; the remaining fields
    add actionable runtime and local-model information without loading weights.
    """

    status: IntegrationStatus
    python: str | None
    model: str | None
    python_version: str | None = None
    torch_version: str | None = None
    model_available: bool | None = None
    model_error: str | None = None


class TaigaBackend(PlanningBackend):
    """Optional Taiga-S1 backend backed by an isolated subprocess.

    Args:
        python: Python executable of the environment containing Taiga-S1 and
            its dependencies. ``None`` reports a structured not-configured
            status instead of falling back to the server's interpreter.
        model: Local model directory containing ``config.json`` and
            ``model.safetensors``. Required for scoring.
        device: Device string passed to Taiga's model loader (``"cpu"`` by
            default).
        timeout: Maximum seconds for one child-process operation.
        runtime: Optional pre-built runtime, primarily for tests.
        runner_path: Optional override for the standalone runner script.
    """

    name = "taiga-s1"

    def __init__(
        self,
        *,
        python: str | None = None,
        model: str | None = None,
        device: str = DEFAULT_DEVICE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        runtime: TaigaRuntime | None = None,
        runner_path: str | None = None,
    ) -> None:
        self.python = python.strip() if python and python.strip() else None
        self.model = model.strip() if model and model.strip() else None
        self.device = (device or DEFAULT_DEVICE).strip() or DEFAULT_DEVICE
        self.timeout = float(timeout)
        self._runtime = runtime
        self._runner_path = runner_path

    def status(self) -> IntegrationStatus:
        """Check the isolated Taiga runtime without loading model weights."""
        return self.status_report().status

    def status_report(self) -> TaigaStatusReport:
        """Return detailed runtime and local-model status."""
        python_version: str | None = None
        torch_version: str | None = None
        model_available: bool | None = None
        model_error: str | None = None

        try:
            runtime = self._get_runtime()
            raw = runtime.status(model=self.model)
        except TaigaRuntimeError as exc:
            return TaigaStatusReport(
                status=IntegrationStatus.unavailable(
                    self.name,
                    unavailable_reason_for(exc),
                    detail=unavailable_detail(exc),
                ),
                python=self.python,
                model=self.model,
            )

        if not isinstance(raw, Mapping) or not isinstance(raw.get("available"), bool):
            return TaigaStatusReport(
                status=IntegrationStatus.unavailable(
                    self.name,
                    UnavailableReason.INCOMPATIBLE,
                    detail="Taiga returned a malformed status result.",
                ),
                python=self.python,
                model=self.model,
            )

        version = _optional_text(raw.get("version"))
        python_version = _optional_text(raw.get("python_version"))
        torch_version = _optional_text(raw.get("torch_version"))
        if isinstance(raw.get("model_available"), bool):
            model_available = bool(raw["model_available"])
        model_error = _optional_text(raw.get("model_error"))

        if raw["available"]:
            status = IntegrationStatus.available(self.name, version=version)
        else:
            status = IntegrationStatus.unavailable(
                self.name,
                _reason_from_value(raw.get("reason")),
                detail=_optional_text(raw.get("detail")) or "",
                version=version,
            )

        return TaigaStatusReport(
            status=status,
            python=self.python,
            model=self.model,
            python_version=python_version,
            torch_version=torch_version,
            model_available=model_available,
            model_error=model_error,
        )

    def plan(self, goal: str) -> PlanningResult:
        """Return a structured result instead of inventing a plan.

        Taiga-S1 scores one next command from a live ``State`` and the list of
        actions that are valid at that moment. Producing a multi-step plan
        requires applying each command and reading the resulting state, which
        is deliberately out of scope for this milestone.
        """
        status = self.status()
        if not status.is_available:
            return PlanningResult.unavailable(
                self.name,
                status.reason or UnavailableReason.INCOMPATIBLE,
                detail=status.detail,
            )

        return PlanningResult.unavailable(
            self.name,
            UnavailableReason.RUNTIME_CONTEXT_REQUIRED,
            detail=(
                "Taiga-S1 ranks one next command from a live FreeCAD State and "
                "the currently valid action list. Multi-step planning requires "
                "an execution runtime, which is not implemented in this "
                "milestone; no model-produced command was executed."
            ),
        )

    def score_actions(
        self,
        state: Mapping[str, Any],
        goal: Mapping[str, Any],
        actions: Sequence[str],
    ) -> ActionScoringResult:
        """Rank candidate actions using the verified Taiga-S1 API.

        Args:
            state: JSON-compatible state accepted by ``freecad_s1.schema.State``.
            goal: JSON-compatible goal accepted by ``freecad_s1.schema.Goal``.
            actions: Currently valid command ids, as returned by Taiga's
                ``enumerate_actions``.

        Returns:
            A ranked :class:`ActionScoringResult`, or a structured unavailable
            result when Taiga, its dependencies, the model, or the expected API
            is absent.

        This method only calls ``Policy.score`` in an isolated child process.
        It never executes a command.
        """
        action_list = _validate_actions(actions)
        if action_list is None:
            return ActionScoringResult.unavailable(
                self.name,
                UnavailableReason.INVALID_REQUEST,
                detail="actions must be a non-empty sequence of distinct, non-empty action ids.",
            )

        if not isinstance(state, Mapping) or not isinstance(goal, Mapping):
            return ActionScoringResult.unavailable(
                self.name,
                UnavailableReason.INVALID_REQUEST,
                detail="state and goal must be JSON objects.",
            )

        if not self.model:
            return ActionScoringResult.unavailable(
                self.name,
                UnavailableReason.NOT_INSTALLED,
                detail=f"No local Taiga model is configured. {_MODEL_ENV_HINT}",
            )

        try:
            runtime = self._get_runtime()
            raw = runtime.score_actions(
                dict(state),
                dict(goal),
                action_list,
                model=self.model,
                device=self.device,
            )
        except TaigaRuntimeError as exc:
            return ActionScoringResult.unavailable(
                self.name,
                unavailable_reason_for(exc),
                detail=unavailable_detail(exc),
            )

        if not isinstance(raw, Mapping):
            return ActionScoringResult.unavailable(
                self.name,
                UnavailableReason.INCOMPATIBLE,
                detail="Taiga returned a malformed scoring result.",
            )

        return _parse_scores(self.name, action_list, raw.get("scores"))

    def _get_runtime(self) -> TaigaRuntime:
        """Build and cache the subprocess runtime on first use."""
        if self._runtime is None:
            self._runtime = TaigaRuntime(
                self.python,
                timeout=self.timeout,
                runner_path=self._runner_path,
            )
        return self._runtime


def _validate_actions(actions: Sequence[str]) -> list[str] | None:
    """Return action ids or ``None`` if the input is malformed."""
    if actions is None or isinstance(actions, str):
        return None

    try:
        iterator = iter(actions)
    except TypeError:
        return None

    action_list: list[str] = []
    for action in iterator:
        if not isinstance(action, str) or not action.strip():
            return None
        action_list.append(action)

    if not action_list or len(set(action_list)) != len(action_list):
        return None
    return action_list


def _parse_scores(
    backend: str,
    actions: list[str],
    raw_scores: Any,
) -> ActionScoringResult:
    """Validate the child's score payload and convert it to typed scores."""
    if not isinstance(raw_scores, Sequence) or isinstance(raw_scores, (str, bytes)):
        return ActionScoringResult.unavailable(
            backend,
            UnavailableReason.INCOMPATIBLE,
            detail="Taiga-S1 returned a non-list score result.",
        )

    expected = set(actions)
    seen: set[str] = set()
    scores: list[ActionScore] = []
    for item in raw_scores:
        if not isinstance(item, Mapping):
            return ActionScoringResult.unavailable(
                backend,
                UnavailableReason.INCOMPATIBLE,
                detail="Taiga-S1 returned a score entry that is not an object.",
            )
        action = item.get("action")
        probability = item.get("probability")
        if not isinstance(action, str) or action not in expected:
            return ActionScoringResult.unavailable(
                backend,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Taiga-S1 returned an unexpected action id: {action!r}",
            )
        if action in seen:
            return ActionScoringResult.unavailable(
                backend,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Taiga-S1 returned a duplicate score for {action!r}.",
            )
        seen.add(action)

        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            return ActionScoringResult.unavailable(
                backend,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Taiga-S1 returned a non-numeric probability for {action!r}.",
            )
        try:
            scores.append(ActionScore(action=action, probability=float(probability)))
        except ValueError as exc:
            return ActionScoringResult.unavailable(
                backend,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Taiga-S1 returned an invalid score: {exc}",
            )

    missing = sorted(expected - seen)
    if missing:
        return ActionScoringResult.unavailable(
            backend,
            UnavailableReason.INCOMPATIBLE,
            detail=f"Taiga-S1 did not score the requested actions: {missing}.",
        )

    return ActionScoringResult.scored_actions(backend, tuple(scores))


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _reason_from_value(value: Any) -> UnavailableReason:
    if isinstance(value, str):
        try:
            return UnavailableReason(value)
        except ValueError:
            pass
    return UnavailableReason.INCOMPATIBLE
