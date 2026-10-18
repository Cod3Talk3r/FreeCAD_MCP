"""MCP server initialization and entry point.

This module builds the MCP server and registers tools. It never imports FreeCAD,
so the server can start on a machine where FreeCAD is not installed; FreeCAD
communication is isolated behind ``freecad_bridge`` (Milestone 2+).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Settings, load_settings
from .tools import register_all

INSTRUCTIONS = """\
freecad-mcp is an MCP server that drives parametric 3D modeling in FreeCAD.

Use the diagnostic tools to confirm the server is reachable. Modeling tools
(create documents, build parametric solids, inspect and validate geometry,
export STEP) are added in later milestones and always take explicit, validated
parameters; this server does not execute arbitrary generated Python inside
FreeCAD.
"""


def create_server(settings: Settings | None = None) -> MCPServer:
    """Create and configure the MCP server.

    Args:
        settings: Settings to use. Defaults to :func:`load_settings` (environment
            based). Passing settings explicitly keeps construction deterministic
            and easy to test.

    Returns:
        A configured :class:`~mcp.server.mcpserver.MCPServer` with tools registered.
    """
    resolved = settings if settings is not None else load_settings()

    server = MCPServer(
        name=resolved.server_name,
        version=__version__,
        instructions=INSTRUCTIONS,
        log_level=resolved.log_level,
    )
    register_all(server, resolved)
    return server


def main() -> None:
    """Run the MCP server over the stdio transport."""
    create_server().run("stdio")


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    main()
