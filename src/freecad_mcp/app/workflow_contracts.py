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


class DocumentArtifactRef(BaseModel):
    """Normalized reference to a workspace document artifact and revision metadata.

    Distinguishes document identity, persisted artifact information, and available
    revision metadata obtained from existing workspace sidecars/files. This does not
    imply that an in-memory model snapshot has been saved unless persisted fields
    explicitly indicate a saved artifact exists.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(description="Validated workspace document identifier.")
    workspace_root: str = Field(
        description="Absolute workspace root used to resolve artifact paths."
    )
    fcstd_path: Optional[str] = Field(
        default=None,
        description="Expected/known .fcstd path for the document, when resolvable.",
    )
    fcstd_exists: bool = Field(
        description="True when the .fcstd artifact exists at fcstd_path."
    )
    meta_path: Optional[str] = Field(
        default=None,
        description="Path to the document metadata sidecar (.meta.json), when resolvable.",
    )
    meta_exists: bool = Field(
        description="True when the metadata sidecar exists at meta_path."
    )
    revision: Optional[int] = Field(
        default=None,
        description="Document revision from persisted metadata sidecar, when available.",
    )
    created_at: Optional[str] = Field(
        default=None, description="Creation timestamp from metadata sidecar, when available."
    )
    last_saved_at: Optional[str] = Field(
        default=None, description="Last save timestamp from metadata sidecar, when available."
    )
    last_modified_at: Optional[str] = Field(
        default=None,
        description="Last modification timestamp from metadata sidecar, when available.",
    )
    latest_snapshot_revision: Optional[int] = Field(
        default=None,
        description="Highest revision found among workspace snapshots for this document.",
    )
    latest_snapshot_path: Optional[str] = Field(
        default=None,
        description="Path to the latest snapshot .fcstd, when snapshots exist.",
    )
    snapshot_count: int = Field(
        default=0,
        description="Number of workspace snapshots found for this document.",
    )


class ModelStateReference(BaseModel):
    """Reference identifying the described model state snapshot."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(description="Validated workspace document identifier.")
    workspace_root: str = Field(
        description="Absolute workspace root associated with the snapshot."
    )
    revision: Optional[int] = Field(
        default=None,
        description=(
            "Live snapshot revision, populated only when the implementation can "
            "establish that the live snapshot corresponds to an exact persisted "
            "revision. None when correspondence is not independently established."
        ),
    )
    freshness: Literal["live_inspection", "unknown"] = Field(
        description=(
            "How the model state was obtained. 'live_inspection' means gathered via "
            "existing bridge operations during this request."
        )
    )
    collected_at: str = Field(
        description="UTC ISO-8601 timestamp when the snapshot was assembled."
    )


class ObjectStateSummary(BaseModel):
    """Bounded, agent-facing summary of one document object."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Stable internal object name.")
    type: str = Field(description="FreeCAD TypeId.")
    label: Optional[str] = Field(
        default=None, description="User-facing label, when available."
    )
    shape_type: Optional[str] = Field(
        default=None, description="Shape type, when reported."
    )
    volume: Optional[float] = Field(
        default=None, description="Volume, when reported."
    )
    valid: Optional[bool] = Field(
        default=None,
        description="Per-object validity from validation results, when available.",
    )


class ModelStateSummary(BaseModel):
    """Concise structured summary of document/model state."""

    model_config = ConfigDict(extra="forbid")

    object_count: int = Field(description="Number of listed document objects.")
    root_hierarchy_count: int = Field(
        description="Number of root nodes in the ownership hierarchy, when available."
    )
    all_valid: Optional[bool] = Field(
        default=None,
        description="Overall validity from validate_document, when available.",
    )
    objects: list[ObjectStateSummary] = Field(
        default_factory=list,
        description="Bounded per-object summaries in deterministic name order.",
    )
    hierarchy_available: bool = Field(
        description="True when ownership hierarchy data was obtained."
    )
    validation_available: bool = Field(
        description="True when validation results were obtained."
    )
    truncated: bool = Field(
        default=False,
        description="True when object summaries were bounded/truncated.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Explicit notes about unavailable, missing, or bounded information.",
    )


class ModelStateSnapshot(BaseModel):
    """Concise snapshot of current document/model state with artifact references."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(description="Validated workspace document identifier.")
    workspace_root: str = Field(
        description="Absolute workspace root associated with the snapshot."
    )
    state_ref: ModelStateReference = Field(
        description="Reference describing this collected model state snapshot."
    )
    artifact_ref: DocumentArtifactRef = Field(
        description="Normalized document/persisted artifact and revision references."
    )
    summary: ModelStateSummary = Field(
        description="Bounded, structured model-state summary."
    )


__all__ = [
    "CapabilityCatalog",
    "DocumentArtifactRef",
    "ModelStateReference",
    "ModelStateSnapshot",
    "ModelStateSummary",
    "ObjectStateSummary",
    "OperationCapability",
    "WorkflowResult",
    "WorkflowStepRequest",
    "WorkflowStepResult",
]
