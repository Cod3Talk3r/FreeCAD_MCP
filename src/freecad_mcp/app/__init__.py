"""Application contracts and adapters, independent of MCP and FreeCAD."""

from .contracts import ApplicationError, Backend, BackendStatus, BuildRequest, BuildResult
from .inspection import (
    DocumentInspection,
    InspectionBackend,
    InspectionRequest,
    InspectionResult,
    ObjectInspection,
    ShapeInfo,
)
from .workflow_contracts import (
    CapabilityCatalog,
    DocumentArtifactRef,
    ModelStateReference,
    ModelStateSnapshot,
    ModelStateSummary,
    ObjectStateSummary,
    OperationCapability,
    WorkflowResult,
    WorkflowStepRequest,
    WorkflowStepResult,
)

__all__ = [
    "ApplicationError",
    "Backend",
    "BackendStatus",
    "BuildRequest",
    "BuildResult",
    "CapabilityCatalog",
    "DocumentArtifactRef",
    "DocumentInspection",
    "InspectionBackend",
    "InspectionRequest",
    "InspectionResult",
    "ModelStateReference",
    "ModelStateSnapshot",
    "ModelStateSummary",
    "ObjectInspection",
    "ObjectStateSummary",
    "OperationCapability",
    "ShapeInfo",
    "WorkflowResult",
    "WorkflowStepRequest",
    "WorkflowStepResult",
]
