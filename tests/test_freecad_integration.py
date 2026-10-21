"""Tests that require a real FreeCAD installation.

Skipped automatically when FreeCAD cannot be discovered, so a machine without
FreeCAD never sees a false failure. Set FREECAD_MCP_FREECAD_EXECUTABLE to force a
specific console executable.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, FreeCadOperationError, find_freecad_executable
from freecad_mcp.tools.freecad import collect_freecad_status

_DISCOVERED = find_freecad_executable()

requires_freecad = pytest.mark.skipif(
    _DISCOVERED is None,
    reason="FreeCAD console executable not found (install FreeCAD or set FREECAD_MCP_FREECAD_EXECUTABLE).",
)


def _settings() -> Settings:
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)


@requires_freecad
def test_real_freecad_status_reports_version_and_part() -> None:
    status = collect_freecad_status(_settings())

    assert status.available is True, status.error
    assert status.version
    assert status.part_available is True
    assert status.python_version


@requires_freecad
def test_real_freecad_bridge_ping() -> None:
    bridge = FreeCadBridge.from_settings(_settings())
    assert bridge.ping("hello")["message"] == "hello"


@requires_freecad
def test_real_freecad_tank_and_coil_shapes_and_reopen(tmp_path: Path) -> None:
    """Exercise create_document, tank and tubular_coil in real FreeCAD.

    Each ``bridge.run`` launches a fresh FreeCAD process, so the final
    ``list_objects`` calls prove the saved documents reopen from disk.
    """
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    created = bridge.run("create_document", {"document_id": "itank", "workspace": workspace})
    assert Path(created["saved_path"]).is_file()

    tank = bridge.run(
        "tank",
        {
            "document_id": "itank",
            "workspace": workspace,
            "length": 20.0,
            "width": 15.0,
            "height": 10.0,
        },
    )
    assert tank["object_name"] == "Tank1"

    tank_info = bridge.run(
        "inspect_object", {"document_id": "itank", "workspace": workspace, "object_name": "Tank1"}
    )["info"]
    assert tank_info["type"] == "Part::Box"
    assert tank_info["shape_type"] == "Solid"
    assert tank_info["shape_valid"] is True
    assert tank_info["volume"] == pytest.approx(3000.0)
    assert tank_info["Length"] == pytest.approx(20.0)
    assert tank_info["Width"] == pytest.approx(15.0)
    assert tank_info["Height"] == pytest.approx(10.0)

    tank_validate = bridge.run("validate", {"document_id": "itank", "workspace": workspace})
    assert tank_validate["all_valid"] is True

    tank_reopened = bridge.run("list_objects", {"document_id": "itank", "workspace": workspace})["objects"]
    assert [obj["name"] for obj in tank_reopened] == ["Tank1"]
    assert tank_reopened[0]["shape_type"] == "Solid"
    assert tank_reopened[0]["volume"] == pytest.approx(3000.0)

    created = bridge.run("create_document", {"document_id": "icoil", "workspace": workspace})
    assert Path(created["saved_path"]).is_file()

    coil_radius, tube_radius, num_turns, pitch = 10.0, 2.0, 5, 5.0
    coil = bridge.run(
        "tubular_coil",
        {
            "document_id": "icoil",
            "workspace": workspace,
            "coil_radius": coil_radius,
            "tube_radius": tube_radius,
            "num_turns": num_turns,
            "pitch": pitch,
        },
    )
    assert coil["object_name"] == "Coil1"

    coil_info = bridge.run(
        "inspect_object", {"document_id": "icoil", "workspace": workspace, "object_name": "Coil1"}
    )["info"]
    assert coil_info["type"] == "Part::Feature"
    assert coil_info["shape_type"] == "Solid"
    assert coil_info["shape_valid"] is True
    expected_volume = math.pi * tube_radius**2 * num_turns * math.sqrt(
        (2 * math.pi * coil_radius) ** 2 + pitch**2
    )
    assert coil_info["volume"] > 0
    assert coil_info["volume"] == pytest.approx(expected_volume, rel=0.02)

    coil_validate = bridge.run("validate", {"document_id": "icoil", "workspace": workspace})
    assert coil_validate["all_valid"] is True

    coil_reopened = bridge.run("list_objects", {"document_id": "icoil", "workspace": workspace})["objects"]
    assert [obj["name"] for obj in coil_reopened] == ["Coil1"]
    assert coil_reopened[0]["shape_type"] == "Solid"
    assert coil_reopened[0]["volume"] == pytest.approx(expected_volume, rel=0.02)


@requires_freecad
@pytest.mark.parametrize("op", ["create_document", "tank"])
def test_real_freecad_corrupt_existing_document_is_not_replaced(op: str, tmp_path: Path) -> None:
    """An existing but unopenable .fcstd must be reported, not overwritten."""
    bridge = FreeCadBridge.from_settings(_settings())
    corrupt = tmp_path / "brokendoc.fcstd"
    original = b"this is not a valid fcstd archive"
    corrupt.write_bytes(original)

    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(op, {"document_id": "brokendoc", "workspace": str(tmp_path)})

    assert excinfo.value.error_type == "OperationError"
    assert "Invalid project file" in str(excinfo.value)
    assert corrupt.read_bytes() == original
