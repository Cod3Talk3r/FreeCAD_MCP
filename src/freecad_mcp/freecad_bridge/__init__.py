"""Local bridge between the MCP server and a FreeCAD installation.

Public API:

* :class:`FreeCadBridge` - run explicit operations in a FreeCAD child process.
* :func:`find_freecad_executable` - locate the FreeCAD console executable.
* The error hierarchy in :mod:`freecad_bridge.errors`.

Importing this package never imports ``FreeCAD`` itself; only the child process
(:mod:`freecad_bridge.runner`) runs inside FreeCAD's interpreter.
"""

from __future__ import annotations

from .bridge import FreeCadBridge, default_runner_path
from .discovery import find_freecad_executable
from .errors import (
    FreeCadBridgeError,
    FreeCadExecutionError,
    FreeCadNotFoundError,
    FreeCadOperationError,
    FreeCadResponseError,
    FreeCadTimeoutError,
)

__all__ = [
    "FreeCadBridge",
    "FreeCadBridgeError",
    "FreeCadExecutionError",
    "FreeCadNotFoundError",
    "FreeCadOperationError",
    "FreeCadResponseError",
    "FreeCadTimeoutError",
    "default_runner_path",
    "find_freecad_executable",
]
