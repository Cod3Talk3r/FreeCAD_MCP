# SPDX-License-Identifier: LGPL-2.1-or-later

"""Initialization of the FreeCAD MCP GUI workbench interface."""

import os
import sys

import FreeCAD as App
import FreeCADGui as Gui

_HERE = globals().get("__file__")
if _HERE:
    _HERE = os.path.dirname(os.path.abspath(_HERE))
else:
    _HERE = None
    for _entry in sys.path:
        if os.path.isfile(os.path.join(_entry, "freecad_mcp_gui", "InitGui.py")):
            _HERE = os.path.join(_entry, "freecad_mcp_gui")
            break
        if (
            os.path.basename(_entry.rstrip(os.sep)) == "freecad_mcp_gui"
            and os.path.isfile(os.path.join(_entry, "InitGui.py"))
        ):
            _HERE = _entry
            break
if _HERE is None:
    App.Console.PrintError(
        "FreeCAD MCP GUI: could not locate the addon directory "
        "(Mod/freecad_mcp_gui). FreeCAD executed InitGui.py without "
        "defining `__file__`, and no `sys.path` entry contains the "
        "addon. Check the Mod/freecad_mcp_gui junction or install path.\n"
    )
    raise RuntimeError("FreeCAD MCP GUI: addon directory not found")
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)


class FreeCADMCPWorkbench(Gui.Workbench):
    """FreeCAD MCP direct modeling workbench."""

    def __init__(self):
        self.__class__.MenuText = "FreeCAD MCP"
        self.__class__.ToolTip = "FreeCAD MCP direct modeling workbench"

    def Initialize(self):
        try:
            from freecad_mcp_gui.command import ShowMCPGuiPanelCommand, ensure_panel

            main_window = Gui.getMainWindow()
            cmd = ShowMCPGuiPanelCommand(main_window)
            Gui.addCommand("FreeCAD_MCP_ShowPanel", cmd)

            self.appendToolbar("FreeCAD MCP", ["FreeCAD_MCP_ShowPanel"])
            self.appendMenu("FreeCAD MCP", ["FreeCAD_MCP_ShowPanel"])

            # Register the dockable panel (hidden by default; shown via command).
            ensure_panel(main_window)
        except Exception as exc:  # noqa: BLE001 - report init failures to user
            App.Console.PrintError(
                f"FreeCAD MCP GUI: Workbench.Initialize failed: {exc}\n"
            )
            raise

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(FreeCADMCPWorkbench())
