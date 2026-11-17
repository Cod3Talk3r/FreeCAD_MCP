"""Additive workflow/capability contracts for M19 Method A planning and discovery.

P1 provides typed, serializable contracts and read-only capability discovery only.
No workflow execution, model-state snapshot composition, or recovery behavior.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class OperationCapability(BaseModel):
    """Discovery metadata for one registered MCP tool/capability."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(description="Registered MCP tool name.")
    title: Optional[str] = Field(
        default=None, description="Human-readable tool title when provided."
    )
    description: str = Field(description="Tool description from MCP registration.")
    input_schema: dict[str, Any] = Field(
        description="JSON Schema object describing the tool input payload."
    )
    output_schema: Optional[dict[str, Any]] = Field(
        default=None,
        description="JSON Schema describing structured output when available.",
    )
    operation_name: Optional[str] = Field(
        default=None,
        description=(
            "Underlying allowlisted FreeCAD bridge/runner operation name when "
            "this capability maps to one."
        ),
    )
    kind: Literal["modeling_operation", "diagnostic", "taiga", "workflow"] = Field(
        description="Capability category derived from registration/operation metadata."
    )


class CapabilityCatalog(BaseModel):
    """Deterministic catalog of registered MCP capabilities."""

    model_config = ConfigDict(extra="forbid")

    server_name: str = Field(description="MCP server name.")
    server_version: str = Field(description="freecad-mcp package version.")
    tools: list[OperationCapability] = Field(
        description="Capabilities derived from currently registered MCP tools."
    )
    operation_tools: list[str] = Field(
        description="Tool names that map to allowlisted bridge/runner operations."
    )
    non_operation_tools: list[str] = Field(
        description="Tool names that do not execute a bridge/runner operation."
    )


class WorkflowStepRequest(BaseModel):
    """Typed request for one future Method A workflow step (shape only in P1)."""

    model_config = ConfigDict(extra="forbid")

    operation: str = Field(
        description="Allowlisted operation or registered tool name for the step."
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Operation parameters as a JSON object.",
    )
    label: Optional[str] = Field(
        default=None, description="Optional human-readable step label."
    )
    inspect_after: bool | list[str] = Field(
        default=False,
        description=(
            "Requested post-step inspection behavior for future workflow execution. "
            "Not executed in P1."
        ),
    )
    validate_after: bool = Field(
        default=False,
        description="Request validation after the step in future workflow execution.",
    )


class WorkflowStepResult(BaseModel):
    """Typed result contract for one future Method A workflow step (shape only in P1)."""

    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(description="Zero-based step index in workflow order.")
    operation: str = Field(description="Requested operation/tool name.")
    label: Optional[str] = Field(default=None, description="Optional step label.")
    status: Literal[
        "pending", "running", "completed", "failed", "skipped", "recovered"
    ] = Field(description="Step lifecycle status.")
    ok: bool = Field(description="Whether the step completed successfully.")
    result: Optional[dict[str, Any]] = Field(
        default=None, description="Structured step result payload when available."
    )
    error: Optional[dict[str, Any]] = Field(
        default=None,
        description="Structured error payload (e.g. message/error_type) on failure.",
    )
    artifact_ref: Optional[dict[str, Any]] = Field(
        default=None,
        description="Artifact/revision reference fields for future workflow results.",
    )
    state_ref: Optional[dict[str, Any]] = Field(
        default=None,
        description="State/freshness reference fields for future workflow results.",
    )


class WorkflowResult(BaseModel):
    """Typed aggregate result contract for future Method A workflows (shape only in P1)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="Overall workflow success flag.")
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = Field(
        description="Overall workflow lifecycle status."
    )
    total_steps: int = Field(description="Total number of requested steps.")
    completed_steps: int = Field(description="Number of steps completed successfully.")
    steps: list[WorkflowStepResult] = Field(
        default_factory=list, description="Per-step results in execution order."
    )
    error: Optional[dict[str, Any]] = Field(
        default=None, description="Top-level workflow error when applicable."
    )
    artifact_ref: Optional[dict[str, Any]] = Field(
        default=None, description="Final artifact/revision reference when available."
    )


__all__ = [
    "CapabilityCatalog",
    "OperationCapability",
    "WorkflowResult",
    "WorkflowStepRequest",
    "WorkflowStepResult",
]
