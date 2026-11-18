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
from .benchmark import (
    BenchmarkEpisodeSummary,
    BenchmarkResult,
    BenchmarkSaveReopen,
    BenchmarkScenario,
    BenchmarkValidation,
    classify_result,
    resolve_artifact_path,
    run_benchmark_scenario,
    validate_geometry,
)
from .runtime import TaigaRuntime, TaigaRuntimeError

__all__ = [
    "ActionScore",
    "ActionScoringResult",
    "BenchmarkEpisodeSummary",
    "BenchmarkResult",
    "BenchmarkSaveReopen",
    "BenchmarkScenario",
    "BenchmarkValidation",
    "TaigaBackend",
    "TaigaRuntime",
    "TaigaRuntimeError",
    "TaigaStatusReport",
    "classify_result",
    "resolve_artifact_path",
    "run_benchmark_scenario",
    "validate_geometry",
]
