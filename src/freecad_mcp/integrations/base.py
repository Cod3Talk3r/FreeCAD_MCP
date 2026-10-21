"""Minimal contract for optional integration backends.

This module is deliberately dependency-free: it must not import FreeCAD,
Taiga, Torch, or any other optional backend. A backend advertises its status
and can return a *data-only* plan; nothing in this contract executes commands
inside FreeCAD.

The contract distinguishes "a plan was generated" from "no plan is
available". A backend that cannot plan must say why, instead of returning an
empty plan that could be mistaken for success.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "IntegrationStatus",
    "PlanStep",
    "PlanningBackend",
    "PlanningResult",
    "UnavailableReason",
]


class UnavailableReason(str, Enum):
    """Structured reasons an optional backend cannot provide a result."""

    NOT_INSTALLED = "not_installed"
    """The backend's distribution or importable package is absent."""

    MISSING_DEPENDENCY = "missing_dependency"
    """A required dependency (for example Torch) is absent."""

    INCOMPATIBLE = "incompatible"
    """The installed backend or its API does not match the verified contract."""

    RUNTIME_CONTEXT_REQUIRED = "runtime_context_required"
    """The backend needs live state or an execution loop that is not available."""

    INVALID_REQUEST = "invalid_request"
    """The caller supplied input the backend cannot use."""


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    """Availability of one optional backend.

    An available status never carries a reason; an unavailable status always
    carries one. This keeps "works" and "does not work" unambiguous.
    """

    name: str
    is_available: bool
    reason: UnavailableReason | None = None
    detail: str = ""
    version: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("integration name must not be empty")
        if self.is_available and self.reason is not None:
            raise ValueError("an available integration must not carry an unavailable reason")
        if not self.is_available and self.reason is None:
            raise ValueError("an unavailable integration must carry an unavailable reason")

    @classmethod
    def available(cls, name: str, *, version: str | None = None) -> IntegrationStatus:
        """Build an available status."""
        return cls(name=name, is_available=True, version=version)

    @classmethod
    def unavailable(
        cls,
        name: str,
        reason: UnavailableReason,
        *,
        detail: str = "",
        version: str | None = None,
    ) -> IntegrationStatus:
        """Build an unavailable status with a structured reason."""
        return cls(
            name=name,
            is_available=False,
            reason=reason,
            detail=detail,
            version=version,
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    """One explicit, declarative step in a plan.

    A step is data, not an executed operation. ``operation`` names the
    intended action (for example a bridge operation or a backend command id),
    and ``parameters`` carries its arguments unchanged.
    """

    operation: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        if not self.operation.strip():
            raise ValueError("plan step operation must not be empty")


@dataclass(frozen=True, slots=True)
class PlanningResult:
    """Outcome of asking a backend for a plan.

    ``planned`` is True only when the backend actually produced a plan, in
    which case ``steps`` must be non-empty and ``reason`` must be unset. When
    ``planned`` is False, the result carries a structured reason and contains
    no steps.
    """

    backend: str
    planned: bool
    steps: tuple[PlanStep, ...] = ()
    reason: UnavailableReason | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.backend.strip():
            raise ValueError("planning result backend must not be empty")
        if self.planned:
            if self.reason is not None:
                raise ValueError("a generated plan must not carry an unavailable reason")
            if not self.steps:
                raise ValueError("a generated plan must contain at least one step")
        else:
            if self.reason is None:
                raise ValueError("a plan that was not generated must carry an unavailable reason")
            if self.steps:
                raise ValueError("a plan that was not generated must not contain steps")

    @classmethod
    def generated(
        cls,
        backend: str,
        steps: tuple[PlanStep, ...],
        *,
        detail: str = "",
    ) -> PlanningResult:
        """Build a result for a plan the backend actually generated."""
        return cls(backend=backend, planned=True, steps=steps, detail=detail)

    @classmethod
    def unavailable(
        cls,
        backend: str,
        reason: UnavailableReason,
        *,
        detail: str = "",
    ) -> PlanningResult:
        """Build a result for a plan the backend could not generate."""
        return cls(backend=backend, planned=False, reason=reason, detail=detail)


class PlanningBackend(ABC):
    """Contract for an optional planning backend.

    Implementations must be import-safe when their optional dependencies are
    absent. ``plan`` returns a :class:`PlanningResult`; it must never execute
    model-produced commands.
    """

    name: str

    @abstractmethod
    def status(self) -> IntegrationStatus:
        """Return the backend's current availability."""

    @abstractmethod
    def plan(self, goal: str) -> PlanningResult:
        """Return an explicit plan for ``goal``, or a structured unavailable result."""
