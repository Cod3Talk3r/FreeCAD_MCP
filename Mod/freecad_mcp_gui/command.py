"""Workbench command to show/hide the FreeCAD MCP dockable panel."""

from __future__ import annotations

from typing import Optional

_PANEL: Optional["MCPGuiPanel"] = None


def get_panel() -> Optional["MCPGuiPanel"]:
    """Return the singleton panel instance, or None if not yet created."""
    return _PANEL


def ensure_panel(main_window) -> "MCPGuiPanel":
    """Create (once) and register the dockable panel on the main window.

    Returns the existing panel on subsequent calls.  Prevents duplicate
    instances.
    """
    global _PANEL
    if _PANEL is None:
        from freecad_mcp_gui.panel import MCPGuiPanel
        from PySide6 import QtCore

        _PANEL = MCPGuiPanel(main_window)
        main_window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, _PANEL.dock)
    return _PANEL


class ShowMCPGuiPanelCommand:
    """Toggle the FreeCAD MCP dockable panel visibility."""

    def __init__(self, main_window) -> None:
        self._main_window = main_window

    def Activated(self) -> None:
        panel = ensure_panel(self._main_window)
        if panel.is_visible():
            panel.hide()
        else:
            panel.show()

    def IsActive(self) -> bool:
        return True

    def GetResources(self):
        return {
            "MenuText": "Show FreeCAD MCP Panel",
            "ToolTip": "Show or hide the FreeCAD MCP modeling panel.",
        }
