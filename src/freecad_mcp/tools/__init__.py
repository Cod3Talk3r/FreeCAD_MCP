"""MCP tool definitions.

Each module in this package registers a group of related tools on the server.
Tools validate their inputs before any work is handed to the FreeCAD bridge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import diagnostics, freecad

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server.mcpserver import MCPServer

    from ..config import Settings

__all__ = ["register_all"]


def register_all(server: MCPServer, settings: Settings) -> None:
    """Register every tool group on ``server``."""
    diagnostics.register(server, settings)
    freecad.register(server, settings)
