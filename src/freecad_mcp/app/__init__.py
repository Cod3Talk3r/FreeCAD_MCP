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

__all__ = [
    "ApplicationError",
    "Backend",
    "BackendStatus",
    "BuildRequest",
    "BuildResult",
    "DocumentInspection",
    "InspectionBackend",
    "InspectionRequest",
    "InspectionResult",
    "ObjectInspection",
    "ShapeInfo",
]
