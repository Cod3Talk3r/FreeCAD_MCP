"""Dockable panel for the FreeCAD MCP Workbench.

All FreeCAD and PySide imports are lazy (inside method bodies) so this module
can be imported and syntax-checked outside a FreeCAD GUI process.
"""

from __future__ import annotations


class MCPGuiPanel:
    """The dockable panel widget for FreeCAD MCP direct modeling."""

    def __init__(self, main_window) -> None:
        from PySide6 import QtWidgets, QtCore
        import FreeCAD as App

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

        layout.addWidget(QtWidgets.QLabel("Object name:"))
        self._name_input = QtWidgets.QLineEdit("Box1")
        layout.addWidget(self._name_input)

        dims_layout = QtWidgets.QGridLayout()
        self._length_input = QtWidgets.QDoubleSpinBox()
        self._width_input = QtWidgets.QDoubleSpinBox()
        self._height_input = QtWidgets.QDoubleSpinBox()
        for spin in (self._length_input, self._width_input, self._height_input):
            spin.setRange(0.001, 100000.0)
            spin.setValue(10.0)
            spin.setDecimals(3)
        dims_layout.addWidget(QtWidgets.QLabel("Length:"), 0, 0)
        dims_layout.addWidget(self._length_input, 0, 1)
        dims_layout.addWidget(QtWidgets.QLabel("Width:"), 1, 0)
        dims_layout.addWidget(self._width_input, 1, 1)
        dims_layout.addWidget(QtWidgets.QLabel("Height:"), 2, 0)
        dims_layout.addWidget(self._height_input, 2, 1)
        layout.addLayout(dims_layout)

        self._create_button = QtWidgets.QPushButton("Create Box")
        self._create_button.clicked.connect(self._on_create_box)
        layout.addWidget(self._create_button)

        self._status_label = QtWidgets.QLabel("")
        self._status_label.setStyleSheet("color: #888;")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addStretch()
        self._dock.setWidget(container)

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

    def _on_create_box(self) -> None:
        import FreeCAD as App
        import Part

        doc = App.ActiveDocument
        if doc is None:
            self._set_status("Error: No active document. Open or create a document first.", error=True)
            return

        name = self._name_input.text().strip()
        if not name:
            self._set_status("Error: Object name must not be empty.", error=True)
            return

        length = self._length_input.value()
        width = self._width_input.value()
        height = self._height_input.value()

        if length <= 0 or width <= 0 or height <= 0:
            self._set_status("Error: Length, width, and height must be positive.", error=True)
            return

        try:
            obj_name = name
            counter = 1
            while doc.getObject(obj_name) is not None:
                obj_name = f"{name}_{counter}"
                counter += 1

            box = doc.addObject("Part::Box", obj_name)
            box.Length = length
            box.Width = width
            box.Height = height
            box.Label = obj_name
            doc.recompute()

            self._set_status(
                f"Created box '{obj_name}' ({length} x {width} x {height}).", error=False
            )
        except Exception as exc:  # noqa: BLE001 - surface FreeCAD errors to user
            self._set_status(f"Error creating box: {exc}", error=True)

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
