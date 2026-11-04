"""Application adapter for the existing Taiga build runtime."""

from __future__ import annotations

from typing import Any

from .contracts import ApplicationError, BackendStatus, BuildRequest, BuildResult


class TaigaBuildBackend:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def status(self) -> BackendStatus:
        return BackendStatus(available=None, capabilities=("goal_build",), detail="Runtime configured; availability has not been checked.")

    def execute(self, request: BuildRequest) -> BuildResult:
        if request.operation != "build":
            raise ValueError("Taiga backend supports only build requests")
        params = request.parameters
        kwargs = {"document_id": params.get("document_id"), "workspace": params.get("workspace")}
        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        result = self._runtime.run(params["goal"], **kwargs)
        error = None
        if result.error is not None or result.error_type is not None:
            error = ApplicationError(result.error, result.error_type)
        return BuildResult(ok=result.ok, status=result.status, data=result.result, error=error)
