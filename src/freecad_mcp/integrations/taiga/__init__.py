"""Optional Taiga-S1 integration.

Importing this package does not import Taiga or Torch. The adapter detects
availability lazily and can score actions only when both are installed.
"""

from __future__ import annotations

from .adapter import ActionScore, ActionScoringResult, TaigaBackend

__all__ = [
    "ActionScore",
    "ActionScoringResult",
    "TaigaBackend",
]
