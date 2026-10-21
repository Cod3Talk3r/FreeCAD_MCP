"""Optional, import-safe Taiga-S1 adapter.

Taiga-S1 is an external research project that scores the next FreeCAD
PartDesign command for a live modeling state. This adapter deliberately does
not adopt Taiga's runtime, sockets, or command execution:

* importing this module never imports ``freecad_s1`` or ``torch``;
* :meth:`TaigaBackend.plan` reports that a runtime context is required,
  because Taiga cannot produce a multi-step plan without a live FreeCAD state
  and an execution loop;
* :meth:`TaigaBackend.score_actions` exposes the one operation that the
  verified upstream API supports without executing commands: ranking a list
  of currently valid actions for a supplied state and goal.

No model-produced command is ever executed here.
"""

from __future__ import annotations

import importlib
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from importlib.util import find_spec
from typing import Any

from ..base import (
    IntegrationStatus,
    PlanningBackend,
    PlanningResult,
    UnavailableReason,
)

__all__ = [
    "ActionScore",
    "ActionScoringResult",
    "TaigaBackend",
]

#: Verified against the upstream Taiga-S1/Biome-S1 API in docs/taiga-s1.md.
DEFAULT_MODEL = "shhivv/taiga-s1"
DEFAULT_DEVICE = "cpu"

_MIN_PYTHON = (3, 11)


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


class TaigaBackend(PlanningBackend):
    """Optional Taiga-S1 backend.

    Args:
        model: Hugging Face repository id or local directory accepted by the
            verified ``from_pretrained`` API.
        device: Device string passed to Taiga's model loader (``"cpu"`` by
            default).
    """

    name = "taiga-s1"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        device: str = DEFAULT_DEVICE,
    ) -> None:
        self.model = model
        self.device = device
        self._policy: Any | None = None

    def status(self) -> IntegrationStatus:
        """Check for an importable Taiga package and its Torch dependency.

        This method imports only ``freecad_s1`` itself (whose upstream
        ``__init__`` is empty); Torch is checked with ``find_spec`` so status
        checks do not load a multi-hundred-megabyte library.
        """
        if tuple(sys.version_info[:2]) < _MIN_PYTHON:
            return IntegrationStatus.unavailable(
                self.name,
                UnavailableReason.INCOMPATIBLE,
                detail="Taiga-S1 requires Python >= 3.11.",
            )

        try:
            module = importlib.import_module("freecad_s1")
        except ImportError:
            return IntegrationStatus.unavailable(
                self.name,
                UnavailableReason.NOT_INSTALLED,
                detail=(
                    "The 'freecad_s1' package is not importable. Install Taiga-S1 "
                    "from its upstream repository in an isolated Python >= 3.11 "
                    "environment; it is not a default dependency of this project."
                ),
            )
        except Exception as exc:  # noqa: BLE001 - status must stay structured
            return IntegrationStatus.unavailable(
                self.name,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Importing 'freecad_s1' failed ({type(exc).__name__}): {exc}",
            )

        version = getattr(module, "__version__", None)
        if not isinstance(version, str) or not version:
            try:
                version = distribution_version("taiga-s1")
            except PackageNotFoundError:
                version = None

        if find_spec("torch") is None:
            return IntegrationStatus.unavailable(
                self.name,
                UnavailableReason.MISSING_DEPENDENCY,
                detail="Taiga-S1 requires PyTorch, which is not installed.",
                version=version,
            )

        return IntegrationStatus.available(self.name, version=version)

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
            result when Taiga, Torch, or the expected API is absent.

        This method only calls ``Policy.score``. It never executes a command.
        """
        status = self.status()
        if not status.is_available:
            return ActionScoringResult.unavailable(
                self.name,
                status.reason or UnavailableReason.INCOMPATIBLE,
                detail=status.detail,
            )

        action_list = _validate_actions(actions)
        if action_list is None or not action_list:
            return ActionScoringResult.unavailable(
                self.name,
                UnavailableReason.INVALID_REQUEST,
                detail="actions must be a non-empty sequence of distinct, non-empty action ids.",
            )

        try:
            from freecad_s1.model.net import from_pretrained
            from freecad_s1.rollout import Policy
            from freecad_s1.schema import Goal, State
        except ImportError as exc:
            reason = (
                UnavailableReason.MISSING_DEPENDENCY
                if getattr(exc, "name", "") == "torch"
                else UnavailableReason.INCOMPATIBLE
            )
            return ActionScoringResult.unavailable(
                self.name,
                reason,
                detail=f"Taiga-S1's verified API is not importable: {exc}",
            )

        try:
            policy = self._get_policy(from_pretrained, Policy)
            parsed_state = State.from_json(dict(state))
            parsed_goal = Goal.from_json(dict(goal))
            raw_scores = policy.score(parsed_state, parsed_goal, action_list)
        except Exception as exc:  # noqa: BLE001 - the adapter must not crash Method A
            return ActionScoringResult.unavailable(
                self.name,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Taiga-S1 scoring failed ({type(exc).__name__}): {exc}",
            )

        return _parse_scores(self.name, action_list, raw_scores)

    def _get_policy(self, from_pretrained: Any, policy_cls: Any) -> Any:
        """Load and cache the model policy on first use."""
        if self._policy is None:
            model = from_pretrained(self.model, device=self.device)
            self._policy = policy_cls(model, device=self.device)
        return self._policy


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

    if len(set(action_list)) != len(action_list):
        return None
    return action_list


def _parse_scores(
    backend: str,
    actions: list[str],
    raw_scores: Any,
) -> ActionScoringResult:
    """Validate the upstream mapping and convert it to typed scores."""
    if not isinstance(raw_scores, Mapping):
        return ActionScoringResult.unavailable(
            backend,
            UnavailableReason.INCOMPATIBLE,
            detail="Taiga-S1 returned a non-mapping score result.",
        )

    expected = set(actions)
    if set(raw_scores) != expected:
        missing = sorted(expected - set(raw_scores))
        extra = sorted(set(raw_scores) - expected)
        return ActionScoringResult.unavailable(
            backend,
            UnavailableReason.INCOMPATIBLE,
            detail=(
                "Taiga-S1 returned scores that do not match the requested actions "
                f"(missing={missing}, extra={extra})."
            ),
        )

    scores: list[ActionScore] = []
    for action, probability in raw_scores.items():
        try:
            scores.append(ActionScore(action=str(action), probability=float(probability)))
        except (TypeError, ValueError) as exc:
            return ActionScoringResult.unavailable(
                backend,
                UnavailableReason.INCOMPATIBLE,
                detail=f"Taiga-S1 returned an invalid score: {exc}",
            )

    return ActionScoringResult.scored_actions(backend, tuple(scores))
