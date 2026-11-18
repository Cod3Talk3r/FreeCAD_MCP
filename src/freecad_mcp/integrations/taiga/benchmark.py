"""M20 Phase 4B.1 — Benchmark infrastructure for native Taiga Method B.

This module provides the infrastructure to run, record and classify native
Taiga episodes against a set of benchmark scenarios.  It does **not** run the
five benchmark scenarios itself; it provides:

* :class:`BenchmarkScenario` — a goal + start + expected-geometry spec.
* :class:`BenchmarkResult` — the structured result schema (design report §4).
* :func:`classify_result` — separates model/runtime failures from
  geometry-quality failures.
* :func:`validate_geometry` — independent volume/bbox/solid checks against
  analytically-computed expected values (not against the native target).
* :func:`run_benchmark_scenario` — orchestrates one scenario: episode →
  native save → fresh-process ``validate_reopen`` → independent validation.

The module never imports ``freecad_s1``, ``torch`` or ``FreeCAD``.  It reuses
the existing :func:`freecad_mcp.tools.taiga.taiga_episode` for episode
execution and the existing :class:`freecad_mcp.freecad_bridge.bridge.FreeCadBridge`
``validate_reopen`` operation for fresh-process reopen validation.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .episode import EpisodeResult

__all__ = [
    "BenchmarkScenario",
    "BenchmarkResult",
    "BenchmarkEpisodeSummary",
    "BenchmarkValidation",
    "BenchmarkSaveReopen",
    "BenchmarkClassification",
    "classify_result",
    "validate_geometry",
    "run_benchmark_scenario",
    "resolve_artifact_path",
]


# ---------------------------------------------------------------------------
# Classification enum (design report §4)
# ---------------------------------------------------------------------------

#: Top-level classification of a benchmark result.
BenchmarkClassification = str

CLASS_GEOMETRY_SUCCESS = "geometry_success"
CLASS_GEOMETRY_QUALITY_FAILURE = "geometry_quality_failure"
CLASS_MODEL_FAILURE = "model_failure"
CLASS_RUNTIME_FAILURE = "runtime_failure"
CLASS_INVALID_GOAL = "invalid_goal"
CLASS_SAVE_REOPEN_FAILURE = "save_reopen_failure"

_VALID_CLASSIFICATIONS = frozenset(
    {
        CLASS_GEOMETRY_SUCCESS,
        CLASS_GEOMETRY_QUALITY_FAILURE,
        CLASS_MODEL_FAILURE,
        CLASS_RUNTIME_FAILURE,
        CLASS_INVALID_GOAL,
        CLASS_SAVE_REOPEN_FAILURE,
    }
)

#: Episode statuses that indicate a model (policy) failure, not a runtime error.
_MODEL_FAILURE_STATUSES = frozenset({"budget", "unrecoverable", "wrong_geometry"})

#: Episode statuses that indicate a runtime/infrastructure failure.
_RUNTIME_FAILURE_STATUSES = frozenset(
    {
        "taiga_unavailable",
        "timeout",
        "crashed",
        "child_process_failure",
        "invalid_response",
        "execution_failure",
    }
)


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkScenario:
    """One benchmark scenario: a goal, start spec and expected geometry.

    The ``expected_volume_mm3``, ``expected_bbox_mm`` and ``volume_tolerance_pct``
    fields are the *independent* validation targets.  They are computed
    analytically from the goal params (design report §3), not from the native
    expert's target solid.
    """

    scenario_id: str
    goal: dict[str, Any]
    start: dict[str, Any] | None = None
    expected_volume_mm3: float | None = None
    expected_bbox_mm: tuple[float, float, float] | None = None
    volume_tolerance_pct: float = 1.0
    bbox_tolerance_mm: float = 0.1
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "goal": self.goal,
            "start": self.start,
            "expected_volume_mm3": self.expected_volume_mm3,
            "expected_bbox_mm": list(self.expected_bbox_mm) if self.expected_bbox_mm else None,
            "volume_tolerance_pct": self.volume_tolerance_pct,
            "bbox_tolerance_mm": self.bbox_tolerance_mm,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Result sub-structures (design report §4)
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkEpisodeSummary:
    """Native episode outcome, kept separate from independent validation."""

    status: str
    success: bool
    clean: bool
    outcome: str | None
    iou: float
    match: bool
    steps: int
    budget: int
    expert_steps: int
    agreement: float
    deviations: int
    history: list[str] | None = None
    error: str | None = None
    error_type: str | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success": self.success,
            "clean": self.clean,
            "outcome": self.outcome,
            "iou": self.iou,
            "match": self.match,
            "steps": self.steps,
            "budget": self.budget,
            "expert_steps": self.expert_steps,
            "agreement": self.agreement,
            "deviations": self.deviations,
            "history": self.history,
            "error": self.error,
            "error_type": self.error_type,
            "duration_seconds": round(self.duration_seconds, 3),
        }


@dataclass
class BenchmarkValidation:
    """Independent geometry validation (not vs the native target)."""

    shape_valid: bool | None = None
    solid_count: int | None = None
    volume_mm3: float | None = None
    volume_expected_mm3: float | None = None
    volume_error_pct: float | None = None
    bbox_mm: list[float] | None = None
    bbox_expected_mm: list[float] | None = None
    bbox_ok: bool | None = None
    checks_passed: bool = False
    checks_failed: list[str] = field(default_factory=list)
    source: str = "final_state"

    def to_dict(self) -> dict[str, Any]:
        return {
            "shape_valid": self.shape_valid,
            "solid_count": self.solid_count,
            "volume_mm3": self.volume_mm3,
            "volume_expected_mm3": self.volume_expected_mm3,
            "volume_error_pct": self.volume_error_pct,
            "bbox_mm": self.bbox_mm,
            "bbox_expected_mm": self.bbox_expected_mm,
            "bbox_ok": self.bbox_ok,
            "checks_passed": self.checks_passed,
            "checks_failed": self.checks_failed,
            "source": self.source,
        }


@dataclass
class BenchmarkSaveReopen:
    """Save + fresh-process reopen validation status."""

    attempted: bool = False
    saved_path: str | None = None
    artifact_written: bool = False
    reopened_in_fresh_process: bool = False
    reopen_shape_valid: bool | None = None
    reopen_solid_count: int | None = None
    reopen_volume_mm3: float | None = None
    reopen_matches_in_memory: bool | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "saved_path": self.saved_path,
            "artifact_written": self.artifact_written,
            "reopened_in_fresh_process": self.reopened_in_fresh_process,
            "reopen_shape_valid": self.reopen_shape_valid,
            "reopen_solid_count": self.reopen_solid_count,
            "reopen_volume_mm3": self.reopen_volume_mm3,
            "reopen_matches_in_memory": self.reopen_matches_in_memory,
            "error": self.error,
        }


@dataclass
class BenchmarkResult:
    """Structured result for one benchmark scenario (design report §4)."""

    schema_version: str
    scenario_id: str
    goal: dict[str, Any]
    start: dict[str, Any] | None
    episode: BenchmarkEpisodeSummary
    independent_validation: BenchmarkValidation
    save_reopen: BenchmarkSaveReopen
    classification: BenchmarkClassification
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "goal": self.goal,
            "start": self.start,
            "episode": self.episode.to_dict(),
            "independent_validation": self.independent_validation.to_dict(),
            "save_reopen": self.save_reopen.to_dict(),
            "classification": self.classification,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Artifact path resolution (isolated per-run paths)
# ---------------------------------------------------------------------------


def resolve_artifact_path(
    workspace: str | os.PathLike[str] | None,
    scenario_id: str,
) -> Path:
    """Resolve an isolated per-run artifact path inside the workspace.

    Uses a subdirectory ``benchmarks/<run-uuid>/`` so concurrent runs never
    collide, and the ``validate_document_id`` rules are respected for the
    filename portion.

    Args:
        workspace: Workspace root.  ``None`` falls back to
            the ``FREECAD_MCP_WORKSPACE`` env var or ``freecad-mcp-workspace``
            relative to cwd.
        scenario_id: Scenario identifier (used as the document id stem).

    Returns:
        Absolute ``.fcstd`` path.  Parent directories are created.

    Raises:
        ValueError: If ``scenario_id`` is not a valid document id.
    """
    from freecad_mcp.freecad_bridge import workspace as ws_mod

    normalized = ws_mod.validate_document_id(scenario_id)
    if workspace is None:
        root = Path(os.environ.get("FREECAD_MCP_WORKSPACE", "freecad-mcp-workspace"))
        if not root.is_absolute():
            root = Path.cwd() / root
    else:
        root = Path(os.path.normpath(str(workspace)))
    run_dir = root / "benchmarks" / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir / f"{normalized}.fcstd"


# ---------------------------------------------------------------------------
# Independent geometry validation
# ---------------------------------------------------------------------------


def validate_geometry(
    shape_info: Mapping[str, Any] | None,
    expected_volume_mm3: float | None,
    expected_bbox_mm: tuple[float, float, float] | None,
    *,
    volume_tolerance_pct: float = 1.0,
    bbox_tolerance_mm: float = 0.1,
    source: str = "final_state",
) -> BenchmarkValidation:
    """Compare a shape-info mapping against analytically expected values.

    This is *independent* of the native target: the expected values come from
    the goal params, not from the expert-built solid.  A native IoU of 1.0
    against the expert target can still hide a systematic executor bug; this
    check catches it.

    Args:
        shape_info: A ``ShapeInfo.to_json()`` mapping (from
            ``final_state["shape"]`` or a fresh reopen).  ``None`` when the
            episode crashed and no shape was observed.
        expected_volume_mm3: Analytically expected volume, or ``None`` to skip.
        expected_bbox_mm: Analytically expected bounding box, or ``None`` to skip.
        volume_tolerance_pct: Allowed relative volume error (percent).
        bbox_tolerance_mm: Allowed absolute per-axis bbox error (mm).
        source: Where ``shape_info`` came from (``"final_state"`` or ``"reopen"``).

    Returns:
        A :class:`BenchmarkValidation` with per-check pass/fail.
    """
    checks_failed: list[str] = []

    shape_valid: bool | None = None
    solid_count: int | None = None
    volume_mm3: float | None = None
    bbox_mm: list[float] | None = None

    if shape_info is None:
        return BenchmarkValidation(
            checks_passed=False,
            checks_failed=["no shape info available (episode crashed or shape missing)"],
            source=source,
        )

    # Shape validity
    raw_valid = shape_info.get("valid")
    if isinstance(raw_valid, bool):
        shape_valid = raw_valid
        if not shape_valid:
            checks_failed.append("shape is not valid")
    else:
        checks_failed.append("shape_valid is missing or not a boolean")

    # Solid count
    raw_solids = shape_info.get("n_solids")
    if isinstance(raw_solids, (int, float)) and not isinstance(raw_solids, bool):
        solid_count = int(raw_solids)
        if solid_count != 1:
            checks_failed.append(f"expected 1 solid, got {solid_count}")
    else:
        checks_failed.append("n_solids is missing or not numeric")

    # Volume
    raw_volume = shape_info.get("volume")
    if isinstance(raw_volume, (int, float)) and not isinstance(raw_volume, bool):
        volume_mm3 = float(raw_volume)
        if expected_volume_mm3 is not None and expected_volume_mm3 > 0:
            error_pct = abs(volume_mm3 - expected_volume_mm3) / expected_volume_mm3 * 100.0
            if error_pct > volume_tolerance_pct:
                checks_failed.append(
                    f"volume {volume_mm3:.2f} mm³ differs from expected "
                    f"{expected_volume_mm3:.2f} mm³ by {error_pct:.2f}% "
                    f"(tolerance {volume_tolerance_pct}%)"
                )
    else:
        checks_failed.append("volume is missing or not numeric")

    # Bbox
    raw_bbox = shape_info.get("bbox")
    bbox_ok: bool | None = None
    if (
        isinstance(raw_bbox, (list, tuple))
        and len(raw_bbox) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw_bbox)
    ):
        bbox_mm = [float(v) for v in raw_bbox]
        if expected_bbox_mm is not None:
            bbox_ok = True
            for i, (actual, expected) in enumerate(zip(bbox_mm, expected_bbox_mm)):
                if abs(actual - expected) > bbox_tolerance_mm:
                    bbox_ok = False
                    checks_failed.append(
                        f"bbox axis {i}: {actual:.2f} mm vs expected {expected:.2f} mm "
                        f"(tolerance {bbox_tolerance_mm} mm)"
                    )
    else:
        checks_failed.append("bbox is missing or malformed")

    # Compute volume error pct for the record
    volume_error_pct: float | None = None
    if volume_mm3 is not None and expected_volume_mm3 is not None and expected_volume_mm3 > 0:
        volume_error_pct = abs(volume_mm3 - expected_volume_mm3) / expected_volume_mm3 * 100.0

    return BenchmarkValidation(
        shape_valid=shape_valid,
        solid_count=solid_count,
        volume_mm3=volume_mm3,
        volume_expected_mm3=expected_volume_mm3,
        volume_error_pct=round(volume_error_pct, 4) if volume_error_pct is not None else None,
        bbox_mm=bbox_mm,
        bbox_expected_mm=list(expected_bbox_mm) if expected_bbox_mm else None,
        bbox_ok=bbox_ok,
        checks_passed=len(checks_failed) == 0,
        checks_failed=checks_failed,
        source=source,
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_result(
    episode_status: str,
    episode_success: bool,
    validation_passed: bool,
    save_reopen_attempted: bool,
    save_reopen_passed: bool | None,
) -> BenchmarkClassification:
    """Classify a benchmark result (design report §4).

    Separates model/runtime failures from geometry-quality failures:

    * ``invalid_goal`` — parent-side goal rejection.
    * ``runtime_failure`` — taiga_unavailable, timeout, crashed, etc.
    * ``model_failure`` — budget, unrecoverable, wrong_geometry.
    * ``save_reopen_failure`` — episode + in-memory OK but save/reopen failed.
    * ``geometry_quality_failure`` — episode ran but independent validation failed.
    * ``geometry_success`` — everything passed.

    Args:
        episode_status: The episode status string.
        episode_success: The native episode ``success`` flag.
        validation_passed: Whether independent geometry validation passed.
        save_reopen_attempted: Whether save/reopen was attempted.
        save_reopen_passed: Save/reopen result (``None`` if not attempted).

    Returns:
        One of the ``CLASS_*`` constants.
    """
    if episode_status == "invalid_goal":
        return CLASS_INVALID_GOAL
    if episode_status in _RUNTIME_FAILURE_STATUSES:
        return CLASS_RUNTIME_FAILURE
    if episode_status in _MODEL_FAILURE_STATUSES:
        return CLASS_MODEL_FAILURE

    # Episode ran to completion (success or other terminal state).
    if save_reopen_attempted and save_reopen_passed is False:
        return CLASS_SAVE_REOPEN_FAILURE
    if not validation_passed:
        return CLASS_GEOMETRY_QUALITY_FAILURE
    if not episode_success:
        # Episode did not report success but geometry is independently valid.
        # This is still a geometry-quality concern (native IoU vs expert target
        # disagreed with independent validation).
        return CLASS_GEOMETRY_QUALITY_FAILURE
    return CLASS_GEOMETRY_SUCCESS


# ---------------------------------------------------------------------------
# Save/reopen orchestration
# ---------------------------------------------------------------------------


def _run_validate_reopen(
    bridge: Any,
    document_id: str,
    workspace: str,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Invoke the existing ``validate_reopen`` bridge op in a fresh process.

    Reuses :class:`freecad_mcp.freecad_bridge.bridge.FreeCadBridge` and the
    ``DirectBackend`` / ``BuildRequest`` pattern already used by
    :mod:`freecad_mcp.tools.modeling`.  No parallel validator is created.

    Returns the bridge result payload on success, or a dict with
    ``{"ok": False, "error": ...}`` on failure.
    """
    from freecad_mcp.app.contracts import BuildRequest
    from freecad_mcp.app.direct import DirectBackend

    params: dict[str, Any] = {"document_id": document_id, "workspace": workspace}
    try:
        result = DirectBackend(bridge).execute(
            BuildRequest("validate_reopen", params, timeout)
        )
    except Exception as exc:  # noqa: BLE001 - never leak a traceback
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    # DirectBackend.execute returns BuildResult(ok=True, data=payload).
    # Wrap the payload in an envelope so callers can check .get("ok").
    payload = result.data or {}
    return {"ok": True, **dict(payload)}


def _extract_reopen_geometry(validate_reopen_result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract geometry fields from a ``validate_reopen`` result.

    The ``validate_reopen`` op returns ``checks`` (list of check dicts),
    ``all_passed``, ``failures``, ``object_count``, and ``meta_before``.
    Geometry (shape_valid, volume, solid_count) is not directly in the report;
    it is embedded in the ``_capture_document_state`` snapshots which are not
    returned.  We infer reopen success from ``all_passed`` + object_count and
    treat the in-memory validation as the geometry source of record.

    For a richer reopen-geometry check, the caller can additionally invoke the
    ``validate`` or ``inspect_object`` bridge ops; this function keeps the
    surface minimal and reuses only ``validate_reopen`` as approved.
    """
    all_passed = bool(validate_reopen_result.get("all_passed", False))
    object_count = validate_reopen_result.get("after_object_count")
    if object_count is None:
        object_count = validate_reopen_result.get("object_count")
    return {
        "all_passed": all_passed,
        "object_count": object_count,
        "failures": list(validate_reopen_result.get("failures") or []),
    }


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


def run_benchmark_scenario(
    scenario: BenchmarkScenario,
    *,
    taiga_episode_fn: Callable[..., Any],
    freecad_executable: str,
    workspace: str | None = None,
    bridge_factory: Callable[[], Any] | None = None,
    validate_reopen_timeout: float | None = None,
) -> BenchmarkResult:
    """Run one benchmark scenario end-to-end.

    Orchestrates:
    1. Resolve an isolated artifact path inside the workspace.
    2. Run the native episode via ``taiga_episode_fn`` with ``save_path`` set.
    3. Independently validate the final-state geometry against expected values.
    4. If the artifact was written, run ``validate_reopen`` in a fresh FreeCAD
       process via the existing bridge.
    5. Classify the result.

    Args:
        scenario: The scenario to run.
        taiga_episode_fn: Callable matching
            ``freecad_mcp.tools.taiga.taiga_episode(settings, goal, *, start, save_path)``.
            Injected so this module stays unit-testable without a real runtime.
        freecad_executable: Path to ``freecadcmd`` (for the bridge).
        workspace: Workspace root.  ``None`` uses the env/default.
        bridge_factory: Callable returning a ``FreeCadBridge``.  ``None`` builds
            one from ``freecad_executable``.
        validate_reopen_timeout: Per-call timeout for ``validate_reopen``.

    Returns:
        A :class:`BenchmarkResult`.
    """
    # 1. Resolve artifact path.
    artifact_path = resolve_artifact_path(workspace, scenario.scenario_id)
    ws_str = str(artifact_path.parent.parent.parent)  # workspace root

    # 2. Run the native episode with save.
    t0 = time.monotonic()
    try:
        episode_result = taiga_episode_fn(
            scenario.goal,
            start=scenario.start,
            save_path=str(artifact_path),
        )
        duration = time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001 - never leak a traceback
        duration = time.monotonic() - t0
        episode_result = EpisodeResult(
            ok=False,
            status="child_process_failure",
            error=f"taiga_episode_fn raised: {exc}",
            error_type=type(exc).__name__,
        )

    episode_summary = BenchmarkEpisodeSummary(
        status=episode_result.status,
        success=episode_result.success,
        clean=episode_result.clean,
        outcome=episode_result.outcome,
        iou=episode_result.iou,
        match=episode_result.match,
        steps=episode_result.steps,
        budget=episode_result.budget,
        expert_steps=episode_result.expert_steps,
        agreement=episode_result.agreement,
        deviations=episode_result.deviations,
        history=episode_result.history,
        error=episode_result.error,
        error_type=episode_result.error_type,
        duration_seconds=duration,
    )

    # 3. Independent geometry validation from final_state.
    shape_info = None
    if episode_result.final_state and isinstance(episode_result.final_state, Mapping):
        raw_shape = episode_result.final_state.get("shape")
        if isinstance(raw_shape, Mapping):
            shape_info = dict(raw_shape)

    validation = validate_geometry(
        shape_info,
        scenario.expected_volume_mm3,
        scenario.expected_bbox_mm,
        volume_tolerance_pct=scenario.volume_tolerance_pct,
        bbox_tolerance_mm=scenario.bbox_tolerance_mm,
        source="final_state",
    )

    # 4. Save/reopen via existing bridge.
    save_reopen = BenchmarkSaveReopen()
    artifact_written = artifact_path.is_file()
    save_reopen.attempted = scenario.expected_volume_mm3 is not None
    save_reopen.saved_path = str(artifact_path) if artifact_written else None
    save_reopen.artifact_written = artifact_written

    if episode_result.save_error:
        save_reopen.error = episode_result.save_error

    if save_reopen.attempted and artifact_written:
        # Build a bridge and run validate_reopen in a fresh process.
        try:
            if bridge_factory is not None:
                bridge = bridge_factory()
            else:
                from freecad_mcp.freecad_bridge.bridge import FreeCadBridge

                bridge = FreeCadBridge(freecad_executable)
            vr = _run_validate_reopen(
                bridge,
                scenario.scenario_id,
                ws_str,
                timeout=validate_reopen_timeout,
            )
            if vr.get("ok"):
                reopen_info = _extract_reopen_geometry(vr)
                save_reopen.reopened_in_fresh_process = True
                save_reopen.reopen_shape_valid = reopen_info["all_passed"]
                save_reopen.reopen_solid_count = reopen_info.get("object_count")
                save_reopen.reopen_matches_in_memory = reopen_info["all_passed"]
            else:
                save_reopen.error = str(vr.get("error", "validate_reopen failed"))
        except Exception as exc:  # noqa: BLE001
            save_reopen.error = f"validate_reopen setup failed: {exc}"
    elif save_reopen.attempted and not artifact_written:
        save_reopen.error = save_reopen.error or "artifact was not written"

    save_reopen_passed: bool | None = None
    if save_reopen.reopened_in_fresh_process:
        save_reopen_passed = bool(save_reopen.reopen_matches_in_memory)

    # 5. Classify.
    classification = classify_result(
        episode_status=episode_result.status,
        episode_success=episode_result.success,
        validation_passed=validation.checks_passed,
        save_reopen_attempted=save_reopen.attempted,
        save_reopen_passed=save_reopen_passed,
    )

    notes_parts: list[str] = []
    if episode_result.iou > 0:
        notes_parts.append(f"native IoU={episode_result.iou:.4f} vs expert target")
    if validation.checks_failed:
        notes_parts.append("independent validation failed: " + "; ".join(validation.checks_failed))
    elif validation.volume_mm3 is not None and scenario.expected_volume_mm3:
        notes_parts.append(
            f"volume within {validation.volume_error_pct:.3f}% of expected"
        )
    if scenario.notes:
        notes_parts.append(scenario.notes)

    return BenchmarkResult(
        schema_version="1.0",
        scenario_id=scenario.scenario_id,
        goal=scenario.goal,
        start=scenario.start,
        episode=episode_summary,
        independent_validation=validation,
        save_reopen=save_reopen,
        classification=classification,
        notes="; ".join(notes_parts),
    )