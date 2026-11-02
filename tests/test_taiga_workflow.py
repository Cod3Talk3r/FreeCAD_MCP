"""Tests for the iterative Taiga-S1 workflow (Milestone 7).

No real Taiga, Torch, model weights, or FreeCAD is involved. The real
:class:`~freecad_mcp.integrations.taiga.adapter.TaigaBackend` is used with an
injected fake runtime, so the scoring validation under test is the production
validation and not a copy of it.

The fake runtime returns envelopes of exactly the shape the real child runner
produces (``{"scores": [{"action": ..., "probability": ...}], "device": ...}``)
and every envelope scores the full candidate set, as Taiga-S1 does.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from freecad_mcp.integrations.base import UnavailableReason
from freecad_mcp.integrations.taiga.runner import DEFAULT_DEVICE
from freecad_mcp.integrations.taiga.runtime import TaigaRuntimeTimeoutError
from freecad_mcp.integrations.taiga.workflow import (
    CANDIDATE_ACTIONS,
    TERMINATION_ACTION,
    WorkflowState,
    _select_action,
    run_iterative_modeling,
)

MODEL = "C:/models/taiga-s1"


# ---------------------------------------------------------------------------
# Fakes and helpers
# ---------------------------------------------------------------------------


class FakeTaigaRuntime:
    """Stand-in for :class:`TaigaRuntime` returning canned runner envelopes."""

    def __init__(self, *envelopes: dict[str, Any], error: BaseException | None = None) -> None:
        self._envelopes = list(envelopes)
        self._error = error
        self.score_calls: list[dict[str, Any]] = []
        self.status_calls: list[str | None] = []

    def status(self, *, model: str | None = None) -> dict[str, Any]:
        self.status_calls.append(model)
        if self._error is not None:
            raise self._error
        return {"available": True, "reason": None, "detail": "", "version": "0.1.0"}

    def score_actions(
        self,
        state: Mapping[str, Any],
        goal: Mapping[str, Any],
        actions: list[str],
        *,
        model: str,
        device: str = DEFAULT_DEVICE,
    ) -> dict[str, Any]:
        self.score_calls.append(
            {"state": dict(state), "goal": dict(goal), "actions": list(actions),
             "model": model, "device": device}
        )
        if self._error is not None:
            raise self._error
        if not self._envelopes:
            msg = "FakeTaigaRuntime got more scoring calls than it has envelopes"
            raise AssertionError(msg)
        # Keep the final envelope for any extra iterations.
        if len(self._envelopes) > 1:
            return self._envelopes.pop(0)
        return self._envelopes[0]


def scores_with(**probabilities: float) -> dict[str, Any]:
    """Build a runner envelope scoring the full candidate set.

    Taiga-S1 scores every enumerated action, so tests that omit an action are
    modelling an invalid upstream response, not a normal one.
    """
    values = dict.fromkeys(CANDIDATE_ACTIONS, 0.01)
    values.update(probabilities)
    return {
        "scores": [
            {"action": action, "probability": probability}
            for action, probability in values.items()
        ],
        "device": "cpu",
    }


class FakeExecutor:
    """Records executed actions and returns a bridge-like envelope."""

    def __init__(
        self,
        *,
        ok: bool = True,
        document_id: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._ok = ok
        self._document_id = document_id
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, action: str, document_id: str) -> Mapping[str, Any]:
        self.calls.append((action, document_id))
        if self._error is not None:
            raise self._error
        if not self._ok:
            return {"ok": False, "error": "FreeCAD operation failed"}
        result: dict[str, Any] = {}
        if self._document_id is not None:
            result["document_id"] = self._document_id
        return {"ok": True, "result": result}


def counting_state_provider(feature_counts: list[int] | None = None):
    """Return a state provider that reports a growing feature count."""
    calls: list[str] = []
    counts = feature_counts or []

    def provider(document_id: str) -> Mapping[str, Any]:
        index = len(calls)
        calls.append(document_id)
        feature_count = counts[index] if index < len(counts) else index
        return {
            "document_id": document_id,
            "object_names": [f"Feature{feature_count}"],
            "object_types": ["PartDesign::Feature"],
            "bbox": [0.0, 0.0, float(feature_count)],
            "feature_count": feature_count,
        }

    provider.calls = calls  # type: ignore[attr-defined]
    return provider


# ---------------------------------------------------------------------------
# Done must terminate without executing anything
# ---------------------------------------------------------------------------


def test_done_terminates_without_executing_any_operation() -> None:
    """'Done' ends the loop before the executor is ever consulted."""
    runtime = FakeTaigaRuntime(scores_with(Done=0.95))
    provider = counting_state_provider()
    executor = FakeExecutor()

    result = run_iterative_modeling(
        "finish the model",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=provider,
        executor=executor,
    )

    assert result.success is True
    assert result.completed_operations == ()
    assert executor.calls == [], "'Done' must not execute a modeling operation"
    assert result.iteration_count == 1
    assert TERMINATION_ACTION in result.final_detail
    assert len(provider.calls) == 1  # type: ignore[attr-defined]


def test_done_records_no_operation_even_after_real_work() -> None:
    """An executed action followed by 'Done' stops before a second execution."""
    runtime = FakeTaigaRuntime(
        scores_with(PartDesign_Pad=0.9),
        scores_with(Done=0.9),
    )
    provider = counting_state_provider([0, 1, 1])
    executor = FakeExecutor(document_id="doc-1")

    result = run_iterative_modeling(
        "pad then finish",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=provider,
        executor=executor,
    )

    assert result.success is True
    assert result.completed_operations == ("PartDesign_Pad",)
    assert executor.calls == [("PartDesign_Pad", "")]


# ---------------------------------------------------------------------------
# Fresh state after each successful operation
# ---------------------------------------------------------------------------


def test_state_is_recaptured_after_each_successful_operation() -> None:
    """Execution is followed by a fresh capture, and the new state is used."""
    runtime = FakeTaigaRuntime(
        scores_with(PartDesign_Pad=0.9),
        scores_with(Done=0.9),
    )
    provider = counting_state_provider([0, 1, 1])
    executor = FakeExecutor(document_id="doc-42")

    result = run_iterative_modeling(
        "pad then finish",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=provider,
        executor=executor,
    )

    # capture -> execute -> capture -> capture(again, before Done)
    assert len(provider.calls) == 3  # type: ignore[attr-defined]
    assert provider.calls[0] == ""  # type: ignore[attr-defined]
    # The post-execution capture uses the document id reported by the bridge.
    assert provider.calls[1] == "doc-42"  # type: ignore[attr-defined]
    # Taiga's second scoring call must see the refreshed state, not the old one.
    assert runtime.score_calls[1]["state"]["feature_count"] == 1
    assert result.final_state is not None
    assert result.final_state.feature_count == 1


# ---------------------------------------------------------------------------
# Explicit boundaries: nothing is fabricated
# ---------------------------------------------------------------------------


def test_missing_state_provider_reports_runtime_context_required() -> None:
    runtime = FakeTaigaRuntime(scores_with(PartDesign_Pad=0.9))

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.RUNTIME_CONTEXT_REQUIRED
    assert result.completed_operations == ()
    assert runtime.score_calls == [], "no scoring without a real state"


def test_missing_executor_reports_runtime_context_required() -> None:
    runtime = FakeTaigaRuntime(scores_with(PartDesign_Pad=0.9))

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.RUNTIME_CONTEXT_REQUIRED
    assert result.completed_operations == ()
    assert "PartDesign_Pad" in result.final_detail


def test_state_provider_that_is_not_a_mapping_is_rejected() -> None:
    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=FakeTaigaRuntime(scores_with(PartDesign_Pad=0.9)),  # type: ignore[arg-type]
        state_provider=lambda _doc: ["not", "a", "mapping"],  # type: ignore[arg-type,return-value]
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.RUNTIME_ERROR


# ---------------------------------------------------------------------------
# Execution failures
# ---------------------------------------------------------------------------


def test_failed_operation_stops_the_workflow() -> None:
    runtime = FakeTaigaRuntime(scores_with(PartDesign_Pad=0.9))

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(ok=False),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.RUNTIME_ERROR
    assert result.completed_operations == ()
    assert "FreeCAD operation failed" in result.error_detail


def test_executor_exception_is_reported_structurally() -> None:
    runtime = FakeTaigaRuntime(scores_with(PartDesign_Pad=0.9))

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(error=RuntimeError("bridge died")),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.RUNTIME_ERROR
    assert "bridge died" in result.error_detail


# ---------------------------------------------------------------------------
# Iteration limits and iteration counting
# ---------------------------------------------------------------------------


def test_max_iterations_stops_without_claiming_success() -> None:
    runtime = FakeTaigaRuntime(scores_with(PartDesign_Pad=0.9))

    result = run_iterative_modeling(
        "keep padding forever",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(),
        max_iterations=2,
    )

    assert result.success is False, "hitting the cap is not success"
    assert result.error_reason is None
    assert result.iteration_count == 2
    assert result.completed_operations == ("PartDesign_Pad", "PartDesign_Pad")
    assert "max_iterations" in result.final_detail


def test_zero_max_iterations_is_invalid() -> None:
    result = run_iterative_modeling("create a box", model=MODEL, max_iterations=0)

    assert result.success is False
    assert result.error_reason is UnavailableReason.INVALID_REQUEST
    assert result.iteration_count == 0


# ---------------------------------------------------------------------------
# Scoring validation (delegated to the real adapter)
# ---------------------------------------------------------------------------


def test_missing_model_reports_not_installed() -> None:
    result = run_iterative_modeling("create a box", settings=None)

    assert result.success is False
    assert result.error_reason is UnavailableReason.NOT_INSTALLED
    assert "FREECAD_MCP_TAIGA_MODEL" in result.final_detail


def test_partially_scored_actions_are_rejected() -> None:
    """Taiga must score every enumerated action; a partial reply is incompatible."""
    runtime = FakeTaigaRuntime(
        {"scores": [{"action": "PartDesign_Pad", "probability": 0.9}], "device": "cpu"}
    )

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.INCOMPATIBLE


def test_unrequested_action_in_scores_is_rejected() -> None:
    envelope = scores_with(PartDesign_Pad=0.9)
    envelope["scores"].append({"action": "Sketcher_CreateCircle", "probability": 0.5})
    runtime = FakeTaigaRuntime(envelope)

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.INCOMPATIBLE


def test_out_of_range_probability_is_rejected() -> None:
    runtime = FakeTaigaRuntime(scores_with(PartDesign_Pad=1.7))

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.INCOMPATIBLE


def test_runtime_timeout_is_reported_as_timeout() -> None:
    runtime = FakeTaigaRuntime(
        error=TaigaRuntimeTimeoutError("Taiga child timed out", timeout=1.0)
    )

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.TIMEOUT


def test_malformed_runner_envelope_is_incompatible() -> None:
    runtime = FakeTaigaRuntime({"unexpected": "shape"})

    result = run_iterative_modeling(
        "create a box",
        model=MODEL,
        runtime=runtime,  # type: ignore[arg-type]
        state_provider=counting_state_provider(),
        executor=FakeExecutor(),
    )

    assert result.success is False
    assert result.error_reason is UnavailableReason.INCOMPATIBLE


# ---------------------------------------------------------------------------
# Small units
# ---------------------------------------------------------------------------


def test_select_action_picks_highest_probability() -> None:
    from freecad_mcp.integrations.taiga.adapter import ActionScore

    scores = (
        ActionScore(action="PartDesign_Pad", probability=0.2),
        ActionScore(action="PartDesign_Fillet", probability=0.8),
        ActionScore(action="Done", probability=0.1),
    )

    assert _select_action(scores) == "PartDesign_Fillet"


def test_select_action_rejects_empty_scores() -> None:
    with pytest.raises(ValueError, match="empty"):
        _select_action(())


def test_workflow_state_json_roundtrip() -> None:
    original = WorkflowState(
        document_id="doc_001",
        object_names=("Box1", "Cyl1"),
        object_types=("Part::Box", "Part::Cylinder"),
        bbox=(0.0, 0.0, 5.0),
        feature_count=2,
    )

    restored = WorkflowState.from_json(original.as_json())

    assert restored == original


def test_workflow_state_tolerates_scalar_fields() -> None:
    """A single object name must not be exploded into characters."""
    state = WorkflowState.from_json(
        {"document_id": "d", "object_names": "Box1", "object_types": "Part::Box",
         "bbox": "abc", "feature_count": 1}
    )

    assert state.object_names == ("Box1",)
    assert state.object_types == ("Part::Box",)
    assert state.bbox is None
    assert state.feature_count == 1
