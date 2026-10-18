"""Error types raised by the FreeCAD bridge.

All bridge failures derive from :class:`FreeCadBridgeError` so the MCP layer can
translate any of them into a structured, user-facing result instead of leaking a
traceback.
"""

from __future__ import annotations


class FreeCadBridgeError(RuntimeError):
    """Base class for every FreeCAD bridge failure."""


class FreeCadNotFoundError(FreeCadBridgeError):
    """No usable FreeCAD console executable could be located."""


class FreeCadTimeoutError(FreeCadBridgeError):
    """FreeCAD did not finish an operation within the allotted time."""

    def __init__(self, message: str, *, timeout: float) -> None:
        super().__init__(message)
        self.timeout = timeout


class FreeCadExecutionError(FreeCadBridgeError):
    """FreeCAD ran but exited abnormally or produced no usable response.

    Carries the captured streams so callers can surface the real cause.
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FreeCadResponseError(FreeCadBridgeError):
    """FreeCAD responded, but the response envelope was missing or malformed."""


class FreeCadOperationError(FreeCadBridgeError):
    """FreeCAD reported a structured failure for the requested operation."""

    def __init__(self, message: str, *, error_type: str = "Error") -> None:
        super().__init__(message)
        self.error_type = error_type
