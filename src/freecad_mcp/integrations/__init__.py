"""Optional integration backends.

Importing this package is always safe: it exposes the dependency-free
contract only. Backend implementations live in subpackages and must keep
their optional dependencies lazy.
"""

from __future__ import annotations

from .base import (
    IntegrationStatus,
    PlanStep,
    PlanningBackend,
    PlanningResult,
    UnavailableReason,
)

__all__ = [
    "IntegrationStatus",
    "PlanStep",
    "PlanningBackend",
    "PlanningResult",
    "UnavailableReason",
]
