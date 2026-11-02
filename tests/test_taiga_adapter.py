"""Tests for the optional Taiga-S1 adapter.

The real Taiga-S1 package and Torch are intentionally not installed. The
adapter is exercised with an injected fake runtime so its public contract,
score validation, and error mapping can be tested without a heavyweight
optional dependency or a real subprocess.
"""

from __future__ import annotations

import math
import subprocess
import sys
from typing import Any

import pytest

from freecad_mcp.integrations import (
    IntegrationStatus,
    PlanningBackend,
    UnavailableReason,
)
from freecad_mcp.integrations.taiga import (
    ActionScore,
    ActionScoringResult,
    TaigaBackend,
    TaigaStatusReport,
)
from freecad_mcp.integrations.taiga.runtime import (
    TaigaRuntimeExecutionError,
    TaigaRuntimeNotConfiguredError,
    TaigaRuntimeOperationError,
    TaigaRuntimeResponseError,
    TaigaRuntimeTimeoutError,
)

_STATE = {"doc_open": True, "workbench": "PartDesignWorkbench"}
_GOAL = {"features": [{"kind": "base_box", "params": {"w": 40.0}}]}
_ACTIONS = ["PartDesign_Body", "Std_New"]


class FakeRuntime:
    """Minimal stand-in for :class:`TaigaRuntime`."""

    def __init__(
        self,
        *,
        status: Any = None,
        scores: Any = None,
        status_error: BaseException | None = None,
        score_error: BaseException | None = None,
    ) -> None:
        self.status_result = (
            {
                "available": True,
                "reason": None,
                "detail": "",
                "version": "0.1.0",
                "python_version": "3.11.9",
                "torch_version": "2.4.0",
                "model": None,
                "model_available": None,
                "model_error": None,
            }
            if status is None
            else status
        )
        self.score_result = (
            {
                "scores": [
                    {"action": "Std_New", "probability": 0.75},
                    {"action": "PartDesign_Body", "probability": 0.25},
                ],
                "device": "cpu",
            }
            if scores is None
            else scores
        )
        self.status_error = status_error
        self.score_error = score_error
        self.status_calls: list[str | None] = []
        self.score_calls: list[tuple[Any, Any, list[str], str, str]] = []

    def status(self, *, model: str | None = None) -> Any:
        self.status_calls.append(model)
        if self.status_error is not None:
            raise self.status_error
        return self.status_result

    def score_actions(
        self,
        state: Any,
        goal: Any,
        actions: list[str],
        *,
        model: str,
        device: str,
    ) -> Any:
        self.score_calls.append((state, goal, list(actions), model, device))
        if self.score_error is not None:
            raise self.score_error
        return self.score_result


def _backend(runtime: FakeRuntime, **kwargs: Any) -> TaigaBackend:
    return TaigaBackend(
        python="C:/taiga/python.exe",
        model="C:/models/taiga",
        runtime=runtime,
        **kwargs,
    )


def test_core_and_adapter_import_without_taiga_or_torch() -> None:
    """A fresh interpreter must import the server and tools with both blocked."""
    source = """
import sys

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "torch" or name == "freecad_s1" or name.startswith("freecad_s1."):
            raise ImportError("blocked optional dependency: " + name)
        return None

sys.meta_path.insert(0, Blocker())

import freecad_mcp
import freecad_mcp.server
import freecad_mcp.integrations
import freecad_mcp.integrations.taiga
import freecad_mcp.tools
import freecad_mcp.tools.taiga

assert "torch" not in sys.modules
assert "freecad_s1" not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_taiga_backend_implements_the_contract() -> None:
    assert isinstance(TaigaBackend(), PlanningBackend)


def test_unconfigured_interpreter_is_reported_as_structured_unavailable() -> None:
    backend = TaigaBackend()

    status = backend.status()

    assert isinstance(status, IntegrationStatus)
    assert status.is_available is False
    assert status.reason is UnavailableReason.NOT_INSTALLED
    assert "FREECAD_MCP_TAIGA_PYTHON" in status.detail

    plan = backend.plan("a plate")
    assert plan.planned is False
    assert plan.steps == ()
    assert plan.reason is UnavailableReason.NOT_INSTALLED


def test_status_report_preserves_contract_and_adds_details() -> None:
    runtime = FakeRuntime(
        status={
            "available": True,
            "reason": None,
            "detail": "",
            "version": "0.1.0",
            "python_version": "3.11.9",
            "torch_version": "2.4.0",
            "model": "C:/models/taiga",
            "model_available": True,
            "model_error": None,
        }
    )

    report = _backend(runtime).status_report()

    assert isinstance(report, TaigaStatusReport)
    assert report.status.is_available is True
    assert report.status.version == "0.1.0"
    assert report.python == "C:/taiga/python.exe"
    assert report.model == "C:/models/taiga"
    assert report.python_version == "3.11.9"
    assert report.torch_version == "2.4.0"
    assert report.model_available is True
    assert report.model_error is None
    assert runtime.status_calls == ["C:/models/taiga"]


def test_status_never_loads_weights_and_reports_model_problem() -> None:
    runtime = FakeRuntime(
        status={
            "available": True,
            "reason": None,
            "detail": "",
            "version": None,
            "python_version": "3.11.9",
            "torch_version": None,
            "model": "C:/models/taiga",
            "model_available": False,
            "model_error": "Model directory does not exist: C:/models/taiga",
        }
    )

    report = _backend(runtime).status_report()

    assert report.status.is_available is True
    assert report.model_available is False
    assert "does not exist" in (report.model_error or "")
    assert runtime.score_calls == []


def test_unavailable_status_maps_child_reason() -> None:
    runtime = FakeRuntime(
        status={
            "available": False,
            "reason": "missing_dependency",
            "detail": "The Taiga runtime is missing required Python packages: torch.",
            "version": None,
            "python_version": "3.11.9",
            "torch_version": None,
            "model": None,
            "model_available": None,
            "model_error": None,
        }
    )

    report = _backend(runtime).status_report()

    assert report.status.is_available is False
    assert report.status.reason is UnavailableReason.MISSING_DEPENDENCY
    assert "torch" in report.status.detail


def test_unavailable_status_with_unknown_reason_is_incompatible() -> None:
    runtime = FakeRuntime(
        status={
            "available": False,
            "reason": "something-new",
            "detail": "unknown",
            "version": None,
            "python_version": None,
            "torch_version": None,
            "model": None,
            "model_available": None,
            "model_error": None,
        }
    )

    report = _backend(runtime).status_report()

    assert report.status.reason is UnavailableReason.INCOMPATIBLE


def test_malformed_status_is_incompatible() -> None:
    report = _backend(FakeRuntime(status={"available": "yes"})).status_report()

    assert report.status.is_available is False
    assert report.status.reason is UnavailableReason.INCOMPATIBLE
    assert "malformed" in report.status.detail


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TaigaRuntimeTimeoutError("slow", timeout=1.0), UnavailableReason.TIMEOUT),
        (TaigaRuntimeExecutionError("crashed"), UnavailableReason.RUNTIME_ERROR),
        (TaigaRuntimeResponseError("bad json"), UnavailableReason.RUNTIME_ERROR),
        (TaigaRuntimeNotConfiguredError("no python"), UnavailableReason.NOT_INSTALLED),
        (
            TaigaRuntimeOperationError("missing torch", error_type="MissingDependency"),
            UnavailableReason.MISSING_DEPENDENCY,
        ),
        (
            TaigaRuntimeOperationError("bad api", error_type="Incompatible"),
            UnavailableReason.INCOMPATIBLE,
        ),
        (
            TaigaRuntimeOperationError("not installed", error_type="NotInstalled"),
            UnavailableReason.NOT_INSTALLED,
        ),
    ],
)
def test_status_maps_runtime_failures(
    error: BaseException,
    reason: UnavailableReason,
) -> None:
    report = _backend(FakeRuntime(status_error=error)).status_report()

    assert report.status.is_available is False
    assert report.status.reason is reason
    assert report.status.detail


def test_plan_is_deferred_even_when_taiga_is_available() -> None:
    result = _backend(FakeRuntime()).plan("a plate with one hole")

    assert result.planned is False
    assert result.steps == ()
    assert result.reason is UnavailableReason.RUNTIME_CONTEXT_REQUIRED
    assert "live FreeCAD State" in result.detail
    assert "no model-produced command was executed" in result.detail


def test_plan_reports_the_runtime_failure_when_unavailable() -> None:
    result = _backend(
        FakeRuntime(status_error=TaigaRuntimeTimeoutError("slow", timeout=1.0))
    ).plan("a plate")

    assert result.planned is False
    assert result.reason is UnavailableReason.TIMEOUT


def test_score_actions_uses_the_runtime_without_executing() -> None:
    runtime = FakeRuntime()
    backend = _backend(runtime, device="cpu")

    result = backend.score_actions(_STATE, _GOAL, _ACTIONS)

    assert result.scored is True
    assert result.reason is None
    assert [score.action for score in result.scores] == ["Std_New", "PartDesign_Body"]
    assert [score.probability for score in result.scores] == [0.75, 0.25]
    assert runtime.score_calls == [
        (_STATE, _GOAL, _ACTIONS, "C:/models/taiga", "cpu")
    ]


def test_score_actions_requires_a_configured_model() -> None:
    runtime = FakeRuntime()
    backend = TaigaBackend(python="C:/taiga/python.exe", runtime=runtime)

    result = backend.score_actions(_STATE, _GOAL, _ACTIONS)

    assert result.scored is False
    assert result.reason is UnavailableReason.NOT_INSTALLED
    assert "FREECAD_MCP_TAIGA_MODEL" in result.detail
    assert runtime.score_calls == []


@pytest.mark.parametrize(
    "actions",
    [
        [],
        ["Std_New", "Std_New"],
        ["Std_New", ""],
        ["Std_New", "   "],
        ["Std_New", 3],
        "Std_New",
        None,
    ],
)
def test_invalid_actions_are_rejected(actions: Any) -> None:
    runtime = FakeRuntime()

    result = _backend(runtime).score_actions(_STATE, _GOAL, actions)

    assert result.scored is False
    assert result.reason is UnavailableReason.INVALID_REQUEST
    assert "distinct" in result.detail
    assert runtime.score_calls == []


@pytest.mark.parametrize("state,goal", [([], _GOAL), (_STATE, []), (None, _GOAL)])
def test_non_object_state_or_goal_is_rejected(state: Any, goal: Any) -> None:
    runtime = FakeRuntime()

    result = _backend(runtime).score_actions(state, goal, _ACTIONS)

    assert result.scored is False
    assert result.reason is UnavailableReason.INVALID_REQUEST
    assert "JSON objects" in result.detail
    assert runtime.score_calls == []


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TaigaRuntimeTimeoutError("slow", timeout=1.0), UnavailableReason.TIMEOUT),
        (TaigaRuntimeExecutionError("crashed"), UnavailableReason.RUNTIME_ERROR),
        (TaigaRuntimeResponseError("bad json"), UnavailableReason.RUNTIME_ERROR),
        (
            TaigaRuntimeOperationError("invalid state", error_type="InvalidRequest"),
            UnavailableReason.INVALID_REQUEST,
        ),
    ],
)
def test_scoring_runtime_failures_are_structured(
    error: BaseException,
    reason: UnavailableReason,
) -> None:
    result = _backend(FakeRuntime(score_error=error)).score_actions(
        _STATE, _GOAL, _ACTIONS
    )

    assert result.scored is False
    assert result.scores == ()
    assert result.reason is reason
    assert result.detail


def test_non_mapping_scoring_result_is_incompatible() -> None:
    result = _backend(FakeRuntime(scores=["nope"])).score_actions(
        _STATE, _GOAL, _ACTIONS
    )

    assert result.scored is False
    assert result.reason is UnavailableReason.INCOMPATIBLE
    assert "malformed" in result.detail


@pytest.mark.parametrize(
    "scores",
    [
        "nope",
        [{"action": "Std_New", "probability": 1.0}],
        [
            {"action": "Std_New", "probability": 0.5},
            {"action": "Std_New", "probability": 0.5},
        ],
        [
            {"action": "Std_New", "probability": 0.5},
            {"action": "PartDesign_Body", "probability": 0.5},
            {"action": "Bogus", "probability": 0.0},
        ],
        [
            {"action": "Std_New", "probability": 0.5},
            {"action": "PartDesign_Body", "probability": 1.5},
        ],
        [
            {"action": "Std_New", "probability": 0.5},
            {"action": "PartDesign_Body", "probability": float("nan")},
        ],
        [
            {"action": "Std_New", "probability": 0.5},
            {"action": "PartDesign_Body", "probability": True},
        ],
        [
            {"action": "Std_New", "probability": 0.5},
            {"action": "PartDesign_Body", "probability": "high"},
        ],
        ["not-an-object"],
        [
            {"action": "Std_New", "probability": 0.5},
            {"probability": 0.5},
        ],
    ],
)
def test_malformed_scores_are_rejected(scores: Any) -> None:
    result = _backend(FakeRuntime(scores={"scores": scores})).score_actions(
        _STATE, _GOAL, _ACTIONS
    )

    assert result.scored is False
    assert result.scores == ()
    assert result.reason is UnavailableReason.INCOMPATIBLE


def test_missing_scores_key_is_rejected() -> None:
    result = _backend(FakeRuntime(scores={})).score_actions(_STATE, _GOAL, _ACTIONS)

    assert result.scored is False
    assert result.reason is UnavailableReason.INCOMPATIBLE


def test_action_score_rejects_invalid_probabilities() -> None:
    with pytest.raises(ValueError, match="probability"):
        ActionScore("Std_New", 1.5)

    with pytest.raises(ValueError, match="probability"):
        ActionScore("Std_New", float("nan"))

    with pytest.raises(ValueError, match="action"):
        ActionScore("   ", 0.5)


def test_scored_result_requires_scores_and_no_reason() -> None:
    with pytest.raises(ValueError, match="at least one score"):
        ActionScoringResult.scored_actions("taiga-s1", ())

    result = ActionScoringResult.unavailable(
        "taiga-s1",
        UnavailableReason.INVALID_REQUEST,
        detail="bad input",
    )
    assert result.scored is False
    assert result.scores == ()

    with pytest.raises(ValueError, match="must not carry"):
        ActionScoringResult(
            backend="taiga-s1",
            scored=True,
            scores=(ActionScore("Std_New", 0.5),),
            reason=UnavailableReason.INVALID_REQUEST,
        )


def test_action_score_accepts_boundary_probabilities() -> None:
    assert math.isclose(ActionScore("Std_New", 0.0).probability, 0.0)
    assert math.isclose(ActionScore("Std_New", 1.0).probability, 1.0)
