"""Iterative Taiga-S1 action-selection loop (Milestone 7).

This module defines the *shape* of an iterative Taiga-S1 workflow:

1. capture the current FreeCAD document state,
2. ask Taiga-S1 to score the candidate actions,
3. select the highest scoring action,
4. execute it through an explicit execution boundary,
5. capture fresh state and repeat until ``Done`` or the iteration limit.

Important scope note
--------------------
This project has **no PartDesign execution layer** and **no live FreeCAD state
capture** in a form Taiga-S1 accepts. Both are deliberately out of scope
(see ``docs/taiga-s1.md``). This module therefore does not pretend to model
anything:

* It never maps a Taiga action id onto a FreeCAD operation by assumed
  semantic equivalence. ``PartDesign_Pad`` is *not* ``create_box``.
* It never fabricates a document state. Without a caller supplied
  ``state_provider`` the workflow stops with
  :attr:`UnavailableReason.RUNTIME_CONTEXT_REQUIRED`.
* It never fabricates a successful execution. Without a caller supplied
  ``executor`` the workflow stops with
  :attr:`UnavailableReason.RUNTIME_CONTEXT_REQUIRED`.
* ``Done`` terminates the loop *without* executing anything.

Consequently the default, unconfigured call does no modeling and says so. The
loop is exercised in tests through injected hooks, which is the only honest
sense in which it currently works.

All scoring goes through :class:`~freecad_mcp.integrations.taiga.adapter.TaigaBackend`,
so input validation, local-only model loading, timeouts, and structured error
mapping are shared with the MCP tools instead of being reimplemented here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from freecad_mcp.config import DEFAULT_TAIGA_TIMEOUT_SECONDS, Settings, load_settings
from freecad_mcp.integrations.base import UnavailableReason
from freecad_mcp.integrations.taiga.adapter import (
    ActionScore,
    ActionScoringResult,
    TaigaBackend,
)
from freecad_mcp.integrations.taiga.runner import DEFAULT_DEVICE
from freecad_mcp.integrations.taiga.runtime import TaigaRuntime

__all__ = [
    "CANDIDATE_ACTIONS",
    "TERMINATION_ACTION",
    "WorkflowResult",
    "WorkflowState",
    "run_iterative_modeling",
]

#: The Taiga action that means "the goal is complete".
TERMINATION_ACTION = "Done"

#: Actions we are willing to ask Taiga-S1 to score.
#:
#: These are Taiga PartDesign action ids taken from the verified upstream
#: action vocabulary. Being a *candidate* does not mean this project can
#: execute it: without an injected ``executor`` the loop refuses to run any
#: of them. There is intentionally no mapping from these ids to FreeCAD
#: operations, because no equivalent operation has been verified.
CANDIDATE_ACTIONS: tuple[str, ...] = (
    "PartDesign_Pad",
    "PartDesign_Pocket",
    "PartDesign_Fillet",
    TERMINATION_ACTION,
)

#: Signature of the state capture hook: ``document_id -> state mapping``.
StateProvider = Callable[[str], Mapping[str, Any]]

#: Signature of the execution hook: ``(action_id, document_id) -> envelope``.
#:
#: The returned mapping must look like a FreeCAD bridge response, i.e. it must
#: contain ``"ok": True`` on success. The hook is responsible for choosing the
#: concrete FreeCAD operation; this module never guesses one.
ActionExecutor = Callable[[str, str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class WorkflowState:
    """A JSON-compatible snapshot of the FreeCAD document state.

    Callers that do implement live capture can build this and return
    :meth:`as_json` from their ``state_provider``. Nothing in this module
    invents these values.
    """

    document_id: str
    object_names: tuple[str, ...] = field(default_factory=tuple)
    object_types: tuple[str, ...] = field(default_factory=tuple)
    bbox: tuple[float, float, float] | None = None
    feature_count: int = 0

    def as_json(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping for Taiga ``State.from_json``."""
        return {
            "document_id": self.document_id,
            "object_names": list(self.object_names),
            "object_types": list(self.object_types),
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "feature_count": self.feature_count,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> WorkflowState:
        """Rebuild a snapshot produced by :meth:`as_json`."""
        raw_bbox = payload.get("bbox")
        bbox: tuple[float, float, float] | None = None
        if isinstance(raw_bbox, Sequence) and not isinstance(raw_bbox, str | bytes):
            values = list(raw_bbox)
            if len(values) == 3:
                bbox = (float(values[0]), float(values[1]), float(values[2]))
        return cls(
            document_id=str(payload.get("document_id") or ""),
            object_names=_as_text_tuple(payload.get("object_names")),
            object_types=_as_text_tuple(payload.get("object_types")),
            bbox=bbox,
            feature_count=int(payload.get("feature_count") or 0),
        )


def _as_text_tuple(value: Any) -> tuple[str, ...]:
    """Coerce an optional list-like or string into a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return ()


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Outcome of one iterative workflow run.

    ``success`` is True only when Taiga-S1 explicitly signalled
    :data:`TERMINATION_ACTION`. Reaching ``max_iterations`` is not success,
    and neither is having executed some operations.
    """

    success: bool
    completed_operations: tuple[str, ...] = field(default_factory=tuple)
    final_detail: str = ""
    iteration_count: int = 0
    final_state: WorkflowState | None = None
    error_reason: UnavailableReason | None = None
    error_detail: str = ""


def run_iterative_modeling(
    goal_description: str,
    *,
    document_id: str | None = None,
    model: str | None = None,
    python: str | None = None,
    device: str = DEFAULT_DEVICE,
    timeout_seconds: float = DEFAULT_TAIGA_TIMEOUT_SECONDS,
    max_iterations: int = 10,
    settings: Settings | None = None,
    runtime: TaigaRuntime | None = None,
    state_provider: StateProvider | None = None,
    executor: ActionExecutor | None = None,
    goal: Mapping[str, Any] | None = None,
) -> WorkflowResult:
    """Run the iterative action-selection loop.

    Args:
        goal_description: Human readable modeling goal, forwarded to Taiga as
            part of the goal document.
        document_id: Existing workspace document to operate on.
        model: Local Taiga model directory. Falls back to
            ``FREECAD_MCP_TAIGA_MODEL``; never downloaded implicitly.
        python: Explicit interpreter containing ``freecad_s1`` and ``torch``.
            Falls back to ``FREECAD_MCP_TAIGA_PYTHON``; there is no fallback
            to the project interpreter.
        device: Device string passed to Taiga's model loader.
        timeout_seconds: Timeout for one isolated Taiga child process.
        max_iterations: Hard cap on loop iterations; must be positive.
        settings: Pre-loaded settings; otherwise read from the environment.
        runtime: Pre-built Taiga runtime (tests, or a custom deployment).
        state_provider: Hook that returns the live document state. Required
            for any iteration; there is no built-in implementation.
        executor: Hook that turns a selected action id into a bridge call.
            Required before anything is executed.
        goal: Optional explicit Taiga goal document. The upstream ``Goal``
            schema has not been verified against this project, so supply it
            when you know the expected shape; otherwise a minimal
            ``{"description": ...}`` document is used.

    Returns:
        A :class:`WorkflowResult`. Callers must check ``success`` and
        ``error_reason``; this function never raises for expected failures.
    """
    if max_iterations < 1:
        return WorkflowResult(
            success=False,
            error_reason=UnavailableReason.INVALID_REQUEST,
            error_detail="max_iterations must be at least 1.",
            final_detail="Invalid request: max_iterations must be at least 1.",
        )

    if settings is None:
        settings = load_settings({})

    resolved_python = python or settings.taiga_python
    resolved_model = model or settings.taiga_model

    # Report a missing local model before anything else: scoring is impossible
    # without it, and "no model" is more actionable than "no state provider".
    if not resolved_model:
        return WorkflowResult(
            success=False,
            error_reason=UnavailableReason.NOT_INSTALLED,
            error_detail="No local Taiga model is configured.",
            final_detail=(
                "No local Taiga model is configured, so nothing can be scored "
                "or executed. Set FREECAD_MCP_TAIGA_MODEL to a directory "
                "containing config.json and model.safetensors."
            ),
        )

    backend = TaigaBackend(
        python=resolved_python,
        model=resolved_model,
        device=device,
        timeout=timeout_seconds,
        runtime=runtime,
    )

    goal_document: Mapping[str, Any] = (
        dict(goal) if goal is not None else {"description": goal_description}
    )

    current_doc_id = document_id or ""
    completed: list[str] = []
    iteration = 0
    final_state: WorkflowState | None = None
    termination_detail = ""

    while iteration < max_iterations:
        iteration += 1

        # --- Step 1: capture state (never fabricated) ---
        captured, failure = _capture_state(state_provider, current_doc_id, iteration)
        if failure is not None:
            return failure
        assert captured is not None  # for type checkers; guarded by failure
        state_mapping = captured
        final_state = _as_workflow_state(state_mapping, current_doc_id)

        # --- Step 2: score candidate actions ---
        scoring = backend.score_actions(
            state_mapping,
            goal_document,
            list(CANDIDATE_ACTIONS),
        )
        if not scoring.scored:
            return WorkflowResult(
                success=False,
                completed_operations=tuple(completed),
                iteration_count=iteration,
                final_state=final_state,
                error_reason=scoring.reason,
                error_detail=scoring.detail,
                final_detail=scoring.detail or "Taiga-S1 could not score the candidate actions.",
            )

        # --- Step 3: select the highest scoring action ---
        try:
            selected = _select_action(scoring.scores)
        except ValueError as exc:
            return WorkflowResult(
                success=False,
                completed_operations=tuple(completed),
                iteration_count=iteration,
                final_state=final_state,
                error_reason=UnavailableReason.INCOMPATIBLE,
                error_detail=str(exc),
                final_detail=f"Taiga-S1 returned no usable scores: {exc}",
            )

        # --- Step 4: terminate on Done WITHOUT executing anything ---
        if selected == TERMINATION_ACTION:
            termination_detail = (
                "Taiga-S1 selected 'Done'; the workflow stopped without "
                "executing an operation."
            )
            break

        # --- Step 5: execute through the explicit boundary (never faked) ---
        if executor is None:
            return WorkflowResult(
                success=False,
                completed_operations=tuple(completed),
                iteration_count=iteration,
                final_state=final_state,
                error_reason=UnavailableReason.RUNTIME_CONTEXT_REQUIRED,
                error_detail=(
                    "No action executor is configured, so the selected action "
                    f"{selected!r} was not executed."
                ),
                final_detail=(
                    f"Taiga-S1 selected {selected!r}, but this project has no "
                    "verified FreeCAD execution path for it. Provide an "
                    "explicit executor once a PartDesign execution boundary "
                    "exists; nothing was executed."
                ),
            )

        envelope, failure = _execute(executor, selected, current_doc_id, iteration, completed, final_state)
        if failure is not None:
            return failure
        assert envelope is not None
        completed.append(selected)

        # --- Step 6: observe fresh state after the successful operation ---
        next_doc_id = _document_id_from(envelope) or current_doc_id
        refreshed, failure = _capture_state(state_provider, next_doc_id, iteration, completed)
        if failure is not None:
            return failure
        assert refreshed is not None
        final_state = _as_workflow_state(refreshed, next_doc_id)
        current_doc_id = next_doc_id

    # ------------------------------------------------------------------
    # Loop ended: either Done, or the iteration cap was hit.
    # ------------------------------------------------------------------
    if termination_detail:
        return WorkflowResult(
            success=True,
            completed_operations=tuple(completed),
            iteration_count=iteration,
            final_state=final_state,
            final_detail=termination_detail,
        )

    return WorkflowResult(
        success=False,
        completed_operations=tuple(completed),
        iteration_count=iteration,
        final_state=final_state,
        final_detail=(
            f"Reached max_iterations={max_iterations} without Taiga-S1 "
            f"selecting '{TERMINATION_ACTION}'. Executed "
            f"{len(completed)} operation(s)."
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _capture_state(
    state_provider: StateProvider | None,
    document_id: str,
    iteration: int,
    completed: Sequence[str] | None = None,
) -> tuple[Mapping[str, Any] | None, WorkflowResult | None]:
    """Return the captured state, or a structured failure.

    A missing provider is reported as ``RUNTIME_CONTEXT_REQUIRED`` rather than
    being replaced with an invented empty document.
    """
    if state_provider is None:
        return None, WorkflowResult(
            success=False,
            completed_operations=tuple(completed or ()),
            iteration_count=iteration,
            error_reason=UnavailableReason.RUNTIME_CONTEXT_REQUIRED,
            error_detail="No state provider is configured.",
            final_detail=(
                "Live FreeCAD state capture is not implemented, so Taiga-S1 "
                "cannot be given a real document state. Nothing was executed."
            ),
        )

    try:
        captured = state_provider(document_id)
    except Exception as exc:  # noqa: BLE001 - convert to a structured result
        return None, WorkflowResult(
            success=False,
            completed_operations=tuple(completed or ()),
            iteration_count=iteration,
            error_reason=UnavailableReason.RUNTIME_ERROR,
            error_detail=f"State capture failed: {exc}",
            final_detail="Failed to capture the FreeCAD document state.",
        )

    if not isinstance(captured, Mapping):
        return None, WorkflowResult(
            success=False,
            completed_operations=tuple(completed or ()),
            iteration_count=iteration,
            error_reason=UnavailableReason.RUNTIME_ERROR,
            error_detail=f"State provider returned {type(captured).__name__}, expected a mapping.",
            final_detail="The state provider must return a JSON-object-like mapping.",
        )

    return captured, None


def _execute(
    executor: ActionExecutor,
    action: str,
    document_id: str,
    iteration: int,
    completed: Sequence[str],
    final_state: WorkflowState | None,
) -> tuple[Mapping[str, Any] | None, WorkflowResult | None]:
    """Run the executor and classify its envelope; never fabricate success."""
    try:
        envelope = executor(action, document_id)
    except Exception as exc:  # noqa: BLE001 - convert to a structured result
        return None, WorkflowResult(
            success=False,
            completed_operations=tuple(completed),
            iteration_count=iteration,
            final_state=final_state,
            error_reason=UnavailableReason.RUNTIME_ERROR,
            error_detail=f"Executing {action!r} raised {type(exc).__name__}: {exc}",
            final_detail=f"Executing {action!r} failed; the workflow stopped.",
        )

    if not isinstance(envelope, Mapping):
        return None, WorkflowResult(
            success=False,
            completed_operations=tuple(completed),
            iteration_count=iteration,
            final_state=final_state,
            error_reason=UnavailableReason.RUNTIME_ERROR,
            error_detail=f"Executor returned {type(envelope).__name__}, expected a mapping.",
            final_detail=f"Executing {action!r} returned a malformed result.",
        )

    if envelope.get("ok") is not True:
        return None, WorkflowResult(
            success=False,
            completed_operations=tuple(completed),
            iteration_count=iteration,
            final_state=final_state,
            error_reason=UnavailableReason.RUNTIME_ERROR,
            error_detail=str(envelope.get("error") or envelope),
            final_detail=f"The FreeCAD operation for {action!r} reported failure; the workflow stopped.",
        )

    return envelope, None


def _document_id_from(envelope: Mapping[str, Any]) -> str | None:
    """Extract a document id from a bridge envelope, if it carries one."""
    result = envelope.get("result")
    if isinstance(result, Mapping):
        document_id = result.get("document_id")
        if isinstance(document_id, str) and document_id.strip():
            return document_id
    return None


def _as_workflow_state(state: Mapping[str, Any], document_id: str) -> WorkflowState:
    """Best-effort typed view of a captured state mapping."""
    payload = dict(state)
    payload.setdefault("document_id", document_id)
    try:
        return WorkflowState.from_json(payload)
    except (TypeError, ValueError):
        return WorkflowState(document_id=document_id)


def _select_action(scores: Sequence[ActionScore]) -> str:
    """Return the highest probability action id.

    Raises:
        ValueError: if there is nothing to select from.
    """
    if not scores:
        msg = "Cannot select an action from an empty score list."
        raise ValueError(msg)

    sorted_scores = sorted(scores, key=lambda score: score.probability, reverse=True)
    return sorted_scores[0].action


def _score_actions(
    runtime: TaigaRuntime,
    state: WorkflowState | Mapping[str, Any],
    goal: Mapping[str, Any],
    model: str,
    device: str,
    timeout: float,
) -> ActionScoringResult:
    """Score :data:`CANDIDATE_ACTIONS` through the shared Taiga backend.

    Kept as a thin, explicit helper so callers (and tests) can score one step
    without running a loop. ``timeout`` is used to bound the child process.
    """
    state_mapping = state.as_json() if isinstance(state, WorkflowState) else dict(state)
    backend = TaigaBackend(
        model=model,
        device=device,
        timeout=timeout,
        runtime=runtime,
    )
    return backend.score_actions(state_mapping, dict(goal), list(CANDIDATE_ACTIONS))
