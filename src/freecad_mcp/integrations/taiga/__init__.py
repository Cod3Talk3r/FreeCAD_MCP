"""Optional Taiga-S1 integration.

Importing this package does not import Taiga or Torch. The adapter runs the
optional runtime in an isolated child process and reports structured
availability when it is absent.
"""

from __future__ import annotations

from .adapter import (
    ActionScore,
    ActionScoringResult,
    TaigaBackend,
    TaigaStatusReport,
)
from .runtime import TaigaRuntime, TaigaRuntimeError

__all__ = [
    "ActionScore",
    "ActionScoringResult",
    "TaigaBackend",
    "TaigaRuntime",
    "TaigaRuntimeError",
    "TaigaStatusReport",
]
