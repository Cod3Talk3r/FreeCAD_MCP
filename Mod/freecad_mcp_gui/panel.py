"""Dockable panel for the FreeCAD MCP Workbench.

All FreeCAD and PySide imports are lazy (inside method bodies) so this module
can be imported and syntax-checked outside a FreeCAD GUI process.
"""

from __future__ import annotations

import os
import sys

# The FreeCAD GUI process only has Mod/ on sys.path (set by InitGui.py). Add the
# repository src/ directory so the shared freecad_mcp package (and its bridge
# runner) can be imported from the dockable panel.
_HERE = os.path.dirname(os.path.realpath(__file__))
_PARENT = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_PARENT)
_SRC = os.path.join(_ROOT, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_SUPPORTED_PROPERTIES = ("Length", "Width", "Height", "Radius")


class MCPGuiPanel:
    """The dockable panel widget for FreeCAD MCP direct modeling."""

    def __init__(self, main_window) -> None:
        from PySide6 import QtWidgets, QtCore

        self._main_window = main_window
        self._dock = QtWidgets.QDockWidget("FreeCAD MCP", main_window)
        self._dock.setObjectName("FreeCADMCPDock")
        self._dock.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)

        title = QtWidgets.QLabel("FreeCAD MCP — Direct Modeling")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        self._doc_label = QtWidgets.QLabel("No active document")
        self._doc_label.setStyleSheet("color: #666;")
        layout.addWidget(self._doc_label)

        layout.addWidget(QtWidgets.QLabel("Document ID:"))
        self._doc_id_input = QtWidgets.QLineEdit("Box1")
        layout.addWidget(self._doc_id_input)

        note = QtWidgets.QLabel(
            "Operates on the FreeCAD MCP workspace document with this ID. "
            "The document is created or opened and may become FreeCAD's "
            "active document."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(note)

        # ---- Create Box -------------------------------------------------
        box_group = QtWidgets.QGroupBox("Create Box")
        box_layout = QtWidgets.QGridLayout(box_group)
        self._length_input = QtWidgets.QDoubleSpinBox()
        self._width_input = QtWidgets.QDoubleSpinBox()
        self._height_input = QtWidgets.QDoubleSpinBox()
        self._length_input.setRange(0.001, 100000.0)
        self._width_input.setRange(0.001, 100000.0)
        self._height_input.setRange(0.001, 100000.0)
        self._length_input.setValue(10.0)
        self._width_input.setValue(5.0)
        self._height_input.setValue(3.0)
        for spin in (self._length_input, self._width_input, self._height_input):
            spin.setDecimals(3)
        self._box_x_input = QtWidgets.QDoubleSpinBox()
        self._box_y_input = QtWidgets.QDoubleSpinBox()
        self._box_z_input = QtWidgets.QDoubleSpinBox()
        for spin in (self._box_x_input, self._box_y_input, self._box_z_input):
            spin.setRange(-100000.0, 100000.0)
            spin.setValue(0.0)
            spin.setDecimals(3)
        box_layout.addWidget(QtWidgets.QLabel("Length:"), 0, 0)
        box_layout.addWidget(self._length_input, 0, 1)
        box_layout.addWidget(QtWidgets.QLabel("Width:"), 1, 0)
        box_layout.addWidget(self._width_input, 1, 1)
        box_layout.addWidget(QtWidgets.QLabel("Height:"), 2, 0)
        box_layout.addWidget(self._height_input, 2, 1)
        box_layout.addWidget(QtWidgets.QLabel("X:"), 3, 0)
        box_layout.addWidget(self._box_x_input, 3, 1)
        box_layout.addWidget(QtWidgets.QLabel("Y:"), 4, 0)
        box_layout.addWidget(self._box_y_input, 4, 1)
        box_layout.addWidget(QtWidgets.QLabel("Z:"), 5, 0)
        box_layout.addWidget(self._box_z_input, 5, 1)
        self._create_box_button = QtWidgets.QPushButton("Create Box")
        self._create_box_button.clicked.connect(self._on_create_box)
        box_layout.addWidget(self._create_box_button, 6, 0, 1, 2)
        layout.addWidget(box_group)

        # ---- Create Cylinder --------------------------------------------
        cyl_group = QtWidgets.QGroupBox("Create Cylinder")
        cyl_layout = QtWidgets.QGridLayout(cyl_group)
        self._radius_input = QtWidgets.QDoubleSpinBox()
        self._cyl_height_input = QtWidgets.QDoubleSpinBox()
        self._radius_input.setRange(0.001, 100000.0)
        self._cyl_height_input.setRange(0.001, 100000.0)
        self._radius_input.setValue(2.0)
        self._cyl_height_input.setValue(7.0)
        self._radius_input.setDecimals(3)
        self._cyl_height_input.setDecimals(3)
        self._cyl_x_input = QtWidgets.QDoubleSpinBox()
        self._cyl_y_input = QtWidgets.QDoubleSpinBox()
        self._cyl_z_input = QtWidgets.QDoubleSpinBox()
        for spin in (self._cyl_x_input, self._cyl_y_input, self._cyl_z_input):
            spin.setRange(-100000.0, 100000.0)
            spin.setValue(0.0)
            spin.setDecimals(3)
        cyl_layout.addWidget(QtWidgets.QLabel("Radius:"), 0, 0)
        cyl_layout.addWidget(self._radius_input, 0, 1)
        cyl_layout.addWidget(QtWidgets.QLabel("Height:"), 1, 0)
        cyl_layout.addWidget(self._cyl_height_input, 1, 1)
        cyl_layout.addWidget(QtWidgets.QLabel("X:"), 2, 0)
        cyl_layout.addWidget(self._cyl_x_input, 2, 1)
        cyl_layout.addWidget(QtWidgets.QLabel("Y:"), 3, 0)
        cyl_layout.addWidget(self._cyl_y_input, 3, 1)
        cyl_layout.addWidget(QtWidgets.QLabel("Z:"), 4, 0)
        cyl_layout.addWidget(self._cyl_z_input, 4, 1)
        self._create_cyl_button = QtWidgets.QPushButton("Create Cylinder")
        self._create_cyl_button.clicked.connect(self._on_create_cylinder)
        cyl_layout.addWidget(self._create_cyl_button, 5, 0, 1, 2)
        layout.addWidget(cyl_group)

        # ---- List objects -----------------------------------------------
        list_group = QtWidgets.QGroupBox("Objects in Workspace Document")
        list_layout = QtWidgets.QVBoxLayout(list_group)
        self._refresh_button = QtWidgets.QPushButton("Refresh Objects")
        self._refresh_button.clicked.connect(self._on_list_objects)
        list_layout.addWidget(self._refresh_button)
        self._objects_list = QtWidgets.QListWidget()
        self._objects_list.setMaximumHeight(120)
        list_layout.addWidget(self._objects_list)
        layout.addWidget(list_group)

        # ---- Edit parameters --------------------------------------------
        edit_group = QtWidgets.QGroupBox("Edit Object Parameter")
        edit_layout = QtWidgets.QGridLayout(edit_group)
        self._edit_name_input = QtWidgets.QLineEdit()
        edit_layout.addWidget(QtWidgets.QLabel("Object name:"), 0, 0)
        edit_layout.addWidget(self._edit_name_input, 0, 1)
        self._property_combo = QtWidgets.QComboBox()
        self._property_combo.addItems(list(_SUPPORTED_PROPERTIES))
        edit_layout.addWidget(QtWidgets.QLabel("Property:"), 1, 0)
        edit_layout.addWidget(self._property_combo, 1, 1)
        self._value_input = QtWidgets.QDoubleSpinBox()
        self._value_input.setRange(0.001, 100000.0)
        self._value_input.setValue(10.0)
        self._value_input.setDecimals(3)
        edit_layout.addWidget(QtWidgets.QLabel("Value:"), 2, 0)
        edit_layout.addWidget(self._value_input, 2, 1)
        self._set_param_button = QtWidgets.QPushButton("Set Parameter")
        self._set_param_button.clicked.connect(self._on_set_parameter)
        edit_layout.addWidget(self._set_param_button, 3, 0, 1, 2)
        layout.addWidget(edit_group)

        # ---- Edit placement ---------------------------------------------
        placement_group = QtWidgets.QGroupBox("Edit Placement")
        placement_layout = QtWidgets.QGridLayout(placement_group)
        self._placement_name_input = QtWidgets.QLineEdit()
        placement_layout.addWidget(QtWidgets.QLabel("Object name:"), 0, 0)
        placement_layout.addWidget(self._placement_name_input, 0, 1)
        self._placement_x_input = QtWidgets.QDoubleSpinBox()
        self._placement_y_input = QtWidgets.QDoubleSpinBox()
        self._placement_z_input = QtWidgets.QDoubleSpinBox()
        for spin in (self._placement_x_input, self._placement_y_input, self._placement_z_input):
            spin.setRange(-100000.0, 100000.0)
            spin.setValue(0.0)
            spin.setDecimals(3)
        placement_layout.addWidget(QtWidgets.QLabel("X:"), 1, 0)
        placement_layout.addWidget(self._placement_x_input, 1, 1)
        placement_layout.addWidget(QtWidgets.QLabel("Y:"), 2, 0)
        placement_layout.addWidget(self._placement_y_input, 2, 1)
        placement_layout.addWidget(QtWidgets.QLabel("Z:"), 3, 0)
        placement_layout.addWidget(self._placement_z_input, 3, 1)
        self._set_placement_button = QtWidgets.QPushButton("Set Placement")
        self._set_placement_button.clicked.connect(self._on_set_placement)
        placement_layout.addWidget(self._set_placement_button, 4, 0, 1, 2)
        layout.addWidget(placement_group)

        # ---- Boolean Cut ------------------------------------------------
        cut_group = QtWidgets.QGroupBox("Boolean Cut")
        cut_layout = QtWidgets.QGridLayout(cut_group)
        self._cut_base_input = QtWidgets.QLineEdit()
        cut_layout.addWidget(QtWidgets.QLabel("Base Object:"), 0, 0)
        cut_layout.addWidget(self._cut_base_input, 0, 1)
        self._cut_tool_input = QtWidgets.QLineEdit()
        cut_layout.addWidget(QtWidgets.QLabel("Tool Object:"), 1, 0)
        cut_layout.addWidget(self._cut_tool_input, 1, 1)
        self._cut_result_input = QtWidgets.QLineEdit("Cut1")
        cut_layout.addWidget(QtWidgets.QLabel("Result Name:"), 2, 0)
        cut_layout.addWidget(self._cut_result_input, 2, 1)
        self._cut_button = QtWidgets.QPushButton("Boolean Cut")
        self._cut_button.clicked.connect(self._on_boolean_cut)
        cut_layout.addWidget(self._cut_button, 3, 0, 1, 2)
        self._fuse_button = QtWidgets.QPushButton("Boolean Fuse")
        self._fuse_button.clicked.connect(self._on_boolean_fuse)
        cut_layout.addWidget(self._fuse_button, 4, 0, 1, 2)
        self._common_button = QtWidgets.QPushButton("Boolean Common")
        self._common_button.clicked.connect(self._on_boolean_common)
        cut_layout.addWidget(self._common_button, 5, 0, 1, 2)
        layout.addWidget(cut_group)

        # ---- Recompute / Validate ---------------------------------------
        actions_layout = QtWidgets.QHBoxLayout()
        self._recompute_button = QtWidgets.QPushButton("Recompute Document")
        self._recompute_button.clicked.connect(self._on_recompute)
        self._validate_button = QtWidgets.QPushButton("Validate Document")
        self._validate_button.clicked.connect(self._on_validate)
        actions_layout.addWidget(self._recompute_button)
        actions_layout.addWidget(self._validate_button)
        layout.addLayout(actions_layout)

        self._validate_result = QtWidgets.QLabel("")
        self._validate_result.setWordWrap(True)
        self._validate_result.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._validate_result)

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setStyleSheet("color: #888;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setWidget(container)
        self._dock.setWidget(scroll)

        self.refresh_document_label()

    @property
    def dock(self):
        return self._dock

    def refresh_document_label(self) -> None:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            self._doc_label.setText("No active document")
            self._doc_label.setStyleSheet("color: #666;")
        else:
            self._doc_label.setText(f"Active document: {doc.Name}")
            self._doc_label.setStyleSheet("color: #000;")

    # ------------------------------------------------------------------
    # Shared handler call
    # ------------------------------------------------------------------
    def _run_operation(self, op: str, params: dict):
        """Call handle_request and return the response dict, or None on failure."""
        from freecad_mcp.freecad_bridge.runner import handle_request

        try:
            return handle_request({"op": op, "params": params})
        except Exception as exc:  # noqa: BLE001 - surface bridge errors to user
            self._set_status(f"Error: {exc}", error=True)
            return None

    @staticmethod
    def _error_message(response) -> str:
        error = response.get("error", {})
        if isinstance(error, dict):
            return str(error.get("message", "Workspace operation failed"))
        return str(error or "Workspace operation failed")

    # ------------------------------------------------------------------
    # Create Box
    # ------------------------------------------------------------------
    def _on_create_box(self) -> None:
        name = self._doc_id_input.text().strip()
        if not name:
            self._set_status(
                "Error: Document ID / object name must not be empty.", error=True
            )
            return

        length = self._length_input.value()
        width = self._width_input.value()
        height = self._height_input.value()
        x = self._box_x_input.value()
        y = self._box_y_input.value()
        z = self._box_z_input.value()
        if length <= 0 or width <= 0 or height <= 0:
            self._set_status(
                "Error: Length, width, and height must be positive.", error=True
            )
            return

        response = self._run_operation(
            "create_box",
            {
                "document_id": name,
                "object_name": name,
                "length": length,
                "width": width,
                "height": height,
                "x": x,
                "y": y,
                "z": z,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        obj_name = (
            result.get("object_name", name) if isinstance(result, dict) else name
        )
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Box '{obj_name}' in workspace document '{name}'.", error=False
        )

    # ------------------------------------------------------------------
    # Create Cylinder
    # ------------------------------------------------------------------
    def _on_create_cylinder(self) -> None:
        name = self._doc_id_input.text().strip()
        if not name:
            self._set_status(
                "Error: Document ID / object name must not be empty.", error=True
            )
            return

        radius = self._radius_input.value()
        height = self._cyl_height_input.value()
        x = self._cyl_x_input.value()
        y = self._cyl_y_input.value()
        z = self._cyl_z_input.value()
        if radius <= 0 or height <= 0:
            self._set_status(
                "Error: Radius and height must be positive.", error=True
            )
            return

        response = self._run_operation(
            "create_cylinder",
            {
                "document_id": name,
                "object_name": name,
                "radius": radius,
                "height": height,
                "x": x,
                "y": y,
                "z": z,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        obj_name = (
            result.get("object_name", name) if isinstance(result, dict) else name
        )
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Cylinder '{obj_name}' in workspace document '{name}'.", error=False
        )

    # ------------------------------------------------------------------
    # List objects
    # ------------------------------------------------------------------
    def _on_list_objects(self) -> None:
        name = self._doc_id_input.text().strip()
        if not name:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return

        response = self._run_operation("list_objects", {"document_id": name})
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        objects = result.get("objects", []) if isinstance(result, dict) else []
        self._objects_list.clear()
        count = 0
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            count += 1
            label = f"{obj.get('name', '')} ({obj.get('type', '')})"
            volume = obj.get("volume")
            if volume is not None:
                label += f", vol {volume:g}"
            self._objects_list.addItem(label)
        self.refresh_document_label()
        self._set_status(
            f"Listed {count} object(s) in workspace document '{name}'.", error=False
        )

    # ------------------------------------------------------------------
    # Set parameter
    # ------------------------------------------------------------------
    def _on_set_parameter(self) -> None:
        document_id = self._doc_id_input.text().strip()
        object_name = self._edit_name_input.text().strip()
        if not object_name:
            self._set_status("Error: Object name must not be empty.", error=True)
            return
        if not document_id:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return

        property_name = self._property_combo.currentText()
        value = self._value_input.value()
        if value <= 0:
            self._set_status("Error: Value must be positive.", error=True)
            return

        response = self._run_operation(
            "set_parameter",
            {
                "document_id": document_id,
                "object_name": object_name,
                "property": property_name,
                "value": value,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Set {property_name}={value} on object '{object_name}' "
            f"in workspace document '{document_id}'.",
            error=False,
        )

    # ------------------------------------------------------------------
    # Set placement
    # ------------------------------------------------------------------
    def _on_set_placement(self) -> None:
        document_id = self._doc_id_input.text().strip()
        object_name = self._placement_name_input.text().strip()
        if not object_name:
            self._set_status("Error: Object name must not be empty.", error=True)
            return
        if not document_id:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return

        x = self._placement_x_input.value()
        y = self._placement_y_input.value()
        z = self._placement_z_input.value()

        response = self._run_operation(
            "set_placement",
            {
                "document_id": document_id,
                "object_name": object_name,
                "x": x,
                "y": y,
                "z": z,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Set placement x={x}, y={y}, z={z} on object '{object_name}' "
            f"in workspace document '{document_id}'.",
            error=False,
        )

    # ------------------------------------------------------------------
    # Recompute
    # ------------------------------------------------------------------
    def _on_recompute(self) -> None:
        name = self._doc_id_input.text().strip()
        if not name:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return

        response = self._run_operation("recompute", {"document_id": name})
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        self.refresh_document_label()
        self._set_status(f"Recomputed workspace document '{name}'.", error=False)

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------
    def _on_validate(self) -> None:
        name = self._doc_id_input.text().strip()
        if not name:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return

        response = self._run_operation("validate", {"document_id": name})
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        all_valid = result.get("all_valid") if isinstance(result, dict) else None
        objects = result.get("objects", []) if isinstance(result, dict) else []
        lines = [f"Valid: {all_valid}"]
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            label = obj.get("name", "?")
            valid = obj.get("valid", False)
            errors = obj.get("errors", [])
            lines.append(f"  {label}: {'OK' if valid else 'INVALID'}")
            for err in errors:
                lines.append(f"    - {err}")
        self._validate_result.setText("\n".join(lines))
        self.refresh_document_label()
        self._set_status(f"Validated workspace document '{name}'.", error=False)

    # ------------------------------------------------------------------
    # Boolean Cut
    # ------------------------------------------------------------------
    def _on_boolean_cut(self) -> None:
        document_id = self._doc_id_input.text().strip()
        base_object = self._cut_base_input.text().strip()
        tool_object = self._cut_tool_input.text().strip()
        result_name = self._cut_result_input.text().strip() or "Cut1"
        if not document_id:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return
        if not base_object:
            self._set_status("Error: Base Object must not be empty.", error=True)
            return
        if not tool_object:
            self._set_status("Error: Tool Object must not be empty.", error=True)
            return
        if base_object == tool_object:
            self._set_status(
                "Error: Base Object and Tool Object must be different.", error=True
            )
            return

        response = self._run_operation(
            "boolean_cut",
            {
                "document_id": document_id,
                "base_object": base_object,
                "tool_object": tool_object,
                "result_name": result_name,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        obj_name = (
            result.get("object_name", result_name)
            if isinstance(result, dict)
            else result_name
        )
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Boolean Cut '{obj_name}' = '{base_object}' - '{tool_object}' "
            f"in workspace document '{document_id}'.",
            error=False,
        )

    def _on_boolean_fuse(self) -> None:
        document_id = self._doc_id_input.text().strip()
        base_object = self._cut_base_input.text().strip()
        tool_object = self._cut_tool_input.text().strip()
        result_name = self._cut_result_input.text().strip() or "Fuse1"
        if not document_id:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return
        if not base_object:
            self._set_status("Error: Base Object must not be empty.", error=True)
            return
        if not tool_object:
            self._set_status("Error: Tool Object must not be empty.", error=True)
            return
        if base_object == tool_object:
            self._set_status(
                "Error: Base Object and Tool Object must be different.", error=True
            )
            return

        response = self._run_operation(
            "boolean_fuse",
            {
                "document_id": document_id,
                "base_object": base_object,
                "tool_object": tool_object,
                "result_name": result_name,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        obj_name = (
            result.get("object_name", result_name)
            if isinstance(result, dict)
            else result_name
        )
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Boolean Fuse '{obj_name}' = '{base_object}' + '{tool_object}' "
            f"in workspace document '{document_id}'.",
            error=False,
        )

    def _on_boolean_common(self) -> None:
        document_id = self._doc_id_input.text().strip()
        base_object = self._cut_base_input.text().strip()
        tool_object = self._cut_tool_input.text().strip()
        result_name = self._cut_result_input.text().strip() or "Common1"
        if not document_id:
            self._set_status("Error: Document ID must not be empty.", error=True)
            return
        if not base_object:
            self._set_status("Error: Base Object must not be empty.", error=True)
            return
        if not tool_object:
            self._set_status("Error: Tool Object must not be empty.", error=True)
            return
        if base_object == tool_object:
            self._set_status(
                "Error: Base Object and Tool Object must be different.", error=True
            )
            return

        response = self._run_operation(
            "boolean_common",
            {
                "document_id": document_id,
                "base_object": base_object,
                "tool_object": tool_object,
                "result_name": result_name,
            },
        )
        if response is None or not response.get("ok"):
            if response is not None:
                self._set_status(f"Error: {self._error_message(response)}", error=True)
            return
        result = response.get("result", {}) if isinstance(response, dict) else {}
        obj_name = (
            result.get("object_name", result_name)
            if isinstance(result, dict)
            else result_name
        )
        self.refresh_document_label()
        self._objects_list.clear()
        self._set_status(
            f"Boolean Common '{obj_name}' = '{base_object}' ∩ '{tool_object}' "
            f"in workspace document '{document_id}'.",
            error=False,
        )

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self._status_label.setText(message)
        self._status_label.setStyleSheet(f"color: {'#b00' if error else '#080'};")

    def show(self) -> None:
        self.refresh_document_label()
        self._dock.show()
        self._dock.raise_()

    def hide(self) -> None:
        self._dock.hide()

    def is_visible(self) -> bool:
        return self._dock.isVisible()
