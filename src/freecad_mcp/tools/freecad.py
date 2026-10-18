"""FreeCAD connection and version diagnostics.

The tools here report whether a usable FreeCAD installation can be reached, and
its version. They never raise on a missing or broken FreeCAD: they return a
structured *unavailable* result with guidance, so the server stays usable on
machines without FreeCAD.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..config import Settings
from ..freecad_bridge import FreeCadBridge, FreeCadBridgeError, find_freecad_executable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.mcpserver import MCPServer

_INSTALL_HINT = (
    "FreeCAD was not found. Install FreeCAD, add freecadcmd/FreeCADCmd to PATH, "
    "or set FREECAD_MCP_FREECAD_EXECUTABLE to its full path."
)


class FreeCadStatus(BaseModel):
    """Structured result of the ``freecad_status`` tool."""

    available: bool = Field(description="Whether a usable FreeCAD was reached and answered.")
    executable: str | None = Field(default=None, description="Resolved FreeCAD console executable path.")
    version: str | None = Field(default=None, description="FreeCAD version, e.g. '1.1.4'.")
    full_version: list[str] | None = Field(default=None, description="Raw FreeCAD version fields.")
    build_date: str | None = Field(default=None, description="FreeCAD build date, when reported.")
    revision: str | None = Field(default=None, description="Source revision, when reported.")
    python_version: str | None = Field(default=None, description="Python version embedded in FreeCAD.")
    part_available: bool | None = Field(
        default=None, description="Whether FreeCAD's Part workbench module imported successfully."
    )
    error: str | None = Field(default=None, description="Failure detail when FreeCAD could not be reached.")
    hint: str | None = Field(default=None, description="Suggested next step when FreeCAD was not found.")


def collect_freecad_status(settings: Settings) -> FreeCadStatus:
    """Probe FreeCAD and return a status payload. Never raises for bridge failures."""
    executable = find_freecad_executable(settings.freecad_executable)
    executable_str = str(executable) if executable is not None else None

    if executable is None:
        return FreeCadStatus(available=False, executable=None, error=_INSTALL_HINT, hint=_INSTALL_HINT)

    try:
        info = FreeCadBridge(executable, timeout=settings.freecad_timeout_seconds).get_version()
    except FreeCadBridgeError as exc:
        return FreeCadStatus(available=False, executable=executable_str, error=str(exc))

    return FreeCadStatus(
        available=True,
        executable=executable_str,
        version=info.get("version"),
        full_version=info.get("version_raw"),
        build_date=info.get("build_date"),
        revision=info.get("revision"),
        python_version=info.get("python_version"),
        part_available=info.get("part_available"),
    )


def register(server: MCPServer, settings: Settings) -> None:
    """Register the FreeCAD diagnostics tools on ``server``."""

    @server.tool(
        name="freecad_status",
        title="FreeCAD status",
        description=(
            "Report whether a usable FreeCAD installation is reachable and, if so, its version, "
            "embedded Python version, and whether the Part workbench is available. Returns "
            "available=false with guidance when FreeCAD is missing or fails to start. Safe to "
            "call at any time."
        ),
        structured_output=True,
    )
    def freecad_status() -> FreeCadStatus:
        """Return FreeCAD connection and version diagnostics."""
        return collect_freecad_status(settings)
