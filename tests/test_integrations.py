"""Tests for the dependency-free integration contract."""

from __future__ import annotations

import pytest

from freecad_mcp.integrations import (
    IntegrationStatus,
    PlanStep,
    PlanningBackend,
    PlanningResult,
    UnavailableReason,
)


class _FakeBackend(PlanningBackend):
    """A small backend proving the contract works without any optional dependency."""

    name = "fake"

    def __init__(self) -> None:
        self.goals: list[str] = []

    def status(self) -> IntegrationStatus:
        return IntegrationStatus.available(self.name, version="1.0")

    def plan(self, goal: str) -> PlanningResult:
        self.goals.append(goal)
        return PlanningResult.generated(
            self.name,
            (
                PlanStep("create_document", {"document_id": goal}, "Create the target document."),
                PlanStep("create_box", {"length": 10.0, "width": 10.0, "height": 10.0}),
            ),
        )


def test_available_status_has_no_reason() -> None:
    status = IntegrationStatus.available("fake", version="1.0")
    assert status.is_available is True
    assert status.reason is None
    assert status.version == "1.0"


def test_unavailable_status_requires_a_structured_reason() -> None:
    with pytest.raises(ValueError, match="must carry an unavailable reason"):
        IntegrationStatus(name="fake", is_available=False)

    status = IntegrationStatus.unavailable(
        "fake",
        UnavailableReason.NOT_INSTALLED,
        detail="not here",
    )
    assert status.is_available is False
    assert status.reason is UnavailableReason.NOT_INSTALLED
    assert status.detail == "not here"


def test_available_status_rejects_a_reason() -> None:
    with pytest.raises(ValueError, match="must not carry"):
        IntegrationStatus(
            name="fake",
            is_available=True,
            reason=UnavailableReason.NOT_INSTALLED,
        )


def test_plan_step_requires_an_operation() -> None:
    with pytest.raises(ValueError, match="operation"):
        PlanStep("")


def test_generated_plan_requires_at_least_one_step() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        PlanningResult.generated("fake", ())

    with pytest.raises(ValueError, match="must not carry"):
        PlanningResult(
            backend="fake",
            planned=True,
            steps=(PlanStep("noop"),),
            reason=UnavailableReason.INCOMPATIBLE,
        )


def test_unavailable_plan_requires_a_reason_and_no_steps() -> None:
    with pytest.raises(ValueError, match="must carry an unavailable reason"):
        PlanningResult(backend="fake", planned=False)

    with pytest.raises(ValueError, match="must not contain steps"):
        PlanningResult(
            backend="fake",
            planned=False,
            steps=(PlanStep("noop"),),
            reason=UnavailableReason.INCOMPATIBLE,
        )


def test_fake_backend_returns_explicit_data_only_steps() -> None:
    backend = _FakeBackend()

    assert isinstance(backend, PlanningBackend)
    assert backend.status() == IntegrationStatus.available("fake", version="1.0")

    result = backend.plan("demo")

    assert backend.goals == ["demo"]
    assert result.planned is True
    assert result.reason is None
    assert [step.operation for step in result.steps] == ["create_document", "create_box"]
    assert result.steps[0].parameters == {"document_id": "demo"}
    assert result.steps[1].parameters == {"length": 10.0, "width": 10.0, "height": 10.0}
