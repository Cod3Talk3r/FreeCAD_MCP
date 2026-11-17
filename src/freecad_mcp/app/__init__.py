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
    "DocumentInspection",
    "InspectionBackend",
    "InspectionRequest",
    "InspectionResult",
    "ObjectInspection",
    "OperationCapability",
    "ShapeInfo",
    "WorkflowResult",
    "WorkflowStepRequest",
    "WorkflowStepResult",
]
