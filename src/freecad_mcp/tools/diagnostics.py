"""Harmless diagnostic tools.

These tools never contact FreeCAD. They exist so a client can confirm the server
is reachable and inspect its build/runtime environment before any CAD operation
is attempted.
"""

from __future__ import annotations

import importlib.metadata
import platform
import sys
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .. import __version__
from ..config import Settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.mcpserver import MCPServer

#: Reported until the FreeCAD bridge exists (Milestone 2).
BRIDGE_STATUS_NOT_IMPLEMENTED = "not_implemented"

_SDK_PACKAGE = "mcp"


def _sdk_version() -> str:
    """Return the installed ``mcp`` SDK version, or ``"unknown"`` if unavailable."""
    try:
        return importlib.metadata.version(_SDK_PACKAGE)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - SDK is a hard dependency
        return "unknown"


class EnvironmentInfo(BaseModel):
    """Host Python environment, returned only when explicitly requested."""

    python_version: str = Field(description="Host Python version, e.g. '3.14.7'.")
    python_implementation: str = Field(description="Python implementation, e.g. 'CPython'.")
    platform: str = Field(description="Operating system and architecture.")
    executable: str = Field(description="Absolute path of the running Python interpreter.")


class ServerInfo(BaseModel):
    """Structured result of the ``server_info`` tool."""

    name: str = Field(description="Server name advertised over MCP.")
    version: str = Field(description="freecad-mcp package version.")
    mcp_sdk_version: str = Field(description="Installed Model Context Protocol Python SDK version.")
    freecad_bridge_status: str = Field(
        description="State of the FreeCAD bridge; 'not_implemented' until Milestone 2."
    )
    environment: EnvironmentInfo | None = Field(
        default=None,
        description="Host Python environment details, present only when include_environment is true.",
    )


def _collect_server_info(settings: Settings, include_environment: bool) -> ServerInfo:
    """Build the :class:`ServerInfo` payload. Pure and FreeCAD-free."""
    environment: EnvironmentInfo | None = None
    if include_environment:
        environment = EnvironmentInfo(
            python_version=platform.python_version(),
            python_implementation=platform.python_implementation(),
            platform=platform.platform(),
            executable=sys.executable,
        )

    return ServerInfo(
        name=settings.server_name,
        version=__version__,
        mcp_sdk_version=_sdk_version(),
        freecad_bridge_status=BRIDGE_STATUS_NOT_IMPLEMENTED,
        environment=environment,
    )


def register(server: MCPServer, settings: Settings) -> None:
    """Register the diagnostic tools on ``server``."""

    @server.tool(
        name="server_info",
        title="Server information",
        description=(
            "Return diagnostic information about this freecad-mcp server: its name, "
            "version, the installed MCP SDK version, and the FreeCAD bridge status. "
            "Set include_environment=true to also report the host Python environment. "
            "This tool does not contact FreeCAD and is safe to call at any time."
        ),
        structured_output=True,
    )
    def server_info(include_environment: bool = False) -> ServerInfo:
        """Return server and (optionally) environment diagnostics."""
        return _collect_server_info(settings, include_environment)
