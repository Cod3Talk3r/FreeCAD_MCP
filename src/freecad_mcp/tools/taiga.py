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

from ..config import Settings
from ..integrations.taiga import TaigaBackend

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
