"""Data-only application contracts shared by interface adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ApplicationError:
    message: str | None
    error_type: str | None


@dataclass(frozen=True)
class BuildRequest:
    operation: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    timeout: float | None = None
    document_id: str | None = None


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    status: str
    data: Mapping[str, Any] | None = field(default_factory=dict)
    error: ApplicationError | None = None


@dataclass(frozen=True)
class BackendStatus:
    available: bool | None
    capabilities: tuple[str, ...] = ()
    detail: str = ""


class Backend(Protocol):
    def execute(self, request: BuildRequest) -> BuildResult: ...

    def status(self) -> BackendStatus: ...
