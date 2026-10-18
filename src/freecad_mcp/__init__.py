"""freecad-mcp: drive FreeCAD parametric modeling from an AI agent over MCP.

This package provides an MCP server that exposes explicit, validated modeling
operations (create documents, build parametric solids, inspect and validate
geometry, export STEP) backed by a bridge to a real FreeCAD instance.

The package must remain importable without FreeCAD installed; all FreeCAD
interaction is isolated behind the ``freecad_bridge`` subpackage.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
