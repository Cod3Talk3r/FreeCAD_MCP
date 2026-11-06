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
from freecad_mcp.freecad_bridge import (
    FreeCadBridge,
    FreeCadOperationError,
    find_freecad_executable,
)
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

    created = bridge.run(
        "create_document", {"document_id": "itank", "workspace": workspace}
    )
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
        "inspect_object",
        {"document_id": "itank", "workspace": workspace, "object_name": "Tank1"},
    )["info"]
    assert tank_info["type"] == "Part::Box"
    assert tank_info["shape_type"] == "Solid"
    assert tank_info["shape_valid"] is True
    assert tank_info["volume"] == pytest.approx(3000.0)
    assert tank_info["Length"] == pytest.approx(20.0)
    assert tank_info["Width"] == pytest.approx(15.0)
    assert tank_info["Height"] == pytest.approx(10.0)

    tank_validate = bridge.run(
        "validate", {"document_id": "itank", "workspace": workspace}
    )
    assert tank_validate["all_valid"] is True

    tank_reopened = bridge.run(
        "list_objects", {"document_id": "itank", "workspace": workspace}
    )["objects"]
    assert [obj["name"] for obj in tank_reopened] == ["Tank1"]
    assert tank_reopened[0]["shape_type"] == "Solid"
    assert tank_reopened[0]["volume"] == pytest.approx(3000.0)

    created = bridge.run(
        "create_document", {"document_id": "icoil", "workspace": workspace}
    )
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
        "inspect_object",
        {"document_id": "icoil", "workspace": workspace, "object_name": "Coil1"},
    )["info"]
    assert coil_info["type"] == "Part::Feature"
    assert coil_info["shape_type"] == "Solid"
    assert coil_info["shape_valid"] is True
    expected_volume = (
        math.pi
        * tube_radius**2
        * num_turns
        * math.sqrt((2 * math.pi * coil_radius) ** 2 + pitch**2)
    )
    assert coil_info["volume"] > 0
    assert coil_info["volume"] == pytest.approx(expected_volume, rel=0.02)

    coil_validate = bridge.run(
        "validate", {"document_id": "icoil", "workspace": workspace}
    )
    assert coil_validate["all_valid"] is True

    coil_reopened = bridge.run(
        "list_objects", {"document_id": "icoil", "workspace": workspace}
    )["objects"]
    assert [obj["name"] for obj in coil_reopened] == ["Coil1"]
    assert coil_reopened[0]["shape_type"] == "Solid"
    assert coil_reopened[0]["volume"] == pytest.approx(expected_volume, rel=0.02)


@requires_freecad
@pytest.mark.parametrize("op", ["create_document", "tank"])
def test_real_freecad_corrupt_existing_document_is_not_replaced(
    op: str, tmp_path: Path
) -> None:
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


@requires_freecad
def test_real_freecad_boolean_cut_hollow_cylinder(tmp_path: Path) -> None:
    """Cut a concentric inner cylinder from an outer one; verify a hollow solid.

    Each ``bridge.run`` launches a fresh FreeCAD process, so the final
    ``list_objects`` call proves the saved document reopens with the cut result
    and the unchanged source objects.
    """
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    created = bridge.run(
        "create_document", {"document_id": "ihollow", "workspace": workspace}
    )
    assert Path(created["saved_path"]).is_file()

    bridge.run(
        "create_cylinder",
        {
            "document_id": "ihollow",
            "workspace": workspace,
            "object_name": "OuterCyl",
            "radius": 2.0,
            "height": 10.0,
        },
    )
    bridge.run(
        "create_cylinder",
        {
            "document_id": "ihollow",
            "workspace": workspace,
            "object_name": "InnerCyl",
            "radius": 1.0,
            "height": 10.0,
        },
    )

    cut = bridge.run(
        "boolean_cut",
        {
            "document_id": "ihollow",
            "workspace": workspace,
            "base_object": "OuterCyl",
            "tool_object": "InnerCyl",
            "result_name": "HollowCylinder",
        },
    )
    expected_volume = math.pi * (2.0**2 - 1.0**2) * 10.0

    assert cut["object_name"] == "HollowCylinder"
    assert cut["base_object"] == "OuterCyl"
    assert cut["tool_object"] == "InnerCyl"
    assert cut["shape_type"] == "Solid"
    assert cut["volume"] == pytest.approx(expected_volume, rel=1e-6)
    assert cut["length"] == pytest.approx(4.0, rel=1e-6)
    assert cut["width"] == pytest.approx(4.0, rel=1e-6)
    assert cut["height"] == pytest.approx(10.0, rel=1e-6)

    validated = bridge.run(
        "validate", {"document_id": "ihollow", "workspace": workspace}
    )
    assert validated["all_valid"] is True
    hollow_validated = next(
        obj for obj in validated["objects"] if obj["name"] == "HollowCylinder"
    )
    assert hollow_validated["valid"] is True
    assert hollow_validated["shape_type"] == "Solid"

    reopened = bridge.run(
        "list_objects", {"document_id": "ihollow", "workspace": workspace}
    )["objects"]
    names = [obj["name"] for obj in reopened]
    assert names == ["OuterCyl", "InnerCyl", "HollowCylinder"]

    hollow = next(obj for obj in reopened if obj["name"] == "HollowCylinder")
    assert hollow["shape_type"] == "Solid"
    assert hollow["volume"] == pytest.approx(expected_volume, rel=1e-6)

    # Source objects remain unchanged.
    outer = next(obj for obj in reopened if obj["name"] == "OuterCyl")
    inner = next(obj for obj in reopened if obj["name"] == "InnerCyl")
    assert outer["volume"] == pytest.approx(math.pi * 2.0**2 * 10.0, rel=1e-6)
    assert inner["volume"] == pytest.approx(math.pi * 1.0**2 * 10.0, rel=1e-6)


@requires_freecad
def test_real_freecad_boolean_cut_error_cases(tmp_path: Path) -> None:
    """Missing operands, identical names and empty results become structured errors."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "icuterr", "workspace": workspace})
    for name, radius in (("A", 2.0), ("B", 1.0), ("B2", 1.0)):
        bridge.run(
            "create_cylinder",
            {
                "document_id": "icuterr",
                "workspace": workspace,
                "object_name": name,
                "radius": radius,
                "height": 10.0,
            },
        )

    def _cut(base: str, tool: str) -> None:
        bridge.run(
            "boolean_cut",
            {
                "document_id": "icuterr",
                "workspace": workspace,
                "base_object": base,
                "tool_object": tool,
            },
        )

    with pytest.raises(FreeCadOperationError) as missing:
        _cut("A", "Missing")
    assert "not found" in str(missing.value)

    with pytest.raises(FreeCadOperationError) as same:
        _cut("A", "A")
    assert "different" in str(same.value).lower()

    # B and B2 are identical, coincident solids: subtracting one from the other
    # yields an empty shape, which must be rejected rather than saved.
    with pytest.raises(FreeCadOperationError) as empty:
        _cut("B", "B2")
    assert "empty" in str(empty.value).lower()


@requires_freecad
def test_real_freecad_boolean_fuse_overlapping_boxes(tmp_path: Path) -> None:
    """Fuse two overlapping boxes; verify valid solid result and expected volume.

    Box A: 10x10x10 at (0,0,0), volume 1000.
    Box B: 10x10x10 at (5,5,5), volume 1000.
    Overlap: 5x5x5 at (5,5,5), volume 125.
    Fuse volume = 1000 + 1000 - 125 = 1875.
    """
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "fuse1", "workspace": workspace}
    )
    bridge.run(
        "create_box",
        {
            "document_id": "fuse1",
            "workspace": workspace,
            "object_name": "BoxA",
            "length": 10.0,
            "width": 10.0,
            "height": 10.0,
        },
    )

    bridge.run(
        "create_box",
        {
            "document_id": "fuse1",
            "workspace": workspace,
            "object_name": "BoxB",
            "length": 10.0,
            "width": 10.0,
            "height": 10.0,
            "x": 5.0,
            "y": 5.0,
            "z": 5.0,
        },
    )

    fused = bridge.run(
        "boolean_fuse",
        {
            "document_id": "fuse1",
            "workspace": workspace,
            "base_object": "BoxA",
            "tool_object": "BoxB",
            "result_name": "Fused1",
        },
    )
    expected_volume = 1000.0 + 1000.0 - 125.0

    assert fused["object_name"] == "Fused1"
    assert fused["base_object"] == "BoxA"
    assert fused["tool_object"] == "BoxB"
    assert fused["shape_type"] == "Solid"
    assert fused["volume"] == pytest.approx(expected_volume, rel=1e-6)

    reopened = bridge.run(
        "list_objects", {"document_id": "fuse1", "workspace": workspace}
    )["objects"]
    names = [obj["name"] for obj in reopened]
    assert names == ["BoxA", "BoxB", "Fused1"]

    fused_reopened = next(obj for obj in reopened if obj["name"] == "Fused1")
    assert fused_reopened["shape_type"] == "Solid"
    assert fused_reopened["volume"] == pytest.approx(expected_volume, rel=1e-6)

    # Source objects remain unchanged.
    a = next(obj for obj in reopened if obj["name"] == "BoxA")
    b = next(obj for obj in reopened if obj["name"] == "BoxB")
    assert a["volume"] == pytest.approx(1000.0, rel=1e-6)
    assert b["volume"] == pytest.approx(1000.0, rel=1e-6)


@requires_freecad
def test_real_freecad_boolean_common_overlapping_boxes(tmp_path: Path) -> None:
    """Intersect two overlapping boxes; verify valid solid result and expected volume.

    Box A: 10x10x10 at (0,0,0), volume 1000.
    Box B: 10x10x10 at (5,5,5), volume 1000.
    Overlap: 5x5x5 at (5,5,5), volume 125.
    Common volume = 125.
    """
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "common1", "workspace": workspace}
    )

    bridge.run(
        "create_box",
        {
            "document_id": "common1",
            "workspace": workspace,
            "object_name": "BoxA",
            "length": 10.0,
            "width": 10.0,
            "height": 10.0,
        },
    )
    bridge.run(
        "create_box",
        {
            "document_id": "common1",
            "workspace": workspace,
            "object_name": "BoxB",
            "length": 10.0,
            "width": 10.0,
            "height": 10.0,
            "x": 5.0,
            "y": 5.0,
            "z": 5.0,
        },
    )

    common = bridge.run(
        "boolean_common",
        {
            "document_id": "common1",
            "workspace": workspace,
            "base_object": "BoxA",
            "tool_object": "BoxB",
            "result_name": "Common1",
        },
    )

    assert common["object_name"] == "Common1"
    assert common["base_object"] == "BoxA"
    assert common["tool_object"] == "BoxB"
    assert common["shape_type"] == "Solid"
    assert common["volume"] == pytest.approx(125.0, rel=1e-6)

    reopened = bridge.run(
        "list_objects", {"document_id": "common1", "workspace": workspace}
    )["objects"]
    names = [obj["name"] for obj in reopened]
    assert names == ["BoxA", "BoxB", "Common1"]

    common_reopened = next(obj for obj in reopened if obj["name"] == "Common1")
    assert common_reopened["shape_type"] == "Solid"
    assert common_reopened["volume"] == pytest.approx(125.0, rel=1e-6)

    # Source objects remain unchanged.
    a = next(obj for obj in reopened if obj["name"] == "BoxA")
    b = next(obj for obj in reopened if obj["name"] == "BoxB")
    assert a["volume"] == pytest.approx(1000.0, rel=1e-6)
    assert b["volume"] == pytest.approx(1000.0, rel=1e-6)


@requires_freecad
def test_real_freecad_boolean_fuse_common_error_cases(tmp_path: Path) -> None:
    """Missing operands and identical names become structured errors for fuse and common."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "boolerr", "workspace": workspace}
    )
    for name in ("A", "B"):
        bridge.run(
            "create_box",
            {
                "document_id": "boolerr",
                "workspace": workspace,
                "object_name": name,
                "length": 10.0,
                "width": 10.0,
                "height": 10.0,
            },
        )

    def _fuse(base: str, tool: str) -> None:
        bridge.run(
            "boolean_fuse",
            {
                "document_id": "boolerr",
                "workspace": workspace,
                "base_object": base,
                "tool_object": tool,
            },
        )

    def _common(base: str, tool: str) -> None:
        bridge.run(
            "boolean_common",
            {
                "document_id": "boolerr",
                "workspace": workspace,
                "base_object": base,
                "tool_object": tool,
            },
        )

    # fuse: missing tool
    with pytest.raises(FreeCadOperationError) as excinfo:
        _fuse("A", "Missing")
    assert "not found" in str(excinfo.value)

    # fuse: identical operands
    with pytest.raises(FreeCadOperationError) as excinfo:
        _fuse("A", "A")
    assert "different" in str(excinfo.value).lower()

    # common: missing base
    with pytest.raises(FreeCadOperationError) as excinfo:
        _common("Missing", "B")
    assert "not found" in str(excinfo.value)

    # common: identical operands
    with pytest.raises(FreeCadOperationError) as excinfo:
        _common("B", "B")
    assert "different" in str(excinfo.value).lower()


@requires_freecad
def test_real_freecad_create_box_placement_and_reopen(tmp_path: Path) -> None:
    """Create a box with placement through Method A, then independently reopen
    the saved FCStd document and verify dimensions, placement, validity, and
    solid type from the reopened object.

    Each ``bridge.run`` launches a fresh FreeCAD process that opens the saved
    document from disk, so ``inspect_object`` and ``list_objects`` constitute
    independent reopens rather than in-memory checks.
    """
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)
    document_id = "m7a2_box"

    # Create the workspace document.
    created = bridge.run(
        "create_document", {"document_id": document_id, "workspace": workspace}
    )
    assert Path(created["saved_path"]).is_file()

    # Execute create_box through the Method A bridge with non-zero placement.
    box = bridge.run(
        "create_box",
        {
            "document_id": document_id,
            "workspace": workspace,
            "object_name": "Box1",
            "length": 50.0,
            "width": 30.0,
            "height": 20.0,
            "x": 100.0,
            "y": 50.0,
            "z": 25.0,
        },
    )
    assert box["object_name"] == "Box1"
    assert box["length"] == pytest.approx(50.0)
    assert box["width"] == pytest.approx(30.0)
    assert box["height"] == pytest.approx(20.0)
    assert box["x"] == pytest.approx(100.0)
    assert box["y"] == pytest.approx(50.0)
    assert box["z"] == pytest.approx(25.0)

    # Independently reopen: inspect_object launches a fresh FreeCAD process
    # that opens the saved document from disk.
    info = bridge.run(
        "inspect_object",
        {
            "document_id": document_id,
            "workspace": workspace,
            "object_name": "Box1",
        },
    )["info"]
    assert info["type"] == "Part::Box"
    assert info["shape_type"] == "Solid"
    assert info["shape_valid"] is True
    assert info["Length"] == pytest.approx(50.0)
    assert info["Width"] == pytest.approx(30.0)
    assert info["Height"] == pytest.approx(20.0)
    assert info["placement_x"] == pytest.approx(100.0)
    assert info["placement_y"] == pytest.approx(50.0)
    assert info["placement_z"] == pytest.approx(25.0)

    # Independently reopen again via list_objects (another fresh process).
    reopened = bridge.run(
        "list_objects", {"document_id": document_id, "workspace": workspace}
    )["objects"]
    assert [obj["name"] for obj in reopened] == ["Box1"]
    reopened_box = next(obj for obj in reopened if obj["name"] == "Box1")
    assert reopened_box["shape_type"] == "Solid"
    assert reopened_box["length"] == pytest.approx(50.0)
    assert reopened_box["width"] == pytest.approx(30.0)
    assert reopened_box["height"] == pytest.approx(20.0)

    # Validate the document.
    validated = bridge.run(
        "validate", {"document_id": document_id, "workspace": workspace}
    )
    assert validated["all_valid"] is True


@requires_freecad
def test_real_freecad_set_placement_box(tmp_path: Path) -> None:
    """Move an existing Box; verify position updated and dimensions preserved."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "sbox", "workspace": workspace}
    )
    bridge.run(
        "create_box",
        {
            "document_id": "sbox",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 10.0,
            "width": 5.0,
            "height": 3.0,
        },
    )

    moved = bridge.run(
        "set_placement",
        {
            "document_id": "sbox",
            "workspace": workspace,
            "object_name": "Box1",
            "x": 10.0,
            "y": 20.0,
            "z": 30.0,
        },
    )
    assert moved["x"] == pytest.approx(10.0)
    assert moved["y"] == pytest.approx(20.0)
    assert moved["z"] == pytest.approx(30.0)

    info = bridge.run(
        "inspect_object",
        {"document_id": "sbox", "workspace": workspace, "object_name": "Box1"},
    )["info"]
    assert info["placement_x"] == pytest.approx(10.0)
    assert info["placement_y"] == pytest.approx(20.0)
    assert info["placement_z"] == pytest.approx(30.0)
    assert info["Length"] == pytest.approx(10.0)
    assert info["Width"] == pytest.approx(5.0)
    assert info["Height"] == pytest.approx(3.0)

    reopened = bridge.run(
        "list_objects", {"document_id": "sbox", "workspace": workspace}
    )["objects"]
    box = next(o for o in reopened if o["name"] == "Box1")
    assert box["length"] == pytest.approx(10.0)
    assert box["width"] == pytest.approx(5.0)
    assert box["height"] == pytest.approx(3.0)


@requires_freecad
def test_real_freecad_set_placement_cylinder(tmp_path: Path) -> None:
    """Move an existing Cylinder; verify position updated and dimensions preserved."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "scyl", "workspace": workspace}
    )
    bridge.run(
        "create_cylinder",
        {
            "document_id": "scyl",
            "workspace": workspace,
            "object_name": "Cyl1",
            "radius": 2.0,
            "height": 7.0,
        },
    )

    moved = bridge.run(
        "set_placement",
        {
            "document_id": "scyl",
            "workspace": workspace,
            "object_name": "Cyl1",
            "x": 5.0,
            "y": -3.0,
            "z": 8.0,
        },
    )
    assert moved["x"] == pytest.approx(5.0)
    assert moved["y"] == pytest.approx(-3.0)
    assert moved["z"] == pytest.approx(8.0)

    info = bridge.run(
        "inspect_object",
        {"document_id": "scyl", "workspace": workspace, "object_name": "Cyl1"},
    )["info"]
    assert info["placement_x"] == pytest.approx(5.0)
    assert info["placement_y"] == pytest.approx(-3.0)
    assert info["placement_z"] == pytest.approx(8.0)
    assert info["Radius"] == pytest.approx(2.0)
    assert info["Height"] == pytest.approx(7.0)


@requires_freecad
def test_real_freecad_set_placement_preserves_rotation(tmp_path: Path) -> None:
    """Set placement twice; second call lands at new coords (Rotation preserved)."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "srot", "workspace": workspace}
    )
    bridge.run(
        "create_box",
        {
            "document_id": "srot",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )

    bridge.run(
        "set_placement",
        {"document_id": "srot", "workspace": workspace, "object_name": "Box1", "x": 1.0, "y": 2.0, "z": 3.0},
    )
    info_first = bridge.run(
        "inspect_object",
        {"document_id": "srot", "workspace": workspace, "object_name": "Box1"},
    )["info"]
    assert info_first["placement_x"] == pytest.approx(1.0)
    assert info_first["placement_y"] == pytest.approx(2.0)
    assert info_first["placement_z"] == pytest.approx(3.0)

    bridge.run(
        "set_placement",
        {"document_id": "srot", "workspace": workspace, "object_name": "Box1", "x": 9.0, "y": 8.0, "z": 7.0},
    )
    info_second = bridge.run(
        "inspect_object",
        {"document_id": "srot", "workspace": workspace, "object_name": "Box1"},
    )["info"]
    assert info_second["placement_x"] == pytest.approx(9.0)
    assert info_second["placement_y"] == pytest.approx(8.0)
    assert info_second["placement_z"] == pytest.approx(7.0)
    assert info_second["Length"] == pytest.approx(4.0)


@requires_freecad
def test_real_freecad_set_placement_missing_object(tmp_path: Path) -> None:
    """set_placement on a missing object returns a structured error."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "smiss", "workspace": workspace}
    )

    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "set_placement",
            {
                "document_id": "smiss",
                "workspace": workspace,
                "object_name": "DoesNotExist",
                "x": 1.0,
                "y": 2.0,
                "z": 3.0,
            },
        )
    assert "not found" in str(excinfo.value).lower()


@requires_freecad
def test_real_freecad_set_placement_boolean_cut_result(tmp_path: Path) -> None:
    """set_placement on a Boolean Cut result (Part::Feature) now succeeds."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "sfeat", "workspace": workspace}
    )
    bridge.run(
        "create_cylinder",
        {
            "document_id": "sfeat",
            "workspace": workspace,
            "object_name": "Outer",
            "radius": 2.0,
            "height": 10.0,
        },
    )
    bridge.run(
        "create_cylinder",
        {
            "document_id": "sfeat",
            "workspace": workspace,
            "object_name": "Inner",
            "radius": 1.0,
            "height": 10.0,
        },
    )
    bridge.run(
        "boolean_cut",
        {
            "document_id": "sfeat",
            "workspace": workspace,
            "base_object": "Outer",
            "tool_object": "Inner",
            "result_name": "Cut1",
        },
    )

    # Part::Feature results are now supported for placement editing.
    moved = bridge.run(
        "set_placement",
        {
            "document_id": "sfeat",
            "workspace": workspace,
            "object_name": "Cut1",
            "x": 1.0,
            "y": 2.0,
            "z": 3.0,
        },
    )
    assert moved["x"] == pytest.approx(1.0)
    assert moved["y"] == pytest.approx(2.0)
    assert moved["z"] == pytest.approx(3.0)

    info = bridge.run(
        "inspect_object",
        {"document_id": "sfeat", "workspace": workspace, "object_name": "Cut1"},
    )["info"]
    assert info["placement_x"] == pytest.approx(1.0)
    assert info["placement_y"] == pytest.approx(2.0)
    assert info["placement_z"] == pytest.approx(3.0)
    assert info["shape_type"] == "Solid"


@requires_freecad
def test_real_freecad_set_placement_boolean_cut_result_reopen(tmp_path: Path) -> None:
    """Create two boxes, boolean_cut, move the result, reopen and verify placement + geometry."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "sreopen", "workspace": workspace}
    )

    bridge.run(
        "create_box",
        {
            "document_id": "sreopen",
            "workspace": workspace,
            "object_name": "OuterBox",
            "length": 10.0,
            "width": 10.0,
            "height": 10.0,
        },
    )
    bridge.run(
        "create_box",
        {
            "document_id": "sreopen",
            "workspace": workspace,
            "object_name": "InnerBox",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )

    cut = bridge.run(
        "boolean_cut",
        {
            "document_id": "sreopen",
            "workspace": workspace,
            "base_object": "OuterBox",
            "tool_object": "InnerBox",
            "result_name": "Cut1",
        },
    )
    expected_volume = 10.0**3 - 4.0**3
    assert cut["shape_type"] == "Solid"
    assert cut["volume"] == pytest.approx(expected_volume, rel=1e-6)

    # Move the boolean cut result.
    moved = bridge.run(
        "set_placement",
        {
            "document_id": "sreopen",
            "workspace": workspace,
            "object_name": "Cut1",
            "x": 50.0,
            "y": 60.0,
            "z": 70.0,
        },
    )
    assert moved["x"] == pytest.approx(50.0)
    assert moved["y"] == pytest.approx(60.0)
    assert moved["z"] == pytest.approx(70.0)

    # Reopen via inspect_object (fresh FreeCAD process).
    info = bridge.run(
        "inspect_object",
        {"document_id": "sreopen", "workspace": workspace, "object_name": "Cut1"},
    )["info"]
    assert info["placement_x"] == pytest.approx(50.0)
    assert info["placement_y"] == pytest.approx(60.0)
    assert info["placement_z"] == pytest.approx(70.0)
    assert info["shape_type"] == "Solid"
    assert info["volume"] == pytest.approx(expected_volume, rel=1e-6)

    # Reopen again via list_objects (another fresh process).
    reopened = bridge.run(
        "list_objects", {"document_id": "sreopen", "workspace": workspace}
    )["objects"]
    names = [obj["name"] for obj in reopened]
    assert names == ["OuterBox", "InnerBox", "Cut1"]
    cut_reopened = next(o for o in reopened if o["name"] == "Cut1")
    assert cut_reopened["shape_type"] == "Solid"
    assert cut_reopened["volume"] == pytest.approx(expected_volume, rel=1e-6)

    # Source objects unchanged.
    outer = next(o for o in reopened if o["name"] == "OuterBox")
    inner = next(o for o in reopened if o["name"] == "InnerBox")
    assert outer["volume"] == pytest.approx(1000.0, rel=1e-6)
    assert inner["volume"] == pytest.approx(64.0, rel=1e-6)


@requires_freecad
def test_real_freecad_set_placement_invalid_numeric(tmp_path: Path) -> None:
    """set_placement with NaN or infinity returns a structured error."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run(
        "create_document", {"document_id": "sinvalid", "workspace": workspace}
    )
    bridge.run(
        "create_box",
        {
            "document_id": "sinvalid",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )

    for bad_value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(FreeCadOperationError) as excinfo:
            bridge.run(
                "set_placement",
                {
                    "document_id": "sinvalid",
                    "workspace": workspace,
                    "object_name": "Box1",
                    "x": bad_value,
                    "y": 2.0,
                    "z": 3.0,
                },
            )
        assert "finite" in str(excinfo.value).lower()
