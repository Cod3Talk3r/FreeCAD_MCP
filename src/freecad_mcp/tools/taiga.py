"""Optional Taiga-S1 runtime tools.

These tools never import Taiga or PyTorch in the MCP server process. They use
the isolated runtime configured through ``FREECAD_MCP_TAIGA_PYTHON`` and return
structured results when the runtime or local model is unavailable.

``taiga_score_actions`` only ranks candidate actions. It never executes a
command and never turns scores into a fabricated plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..app.contracts import BuildRequest, BuildResult as ApplicationBuildResult
from ..app.taiga import TaigaBuildBackend
from ..config import Settings
from ..integrations.taiga import TaigaBackend
from ..integrations.taiga.benchmark import (
    BenchmarkResult,
    BenchmarkScenario,
    resolve_artifact_path,
    run_benchmark_scenario,
)
from ..integrations.taiga.build import BuildResult, BuildRuntime
from ..integrations.taiga.child import TaigaChildRunner
from ..integrations.taiga.episode import EpisodeRuntime, EpisodeResult
from ..integrations.taiga.runtime import TaigaRuntime

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.mcpserver import MCPServer


class TaigaStatus(BaseModel):
    """Structured result of the ``taiga_status`` tool."""

    available: bool = Field(description="Whether the isolated Taiga runtime is importable.")
    reason: str | None = Field(
        default=None,
        description="Structured unavailable reason such as 'not_installed' or 'timeout'.",
    )
    detail: str = Field(default="", description="Actionable explanation when unavailable.")
    version: str | None = Field(default=None, description="Installed taiga-s1 distribution version.")
    python: str | None = Field(
        default=None,
        description="Configured Taiga Python interpreter, if any.",
    )
    python_version: str | None = Field(
        default=None,
        description="Python version reported by the isolated runtime.",
    )
    torch_version: str | None = Field(
        default=None,
        description="Torch version reported by the isolated runtime, when available.",
    )
    model: str | None = Field(
        default=None,
        description="Configured local model directory, if any.",
    )
    model_available: bool | None = Field(
        default=None,
        description="Whether the configured local model directory has the required files.",
    )
    model_error: str | None = Field(
        default=None,
        description="Why the configured local model directory is unusable.",
    )


class TaigaActionScore(BaseModel):
    """One scored candidate action."""

    action: str = Field(description="Candidate command id.")
    probability: float = Field(description="Calibrated probability in [0, 1].")


class TaigaScoreResult(BaseModel):
    """Structured result of the ``taiga_score_actions`` tool."""

    backend: str = Field(description="Backend name, always 'taiga-s1'.")
    scored: bool = Field(description="True only when every requested action was scored.")
    scores: list[TaigaActionScore] = Field(
        default_factory=list,
        description="Action/probability pairs, highest probability first.",
    )
    reason: str | None = Field(
        default=None,
        description="Structured unavailable reason when scoring did not run.",
    )
    detail: str = Field(default="", description="Actionable explanation when scoring did not run.")


class TaigaScoreParams(BaseModel):
    """Parameters for scoring Taiga actions."""

    state: dict[str, Any] = Field(
        description=(
            "JSON state accepted by freecad_s1.schema.State. The caller is "
            "responsible for capturing it from FreeCAD."
        ),
    )
    goal: dict[str, Any] = Field(
        description="JSON goal accepted by freecad_s1.schema.Goal.",
    )
    actions: list[str] = Field(
        min_length=1,
        description="Non-empty list of distinct candidate action ids.",
    )


def _make_backend(settings: Settings) -> TaigaBackend:
    return TaigaBackend(
        python=settings.taiga_python,
        model=settings.taiga_model,
        device=settings.taiga_device,
        timeout=settings.taiga_timeout_seconds,
    )


def collect_taiga_status(settings: Settings) -> TaigaStatus:
    """Probe the isolated runtime and return a status payload. Never raises."""
    report = _make_backend(settings).status_report()
    status = report.status
    return TaigaStatus(
        available=status.is_available,
        reason=status.reason.value if status.reason is not None else None,
        detail=status.detail,
        version=status.version,
        python=report.python,
        python_version=report.python_version,
        torch_version=report.torch_version,
        model=report.model,
        model_available=report.model_available,
        model_error=report.model_error,
    )


def score_actions(
    settings: Settings,
    state: dict[str, Any],
    goal: dict[str, Any],
    actions: list[str],
) -> TaigaScoreResult:
    """Score actions through the isolated runtime. Never executes a command."""
    result = _make_backend(settings).score_actions(state, goal, actions)
    return TaigaScoreResult(
        backend=result.backend,
        scored=result.scored,
        scores=[
            TaigaActionScore(action=score.action, probability=score.probability)
            for score in result.scores
        ],
        reason=result.reason.value if result.reason is not None else None,
        detail=result.detail,
    )


class TaigaBuildParams(BaseModel):
    """Parameters for building a Taiga goal."""

    goal: dict[str, Any] = Field(
        description=(
            "Taiga Goal document: an ordered list of feature intents "
            '{"kind": "...", "params": {...}} plus optional "level", "scale", '
            "and \"target\"."
        ),
    )
    document_id: str | None = Field(
        default=None,
        description=(
            "Optional workspace document identifier. If omitted, one is derived "
            "deterministically from the goal."
        ),
    )
    workspace: str | None = Field(
        default=None,
        description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE.",
    )


class TaigaBuildResult(BaseModel):
    """Structured result of the ``taiga_build`` tool."""

    ok: bool = Field(description="True only when the build reached Done and IoU >= 0.99.")
    status: str = Field(
        description=(
            "Build outcome: completed, invalid_goal, feasibility_failure, "
            "execution_failure, timeout, taiga_unavailable, child_process_failure, "
            "invalid_response, geometry_mismatch."
        )
    )
    document_id: str | None = Field(default=None, description="Workspace document identifier.")
    saved_path: str | None = Field(default=None, description="Path to the saved FCStd artifact.")
    artifact_written: bool = Field(default=False, description="Whether the FCStd artifact was written.")
    metadata_written: bool = Field(default=False, description="Whether the metadata side-car was written.")
    iou: float = Field(default=0.0, description="Volumetric IoU of the built solid against the target.")
    match: bool = Field(default=False, description="True when IoU >= 0.99.")
    steps: int = Field(default=0, description="Expert steps taken during execution.")
    budget: int = Field(default=0, description="Step budget (2 * expert plan length + 6).")
    done: bool = Field(default=False, description="Whether the Expert reached Done.")
    feasible: bool = Field(default=False, description="Whether the goal passed feasibility.")
    validation_ok: bool | None = Field(
        default=None, description="Whether in-child post-save validation passed."
    )
    validation_errors: list[str] = Field(
        default_factory=list, description="Post-save validation errors, if any."
    )
    error: str | None = Field(default=None, description="Failure message, if any.")
    error_type: str | None = Field(default=None, description="Structured failure type, if any.")


class TaigaEpisodeParams(BaseModel):
    """Parameters for running one native Taiga episode."""

    goal: dict[str, Any] = Field(
        description=(
            "Taiga Goal document: an ordered list of feature intents "
            '{"kind": "...", "params": {...}} plus optional "level", "scale", '
            'and "target". The native build_target is authoritative; any '
            "caller-supplied target is ignored."
        ),
    )
    start: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional initial session condition: "
            '{"doc_open": true, "workbench": "PartDesignWorkbench", "body": false}. '
            "Defaults to a fresh PartDesign session."
        ),
    )


class TaigaEpisodeResult(BaseModel):
    """Structured result of the ``taiga_episode`` tool."""

    ok: bool = Field(description="True only when the episode reached success (IoU >= 0.99).")
    status: str = Field(
        description=(
            "Episode outcome: success, wrong_geometry, budget, unrecoverable, "
            "crashed, invalid_goal, timeout, taiga_unavailable, "
            "child_process_failure, invalid_response, execution_failure."
        )
    )
    goal: dict[str, Any] | None = Field(default=None, description="Echoed normalized goal.")
    start: dict[str, Any] | None = Field(default=None, description="Echoed start spec.")
    level: int | None = Field(default=None, description="Goal level.")
    success: bool = Field(default=False, description="True when Done and IoU >= 0.99.")
    clean: bool = Field(default=False, description="True when success and no leftover off-plan objects.")
    iou: float = Field(default=0.0, description="Volumetric IoU of the final solid against the target.")
    match: bool = Field(default=False, description="True when IoU >= 0.99.")
    steps: int = Field(default=0, description="Policy steps taken during the episode.")
    budget: int = Field(default=0, description="Native step budget (authoritative).")
    expert_steps: int = Field(default=0, description="Expert plan length (budget//2 - 3).")
    agreement: float = Field(default=0.0, description="Fraction of steps where the policy picked an expert-acceptable action.")
    deviations: int = Field(default=0, description="Policy steps whose action was not expert-acceptable.")
    outcome: str | None = Field(default=None, description="Native EpisodeResult outcome string.")
    features: list[str] | None = Field(default=None, description="Goal feature kinds.")
    rule: str | None = Field(default=None, description="Held-out composition rule, if any.")
    history: list[str] | None = Field(default=None, description="Actions taken (kept for failed episodes).")
    final_state: dict[str, Any] | None = Field(default=None, description="Terminal native State JSON.")
    saved_path: str | None = Field(default=None, description="Path to the saved FCStd artifact, if save was requested.")
    save_error: str | None = Field(default=None, description="Save failure message, if save was requested and failed.")
    error: str | None = Field(default=None, description="Failure message, if any.")
    error_type: str | None = Field(default=None, description="Structured failure type, if any.")


class TaigaBenchmarkParams(BaseModel):
    """Parameters for running one native Taiga benchmark scenario."""

    scenario_id: str = Field(
        description="Unique scenario identifier. Used as the artifact document id stem.",
    )
    goal: dict[str, Any] = Field(
        description=(
            "Taiga Goal document: an ordered list of feature intents "
            '{"kind": "...", "params": {...}} plus optional "level", "scale".'
        ),
    )
    start: dict[str, Any] | None = Field(
        default=None,
        description=(
            'Optional initial session condition: {"doc_open": true, '
            '"workbench": "PartDesignWorkbench", "body": false}.'
        ),
    )
    expected_volume_mm3: float | None = Field(
        default=None,
        description="Analytically expected volume for independent validation. None skips volume check.",
    )
    expected_bbox_mm: list[float] | None = Field(
        default=None,
        description="Analytically expected bounding box [x, y, z] for independent validation. None skips bbox check.",
    )
    volume_tolerance_pct: float = Field(
        default=1.0,
        description="Allowed relative volume error percent for independent validation.",
    )
    bbox_tolerance_mm: float = Field(
        default=0.1,
        description="Allowed absolute per-axis bounding-box error (mm).",
    )
    workspace: str | None = Field(
        default=None,
        description="Optional workspace path. Defaults to FREECAD_MCP_WORKSPACE.",
    )


class TaigaBenchmarkResult(BaseModel):
    """Structured result of the ``taiga_benchmark`` tool."""

    schema_version: str = Field(description="Result schema version.")
    scenario_id: str = Field(description="Scenario identifier.")
    classification: str = Field(
        description=(
            "Overall classification: geometry_success, geometry_quality_failure, "
            "model_failure, runtime_failure, invalid_goal, save_reopen_failure."
        )
    )
    episode: dict[str, Any] = Field(description="Native episode outcome (status, IoU, steps, etc.).")
    independent_validation: dict[str, Any] = Field(
        description="Independent geometry validation (volume, bbox, solid count vs expected)."
    )
    save_reopen: dict[str, Any] = Field(description="Save + fresh-process reopen status.")
    goal: dict[str, Any] = Field(description="Echoed goal.")
    start: dict[str, Any] | None = Field(default=None, description="Echoed start spec.")
    notes: str = Field(default="", description="Human-readable notes.")


def _make_child_runner(settings: Settings) -> TaigaChildRunner | None:
    """Build the isolated FreeCAD child runner, or None when not configured."""
    if not settings.freecad_executable:
        return None
    if not settings.taiga_source:
        return None
    return TaigaChildRunner(
        settings.freecad_executable,
        taiga_source=settings.taiga_source,
        timeout=settings.taiga_build_timeout_seconds,
    )


def taiga_build(
    settings: Settings,
    goal: dict[str, Any],
    *,
    document_id: str | None = None,
    workspace: str | None = None,
) -> TaigaBuildResult:
    """Build a Taiga goal through an isolated FreeCAD child process. Never raises."""
    runner = _make_child_runner(settings)
    if runner is None:
        missing = []
        if not settings.freecad_executable:
            missing.append("FREECAD_MCP_FREECAD_EXECUTABLE")
        if not settings.taiga_source:
            missing.append("FREECAD_MCP_TAIGA_SOURCE")
        return TaigaBuildResult(
            ok=False,
            status="taiga_unavailable",
            error=(
                "Taiga build is not configured: " + ", ".join(missing) + ". "
                "Set them and point FREECAD_MCP_TAIGA_SOURCE at the Taiga-S1 "
                "source repository so freecad_s1 is importable by the child."
            ),
            error_type="TaigaUnavailableError",
        )

    runtime = BuildRuntime(child_runner=runner.run, timeout=settings.taiga_build_timeout_seconds)
    try:
        build_result = TaigaBuildBackend(runtime).execute(
            BuildRequest("build", {"goal": goal, "document_id": document_id, "workspace": workspace})
        )
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to the client
        return TaigaBuildResult(
            ok=False,
            status="child_process_failure",
            error=f"Build runtime failed: {exc}",
            error_type=type(exc).__name__,
        )
    return _to_build_result(build_result, document_id or "")


def _to_build_result(build_result: ApplicationBuildResult, document_id: str) -> TaigaBuildResult:
    result = build_result.data or {}
    return TaigaBuildResult(
        ok=build_result.ok,
        status=build_result.status,
        document_id=result.get("document_id") or document_id or None,
        saved_path=result.get("saved_path"),
        artifact_written=bool(result.get("artifact_written", False)),
        metadata_written=bool(result.get("metadata_written", False)),
        iou=float(result.get("iou", 0.0) or 0.0),
        match=bool(result.get("match", False)),
        steps=int(result.get("steps", 0) or 0),
        budget=int(result.get("budget", 0) or 0),
        done=bool(result.get("done", False)),
        feasible=bool(result.get("feasible", False)),
        validation_ok=result.get("validation_ok"),
        validation_errors=list(result.get("validation_errors") or []),
        error=build_result.error.message if build_result.error else None,
        error_type=build_result.error.error_type if build_result.error else None,
    )


def _make_episode_runtime(settings: Settings) -> tuple[EpisodeRuntime, TaigaRuntime] | None:
    """Build the episode runtime + Taiga child client, or None when unconfigured."""
    if not settings.taiga_python:
        return None
    if not settings.freecad_executable:
        return None
    if not settings.taiga_source:
        return None
    if not settings.taiga_model:
        return None
    runtime = TaigaRuntime(
        settings.taiga_python,
        timeout=settings.taiga_episode_timeout_seconds,
    )
    return (
        EpisodeRuntime(
            child_runner=runtime.run,
            timeout=settings.taiga_episode_timeout_seconds,
        ),
        runtime,
    )


def taiga_episode(
    settings: Settings,
    goal: dict[str, Any],
    *,
    start: dict[str, Any] | None = None,
    save_path: str | None = None,
) -> TaigaEpisodeResult:
    """Run one native Taiga episode through an isolated FreeCAD + Taiga child. Never raises."""
    configured = _make_episode_runtime(settings)
    if configured is None:
        missing = []
        if not settings.freecad_executable:
            missing.append("FREECAD_MCP_FREECAD_EXECUTABLE")
        if not settings.taiga_source:
            missing.append("FREECAD_MCP_TAIGA_SOURCE")
        if not settings.taiga_python:
            missing.append("FREECAD_MCP_TAIGA_PYTHON")
        if not settings.taiga_model:
            missing.append("FREECAD_MCP_TAIGA_MODEL")
        return TaigaEpisodeResult(
            ok=False,
            status="taiga_unavailable",
            error=(
                "Native episode runtime is not configured: " + ", ".join(missing) + ". "
                "Set them and point FREECAD_MCP_TAIGA_MODEL at a local model "
                "directory (config.json + model.safetensors)."
            ),
            error_type="TaigaUnavailableError",
        )

    runtime, taiga_runtime = configured
    assert settings.taiga_model and settings.freecad_executable and settings.taiga_source
    try:
        result = runtime.run(
            goal,
            start,
            freecad_executable=settings.freecad_executable,
            taiga_source=settings.taiga_source,
            model=settings.taiga_model,
            device=settings.taiga_device,
            save_path=save_path,
        )
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to the client
        return TaigaEpisodeResult(
            ok=False,
            status="child_process_failure",
            error=f"Episode runtime failed: {exc}",
            error_type=type(exc).__name__,
        )
    return _to_episode_result(result)


def _make_benchmark_episode_fn(settings: Settings) -> Any:
    """Build a ``taiga_episode``-compatible callable for the benchmark runner."""

    def _fn(
        goal: dict[str, Any],
        *,
        start: dict[str, Any] | None = None,
        save_path: str | None = None,
    ) -> EpisodeResult:
        # Reuse the existing taiga_episode path but return the EpisodeResult
        # (not the Pydantic model) so the benchmark module can access all fields.
        configured = _make_episode_runtime(settings)
        if configured is None:
            return EpisodeResult(
                ok=False,
                status="taiga_unavailable",
                error="Native episode runtime is not configured.",
                error_type="TaigaUnavailableError",
            )
        runtime, _ = configured
        assert settings.taiga_model and settings.freecad_executable and settings.taiga_source
        try:
            return runtime.run(
                goal,
                start,
                freecad_executable=settings.freecad_executable,
                taiga_source=settings.taiga_source,
                model=settings.taiga_model,
                device=settings.taiga_device,
                save_path=save_path,
            )
        except Exception as exc:  # noqa: BLE001 - never leak a traceback
            return EpisodeResult(
                ok=False,
                status="child_process_failure",
                error=f"Episode runtime failed: {exc}",
                error_type=type(exc).__name__,
            )

    return _fn


def taiga_benchmark(
    settings: Settings,
    scenario_id: str,
    goal: dict[str, Any],
    *,
    start: dict[str, Any] | None = None,
    expected_volume_mm3: float | None = None,
    expected_bbox_mm: list[float] | None = None,
    volume_tolerance_pct: float = 1.0,
    bbox_tolerance_mm: float = 0.1,
    workspace: str | None = None,
) -> TaigaBenchmarkResult:
    """Run one native Taiga benchmark scenario with independent validation. Never raises."""
    bbox_tuple: tuple[float, float, float] | None = None
    if expected_bbox_mm is not None and len(expected_bbox_mm) == 3:
        bbox_tuple = (float(expected_bbox_mm[0]), float(expected_bbox_mm[1]), float(expected_bbox_mm[2]))

    scenario = BenchmarkScenario(
        scenario_id=scenario_id,
        goal=goal,
        start=start,
        expected_volume_mm3=expected_volume_mm3,
        expected_bbox_mm=bbox_tuple,
        volume_tolerance_pct=volume_tolerance_pct,
        bbox_tolerance_mm=bbox_tolerance_mm,
    )

    episode_fn = _make_benchmark_episode_fn(settings)
    bridge_factory = None
    if settings.freecad_executable:
        from ..freecad_bridge.bridge import FreeCadBridge

        def _bridge_factory() -> Any:
            return FreeCadBridge(settings.freecad_executable or "")

        bridge_factory = _bridge_factory

    result: BenchmarkResult = run_benchmark_scenario(
        scenario,
        taiga_episode_fn=episode_fn,
        freecad_executable=settings.freecad_executable or "",
        workspace=workspace,
        bridge_factory=bridge_factory,
    )

    return TaigaBenchmarkResult(
        schema_version=result.schema_version,
        scenario_id=result.scenario_id,
        classification=result.classification,
        episode=result.episode.to_dict(),
        independent_validation=result.independent_validation.to_dict(),
        save_reopen=result.save_reopen.to_dict(),
        goal=result.goal,
        start=result.start,
        notes=result.notes,
    )


def _to_episode_result(result: EpisodeResult) -> TaigaEpisodeResult:
    return TaigaEpisodeResult(
        ok=result.ok,
        status=result.status,
        goal=result.goal,
        start=result.start,
        level=result.level,
        success=result.success,
        clean=result.clean,
        iou=result.iou,
        match=result.match,
        steps=result.steps,
        budget=result.budget,
        expert_steps=result.expert_steps,
        agreement=result.agreement,
        deviations=result.deviations,
        outcome=result.outcome,
        features=result.features,
        rule=result.rule,
        history=result.history,
        final_state=result.final_state,
        saved_path=result.saved_path,
        save_error=result.save_error,
        error=result.error,
        error_type=result.error_type,
    )


def register(server: MCPServer, settings: Settings) -> None:
    """Register the optional Taiga-S1 runtime tools on ``server``."""

    @server.tool(
        name="taiga_status",
        title="Taiga-S1 status",
        description=(
            "Report whether the optional Taiga-S1 runtime is available in the "
            "configured isolated Python interpreter, plus the local model "
            "directory state. Never loads model weights or downloads anything. "
            "Returns available=false with an actionable reason when Taiga, its "
            "dependencies, the interpreter, or the model are not configured."
        ),
        structured_output=True,
    )
    def taiga_status_tool() -> TaigaStatus:
        """Return isolated Taiga runtime and local-model status."""
        return collect_taiga_status(settings)

    @server.tool(
        name="taiga_score_actions",
        title="Score Taiga-S1 actions",
        description=(
            "Score a non-empty list of candidate FreeCAD action ids for a "
            "supplied state and goal using the verified Taiga-S1 Policy.score "
            "API in an isolated subprocess. Requires a configured local model "
            "directory; remote model ids are rejected and nothing is "
            "downloaded. Returns calibrated probabilities without executing "
            "any action or producing a multi-step plan."
        ),
        structured_output=True,
    )
    def taiga_score_actions_tool(params: TaigaScoreParams) -> TaigaScoreResult:
        """Rank candidate actions without executing them."""
        return score_actions(settings, params.state, params.goal, params.actions)

    @server.tool(
        name="taiga_build",
        title="Build Taiga-S1 goal",
        description=(
            "Build a Taiga-S1 goal in an isolated FreeCAD child process using "
            "the verified Expert-driven execution pipeline. Runs feasibility, "
            "execution, atomic save, metadata, and in-child post-save "
            "validation. Requires FREECAD_MCP_FREECAD_EXECUTABLE and "
            "FREECAD_MCP_TAIGA_SOURCE. Never imports FreeCAD or Torch in the "
            "server process. PyTorch, Policy, model weights, and scoring are "
            "not used."
        ),
        structured_output=True,
    )
    def taiga_build_tool(params: TaigaBuildParams) -> TaigaBuildResult:
        """Build a Taiga goal and return the structured result."""
        return taiga_build(settings, params.goal, document_id=params.document_id, workspace=params.workspace)

    @server.tool(
        name="taiga_episode",
        title="Run native Taiga-S1 episode",
        description=(
            "Run one native Taiga-S1 closed-loop episode (Policy → Action → "
            "Executor) through the pinned native runtime: Policy, "
            "run_episodes, Worker and HeadlessSession, with native action "
            "enumeration and executors. The native step budget is "
            "authoritative. Runs in an isolated subprocess per request and "
            "tears down the entire owned process tree. Requires "
            "FREECAD_MCP_FREECAD_EXECUTABLE, FREECAD_MCP_TAIGA_SOURCE, "
            "FREECAD_MCP_TAIGA_PYTHON and FREECAD_MCP_TAIGA_MODEL (local "
            "directory). Loads the model offline; nothing is downloaded. Never "
            "imports FreeCAD or Torch in the server process."
        ),
        structured_output=True,
    )
    def taiga_episode_tool(params: TaigaEpisodeParams) -> TaigaEpisodeResult:
        """Run one native episode and return the structured result."""
        return taiga_episode(settings, params.goal, start=params.start)

    @server.tool(
        name="taiga_benchmark",
        title="Run Taiga benchmark scenario",
        description=(
            "Run one native Taiga-S1 closed-loop episode as a benchmark "
            "scenario with independent geometry validation and save/reopen "
            "verification. Saves the artifact to an isolated per-run path "
            "inside the workspace, validates the final-state geometry against "
            "analytically-expected volume/bounding-box values, then reopens the "
            "saved artifact in a fresh FreeCAD process via the existing "
            "validate_reopen mechanism. Returns a structured result with a "
            "classification separating model/runtime failures from "
            "geometry-quality failures. Requires the same Taiga configuration "
            "as taiga_episode. Never imports FreeCAD or Torch in the server "
            "process."
        ),
        structured_output=True,
    )
    def taiga_benchmark_tool(params: TaigaBenchmarkParams) -> TaigaBenchmarkResult:
        """Run one benchmark scenario and return the structured result."""
        return taiga_benchmark(
            settings,
            params.scenario_id,
            params.goal,
            start=params.start,
            expected_volume_mm3=params.expected_volume_mm3,
            expected_bbox_mm=params.expected_bbox_mm,
            volume_tolerance_pct=params.volume_tolerance_pct,
            bbox_tolerance_mm=params.bbox_tolerance_mm,
            workspace=params.workspace,
        )
