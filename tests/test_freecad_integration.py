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


@requires_freecad
def test_real_freecad_create_sketch_and_reopen(tmp_path: Path) -> None:
    """Create a Sketcher::SketchObject on the XY plane; reopen from disk."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "sk1", "workspace": workspace})

    sketch = bridge.run(
        "create_sketch",
        {"document_id": "sk1", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert sketch["sketch_name"] == "Sketch1"
    assert sketch["type"] == "Sketcher::SketchObject"
    assert sketch["plane"] == "XY"
    assert sketch["edges"] == 0

    info = bridge.run(
        "inspect_object",
        {"document_id": "sk1", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["type"] == "Sketcher::SketchObject"
    assert info["geometry_count"] == 0
    assert info["constraint_count"] == 0

    reopened = bridge.run("list_objects", {"document_id": "sk1", "workspace": workspace})["objects"]
    assert [obj["name"] for obj in reopened] == ["Sketch1"]
    assert reopened[0]["geometry_count"] == 0


@requires_freecad
def test_real_freecad_sketch_add_rectangle(tmp_path: Path) -> None:
    """Add four connected line segments to a sketch; verify count and persistence."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skrect", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skrect", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    rect = bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skrect",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 10.0,
            "y": 5.0,
            "width": 20.0,
            "height": 15.0,
        },
    )
    assert rect["sketch_name"] == "Sketch1"
    assert rect["x"] == pytest.approx(10.0)
    assert rect["y"] == pytest.approx(5.0)
    assert rect["width"] == pytest.approx(20.0)
    assert rect["height"] == pytest.approx(15.0)
    assert rect["edges_added"] == 4
    assert rect["total_edges"] == 4

    info = bridge.run(
        "inspect_object",
        {"document_id": "skrect", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 4

    # Reopen from disk (fresh FreeCAD process): geometry persists.
    reopened = bridge.run("list_objects", {"document_id": "skrect", "workspace": workspace})["objects"]
    assert [obj["name"] for obj in reopened] == ["Sketch1"]
    assert reopened[0]["geometry_count"] == 4


@requires_freecad
def test_real_freecad_sketch_add_circle(tmp_path: Path) -> None:
    """Add a circle to a sketch; verify center/radius reporting and persistence."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skcirc", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skcirc", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    circle = bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skcirc",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 7.0,
            "center_y": -3.0,
            "radius": 4.0,
        },
    )
    assert circle["sketch_name"] == "Sketch1"
    assert circle["center_x"] == pytest.approx(7.0)
    assert circle["center_y"] == pytest.approx(-3.0)
    assert circle["radius"] == pytest.approx(4.0)
    assert circle["edges_added"] == 1
    assert circle["total_edges"] == 1

    info = bridge.run(
        "inspect_object",
        {"document_id": "skcirc", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 1

    reopened = bridge.run("list_objects", {"document_id": "skcirc", "workspace": workspace})["objects"]
    assert reopened[0]["geometry_count"] == 1


@requires_freecad
def test_real_freecad_multiple_geometries_same_sketch(tmp_path: Path) -> None:
    """A rectangle and a circle accumulate in one sketch."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skmulti", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skmulti", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skmulti",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        },
    )
    circle = bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skmulti",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 1.0,
            "center_y": 1.0,
            "radius": 0.5,
        },
    )
    assert circle["total_edges"] == 5

    info = bridge.run(
        "inspect_object",
        {"document_id": "skmulti", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 5


@requires_freecad
def test_real_freecad_sketch_error_cases(tmp_path: Path) -> None:
    """Missing sketch, wrong object type, and invalid dimensions are structured errors."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skerr", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skerr",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skerr", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_rectangle",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "NoSuchSketch",
                "x": 0.0,
                "y": 0.0,
                "width": 2.0,
                "height": 2.0,
            },
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type: a Part::Box is not a sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_rectangle",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "Box1",
                "x": 0.0,
                "y": 0.0,
                "width": 2.0,
                "height": 2.0,
            },
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)

    # Invalid dimensions.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_rectangle",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "x": 0.0,
                "y": 0.0,
                "width": 0.0,
                "height": 2.0,
            },
        )
    assert "width" in str(excinfo.value).lower()

    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_circle",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": -1.0,
            },
        )
    assert "radius" in str(excinfo.value).lower()


@requires_freecad
def test_real_freecad_sketch_save_reopen_and_existing_objects_unchanged(tmp_path: Path) -> None:
    """Sketch geometry survives save/reopen and other objects in the document stay untouched."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skkeep", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skkeep",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skkeep", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skkeep",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        },
    )

    # Fresh process reopen: box unchanged, sketch holds its geometry.
    reopened = bridge.run("list_objects", {"document_id": "skkeep", "workspace": workspace})["objects"]
    names = [obj["name"] for obj in reopened]
    assert names == ["Box1", "Sketch1"]
    box = next(obj for obj in reopened if obj["name"] == "Box1")
    assert box["volume"] == pytest.approx(64.0)
    sketch = next(obj for obj in reopened if obj["name"] == "Sketch1")
    assert sketch["geometry_count"] == 4

    box_info = bridge.run(
        "inspect_object",
        {"document_id": "skkeep", "workspace": workspace, "object_name": "Box1"},
    )["info"]
    assert box_info["Length"] == pytest.approx(4.0)
    assert box_info["Width"] == pytest.approx(4.0)
    assert box_info["Height"] == pytest.approx(4.0)


@requires_freecad
def test_real_freecad_sketch_add_triangle(tmp_path: Path) -> None:
    """Add three connected line segments forming a closed triangle; verify persistence."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "sktri", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "sktri", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    tri = bridge.run(
        "sketch_add_triangle",
        {
            "document_id": "sktri",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 4.0,
            "y2": 0.0,
            "x3": 2.0,
            "y3": 3.0,
        },
    )
    assert tri["sketch_name"] == "Sketch1"
    assert tri["x1"] == pytest.approx(0.0)
    assert tri["y1"] == pytest.approx(0.0)
    assert tri["x2"] == pytest.approx(4.0)
    assert tri["y2"] == pytest.approx(0.0)
    assert tri["x3"] == pytest.approx(2.0)
    assert tri["y3"] == pytest.approx(3.0)
    assert tri["edges_added"] == 3
    assert tri["total_edges"] == 3

    info = bridge.run(
        "inspect_object",
        {"document_id": "sktri", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 3

    reopened = bridge.run("list_objects", {"document_id": "sktri", "workspace": workspace})["objects"]
    assert [obj["name"] for obj in reopened] == ["Sketch1"]
    assert reopened[0]["geometry_count"] == 3


@requires_freecad
def test_real_freecad_sketch_add_ellipse(tmp_path: Path) -> None:
    """Add an axis-aligned ellipse; verify center and radii persistence."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skell", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skell", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    ell = bridge.run(
        "sketch_add_ellipse",
        {
            "document_id": "skell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": -1.0,
            "major_radius": 4.0,
            "minor_radius": 2.0,
        },
    )
    assert ell["sketch_name"] == "Sketch1"
    assert ell["center_x"] == pytest.approx(5.0)
    assert ell["center_y"] == pytest.approx(-1.0)
    assert ell["major_radius"] == pytest.approx(4.0)
    assert ell["minor_radius"] == pytest.approx(2.0)
    assert ell["edges_added"] == 1
    assert ell["total_edges"] == 1

    info = bridge.run(
        "inspect_object",
        {"document_id": "skell", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 1

    reopened = bridge.run("list_objects", {"document_id": "skell", "workspace": workspace})["objects"]
    assert reopened[0]["geometry_count"] == 1


@requires_freecad
def test_real_freecad_sketch_mixed_geometry(tmp_path: Path) -> None:
    """A triangle and an ellipse accumulate in one sketch alongside existing M9-A geometry."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skmix", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skmix", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skmix",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        },
    )
    bridge.run(
        "sketch_add_triangle",
        {
            "document_id": "skmix",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x1": 0.0,
            "y1": 0.0,
            "x2": 1.0,
            "y2": 0.0,
            "x3": 0.0,
            "y3": 1.0,
        },
    )
    ell = bridge.run(
        "sketch_add_ellipse",
        {
            "document_id": "skmix",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 1.0,
            "center_y": 1.0,
            "major_radius": 1.0,
            "minor_radius": 0.5,
        },
    )
    assert ell["total_edges"] == 8  # 4 rectangle + 3 triangle + 1 ellipse

    info = bridge.run(
        "inspect_object",
        {"document_id": "skmix", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 8  # 4 rectangle + 3 triangle + 1 ellipse


@requires_freecad
def test_real_freecad_sketch_triangle_ellipse_error_cases(tmp_path: Path) -> None:
    """Collinear triangle, invalid radii, missing sketch, and wrong object type are structured errors."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skerr", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skerr",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skerr", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Collinear triangle (zero signed area).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_triangle",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "x1": 0.0,
                "y1": 0.0,
                "x2": 2.0,
                "y2": 2.0,
                "x3": 4.0,
                "y3": 4.0,
            },
        )
    assert "collinear" in str(excinfo.value).lower() or "degenerate" in str(excinfo.value).lower()

    # Invalid ellipse radii.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_ellipse",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": -1.0,
                "minor_radius": 1.0,
            },
        )
    assert "major_radius" in str(excinfo.value).lower()

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_triangle",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "NoSuchSketch",
                "x1": 0.0,
                "y1": 0.0,
                "x2": 1.0,
                "y2": 0.0,
                "x3": 0.0,
                "y3": 1.0,
            },
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type: a Part::Box is not a sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_add_ellipse",
            {
                "document_id": "skerr",
                "workspace": workspace,
                "sketch_name": "Box1",
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 1.0,
                "minor_radius": 1.0,
            },
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)


@requires_freecad
def test_real_freecad_sketch_add_regular_polygon(tmp_path: Path) -> None:
    """Add a regular hexagon; verify vertex count, circumradius, and persistence."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skreg", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skreg", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    poly = bridge.run(
        "sketch_add_regular_polygon",
        {
            "document_id": "skreg",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 3.0,
            "center_y": -2.0,
            "radius": 4.0,
            "sides": 6,
        },
    )
    assert poly["sketch_name"] == "Sketch1"
    assert poly["center_x"] == pytest.approx(3.0)
    assert poly["center_y"] == pytest.approx(-2.0)
    assert poly["radius"] == pytest.approx(4.0)
    assert poly["sides"] == 6
    assert poly["edges_added"] == 6
    assert poly["total_edges"] == 6

    info = bridge.run(
        "inspect_object",
        {"document_id": "skreg", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 6

    reopened = bridge.run("list_objects", {"document_id": "skreg", "workspace": workspace})["objects"]
    assert reopened[0]["geometry_count"] == 6


@requires_freecad
def test_real_freecad_sketch_add_custom_polygon(tmp_path: Path) -> None:
    """Add a custom quadrilateral; verify ordered coordinates and persistence."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skcus", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skcus", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    poly = bridge.run(
        "sketch_add_polygon",
        {
            "document_id": "skcus",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "vertices": [
                {"x": 0.0, "y": 0.0},
                {"x": 2.0, "y": 0.0},
                {"x": 2.0, "y": 1.0},
                {"x": 0.0, "y": 1.0},
            ],
        },
    )
    assert poly["sketch_name"] == "Sketch1"
    assert poly["edges_added"] == 4
    assert poly["total_edges"] == 4

    info = bridge.run(
        "inspect_object",
        {"document_id": "skcus", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 4

    reopened = bridge.run("list_objects", {"document_id": "skcus", "workspace": workspace})["objects"]
    assert reopened[0]["geometry_count"] == 4


@requires_freecad
def test_real_freecad_sketch_mixed_polygon_geometry(tmp_path: Path) -> None:
    """Rectangle, regular polygon, and custom polygon accumulate in one sketch."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skmpoly", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skmpoly", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skmpoly",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 2.0,
        },
    )
    bridge.run(
        "sketch_add_regular_polygon",
        {
            "document_id": "skmpoly",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 3.0,
            "center_y": 3.0,
            "radius": 1.0,
            "sides": 5,
        },
    )
    poly = bridge.run(
        "sketch_add_polygon",
        {
            "document_id": "skmpoly",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "vertices": [
                {"x": 5.0, "y": 0.0},
                {"x": 6.0, "y": 0.0},
                {"x": 5.5, "y": 1.0},
            ],
        },
    )
    assert poly["edges_added"] == 3
    assert poly["total_edges"] == 12  # 4 + 5 + 3

    info = bridge.run(
        "inspect_object",
        {"document_id": "skmpoly", "workspace": workspace, "object_name": "Sketch1"},
    )["info"]
    assert info["geometry_count"] == 12


@requires_freecad
def test_real_freecad_inspect_sketch(tmp_path: Path) -> None:
    """Inspect a sketch with line, circle, and ellipse geometry; verify structure."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skinsp", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skinsp", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Empty sketch inspection.
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skinsp", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["sketch_name"] == "Sketch1"
    assert info["geometry_count"] == 0
    assert info["constraint_count"] == 0
    assert info["geometries"] == []
    assert info["type"] == "Sketcher::SketchObject"

    # Add geometry.
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skinsp",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skinsp",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 2.0,
        },
    )
    bridge.run(
        "sketch_add_ellipse",
        {
            "document_id": "skinsp",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 10.0,
            "center_y": 10.0,
            "major_radius": 4.0,
            "minor_radius": 2.0,
        },
    )

    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skinsp", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 6  # 4 lines (rectangle) + 1 circle + 1 ellipse
    assert len(info["geometries"]) == 6

    # Verify each geometry has index and type.
    for geo in info["geometries"]:
        assert "index" in geo
        assert "type" in geo
        assert isinstance(geo["index"], int)

    # Line segment should have start_point and end_point.
    lines = [g for g in info["geometries"] if g["type"] == "LineSegment"]
    assert len(lines) == 4
    for line in lines:
        assert "start_point" in line
        assert "end_point" in line
        assert "x" in line["start_point"]
        assert "y" in line["start_point"]

    # Circle should have center and radius.
    circles = [g for g in info["geometries"] if g["type"] == "Circle"]
    assert len(circles) == 1
    assert "center" in circles[0]
    assert "radius" in circles[0]

    # Ellipse should have center, major_radius, minor_radius.
    ellipses = [g for g in info["geometries"] if g["type"] == "Ellipse"]
    assert len(ellipses) == 1
    assert "center" in ellipses[0]
    assert "major_radius" in ellipses[0]
    assert "minor_radius" in ellipses[0]


@requires_freecad
def test_real_freecad_inspect_sketch_missing_and_wrong_type(tmp_path: Path) -> None:
    """inspect_sketch returns structured errors for missing sketch and wrong type."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skerr2", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skerr2",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skerr2", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "inspect_sketch",
            {"document_id": "skerr2", "workspace": workspace, "sketch_name": "NoSuchSketch"},
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "inspect_sketch",
            {"document_id": "skerr2", "workspace": workspace, "sketch_name": "Box1"},
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)


@requires_freecad
def test_real_freecad_sketch_delete_geometry(tmp_path: Path) -> None:
    """Delete geometry from a sketch; verify count, persistence, and constraint handling."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skdel", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skdel", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Add geometry: rectangle (4 lines) + circle + ellipse.
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skdel",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skdel",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 2.0,
        },
    )
    bridge.run(
        "sketch_add_ellipse",
        {
            "document_id": "skdel",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 10.0,
            "center_y": 10.0,
            "major_radius": 4.0,
            "minor_radius": 2.0,
        },
    )

    # Inspect: should have 6 geometries (4 lines + 1 circle + 1 ellipse).
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skdel", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 6

    # Delete the circle (index 4, after 4 rectangle lines).
    result = bridge.run(
        "sketch_delete_geometry",
        {
            "document_id": "skdel",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "geometry_index": 4,
        },
    )
    assert result["deleted_index"] == 4
    assert result["geometry_count"] == 5

    # Verify deletion persisted.
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skdel", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 5
    assert len(info["geometries"]) == 5

    # Remaining geometries should be 4 lines + 1 ellipse (circle removed).
    line_types = [g for g in info["geometries"] if g["type"] == "LineSegment"]
    ellipse_types = [g for g in info["geometries"] if g["type"] == "Ellipse"]
    assert len(line_types) == 4
    assert len(ellipse_types) == 1


@requires_freecad
def test_real_freecad_sketch_delete_geometry_errors(tmp_path: Path) -> None:
    """sketch_delete_geometry returns structured errors for missing sketch and wrong type."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skderr", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skderr",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skderr", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_delete_geometry",
            {
                "document_id": "skderr",
                "workspace": workspace,
                "sketch_name": "NoSuchSketch",
                "geometry_index": 0,
            },
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_delete_geometry",
            {
                "document_id": "skderr",
                "workspace": workspace,
                "sketch_name": "Box1",
                "geometry_index": 0,
            },
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)


@requires_freecad
def test_real_freecad_sketch_delete_geometry_out_of_range(tmp_path: Path) -> None:
    """sketch_delete_geometry rejects out-of-range index."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skdor", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skdor", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skdor",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )

    # Index out of range (only 4 lines, index 10 is invalid).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_delete_geometry",
            {
                "document_id": "skdor",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 10,
            },
        )
    assert "out of range" in str(excinfo.value).lower()

    # Geometry count unchanged.
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skdor", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 4


@requires_freecad
def test_real_freecad_sketch_edit_line(tmp_path: Path) -> None:
    """Edit a line in a sketch; verify endpoints, persistence, and mixed sketch."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skedit", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skedit", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Add geometry: rectangle (4 lines) + circle.
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skedit",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skedit",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 2.0,
        },
    )

    # Inspect: should have 5 geometries (4 lines + 1 circle).
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skedit", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 5

    # Edit the first line (index 0) to new endpoints.
    result = bridge.run(
        "sketch_edit_line",
        {
            "document_id": "skedit",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "geometry_index": 0,
            "start_x": 10.0,
            "start_y": 20.0,
            "end_x": 30.0,
            "end_y": 40.0,
        },
    )
    assert result["geometry_index"] == 0
    assert result["start_x"] == pytest.approx(10.0)
    assert result["start_y"] == pytest.approx(20.0)
    assert result["end_x"] == pytest.approx(30.0)
    assert result["end_y"] == pytest.approx(40.0)
    assert result["geometry_count"] == 5

    # Verify edit persisted.
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skedit", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 5
    assert len(info["geometries"]) == 5

    # First geometry should now be a line with the new endpoints.
    first_geo = info["geometries"][0]
    assert first_geo["type"] == "LineSegment"
    assert first_geo["start_point"]["x"] == pytest.approx(10.0)
    assert first_geo["start_point"]["y"] == pytest.approx(20.0)
    assert first_geo["end_point"]["x"] == pytest.approx(30.0)
    assert first_geo["end_point"]["y"] == pytest.approx(40.0)

    # Other geometries preserved: 3 more lines + 1 circle.
    line_types = [g for g in info["geometries"] if g["type"] == "LineSegment"]
    circle_types = [g for g in info["geometries"] if g["type"] == "Circle"]
    assert len(line_types) == 4  # edited line + 3 remaining rectangle lines
    assert len(circle_types) == 1


@requires_freecad
def test_real_freecad_sketch_edit_line_errors(tmp_path: Path) -> None:
    """sketch_edit_line returns structured errors for missing sketch, wrong type, non-line, out-of-range."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skeder", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skeder",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skeder", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skeder",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skeder",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 1.0,
        },
    )

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_line",
            {
                "document_id": "skeder",
                "workspace": workspace,
                "sketch_name": "NoSuchSketch",
                "geometry_index": 0,
                "start_x": 0.0,
                "start_y": 0.0,
                "end_x": 1.0,
                "end_y": 0.0,
            },
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_line",
            {
                "document_id": "skeder",
                "workspace": workspace,
                "sketch_name": "Box1",
                "geometry_index": 0,
                "start_x": 0.0,
                "start_y": 0.0,
                "end_x": 1.0,
                "end_y": 0.0,
            },
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)

    # Non-line geometry (circle at index 4).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_line",
            {
                "document_id": "skeder",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 4,
                "start_x": 0.0,
                "start_y": 0.0,
                "end_x": 1.0,
                "end_y": 0.0,
            },
        )
    assert "LineSegment" in str(excinfo.value)

    # Out-of-range index (only 5 geometries).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_line",
            {
                "document_id": "skeder",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 99,
                "start_x": 0.0,
                "start_y": 0.0,
                "end_x": 1.0,
                "end_y": 0.0,
            },
        )
    assert "out of range" in str(excinfo.value).lower()


@requires_freecad
def test_real_freecad_sketch_edit_circle(tmp_path: Path) -> None:
    """Edit a circle in a sketch; verify center, radius, persistence, and mixed sketch."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skeditcirc", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skeditcirc", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Add geometry: rectangle (4 lines) + circle.
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skeditcirc",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skeditcirc",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 2.0,
        },
    )

    # Inspect: should have 5 geometries (4 lines + 1 circle).
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skeditcirc", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 5

    # Edit the circle (index 4) to new center and radius.
    result = bridge.run(
        "sketch_edit_circle",
        {
            "document_id": "skeditcirc",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "geometry_index": 4,
            "center_x": 10.0,
            "center_y": 20.0,
            "radius": 7.5,
        },
    )
    assert result["geometry_index"] == 4
    assert result["center_x"] == pytest.approx(10.0)
    assert result["center_y"] == pytest.approx(20.0)
    assert result["radius"] == pytest.approx(7.5)
    assert result["geometry_count"] == 5

    # Verify edit persisted.
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skeditcirc", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 5
    assert len(info["geometries"]) == 5

    # Fifth geometry should now be a circle with the new center and radius.
    fifth_geo = info["geometries"][4]
    assert fifth_geo["type"] == "Circle"
    assert fifth_geo["center"]["x"] == pytest.approx(10.0)
    assert fifth_geo["center"]["y"] == pytest.approx(20.0)
    assert fifth_geo["radius"] == pytest.approx(7.5)

    # Other geometries preserved: 4 lines.
    line_types = [g for g in info["geometries"] if g["type"] == "LineSegment"]
    circle_types = [g for g in info["geometries"] if g["type"] == "Circle"]
    assert len(line_types) == 4
    assert len(circle_types) == 1


@requires_freecad
def test_real_freecad_sketch_edit_circle_errors(tmp_path: Path) -> None:
    """sketch_edit_circle returns structured errors for missing sketch, wrong type, non-circle, out-of-range, bad radius."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skederrcirc", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skederrcirc",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skederrcirc", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skederrcirc",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skederrcirc",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 1.0,
        },
    )

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_circle",
            {
                "document_id": "skederrcirc",
                "workspace": workspace,
                "sketch_name": "NoSuchSketch",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 1.0,
            },
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_circle",
            {
                "document_id": "skederrcirc",
                "workspace": workspace,
                "sketch_name": "Box1",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 1.0,
            },
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)

    # Non-circle geometry (line at index 0).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_circle",
            {
                "document_id": "skederrcirc",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 1.0,
            },
        )
    assert "Circle" in str(excinfo.value)

    # Out-of-range index (only 5 geometries).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_circle",
            {
                "document_id": "skederrcirc",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 99,
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 1.0,
            },
        )
    assert "out of range" in str(excinfo.value).lower()

    # Zero radius.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_circle",
            {
                "document_id": "skederrcirc",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 4,
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 0.0,
            },
        )
    assert "positive" in str(excinfo.value).lower()


@requires_freecad
def test_real_freecad_sketch_edit_ellipse(tmp_path: Path) -> None:
    """Edit an ellipse in a sketch; verify center, radii, rotation, persistence, and mixed sketch."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skeditell", "workspace": workspace})
    bridge.run(
        "create_sketch",
        {"document_id": "skeditell", "workspace": workspace, "sketch_name": "Sketch1"},
    )

    # Add geometry: rectangle (4 lines) + circle + ellipse.
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skeditell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_circle",
        {
            "document_id": "skeditell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "radius": 2.0,
        },
    )
    bridge.run(
        "sketch_add_ellipse",
        {
            "document_id": "skeditell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 8.0,
            "center_y": 8.0,
            "major_radius": 4.0,
            "minor_radius": 2.0,
        },
    )

    # Inspect: should have 6 geometries (4 lines + 1 circle + 1 ellipse).
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skeditell", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 6

    # Edit the ellipse (index 5) to new center, radii, and rotation.
    result = bridge.run(
        "sketch_edit_ellipse",
        {
            "document_id": "skeditell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "geometry_index": 5,
            "center_x": 10.0,
            "center_y": 20.0,
            "major_radius": 7.5,
            "minor_radius": 3.0,
            "rotation_deg": 30.0,
        },
    )
    assert result["geometry_index"] == 5
    assert result["center_x"] == pytest.approx(10.0)
    assert result["center_y"] == pytest.approx(20.0)
    assert result["major_radius"] == pytest.approx(7.5)
    assert result["minor_radius"] == pytest.approx(3.0)
    assert result["rotation_deg"] == pytest.approx(30.0)
    assert result["geometry_count"] == 6

    # Verify edit persisted.
    info = bridge.run(
        "inspect_sketch",
        {"document_id": "skeditell", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    assert info["geometry_count"] == 6
    assert len(info["geometries"]) == 6

    # Sixth geometry should now be an ellipse with the new parameters.
    sixth_geo = info["geometries"][5]
    assert sixth_geo["type"] == "Ellipse"
    assert sixth_geo["center"]["x"] == pytest.approx(10.0)
    assert sixth_geo["center"]["y"] == pytest.approx(20.0)
    assert sixth_geo["major_radius"] == pytest.approx(7.5)
    assert sixth_geo["minor_radius"] == pytest.approx(3.0)

    # Other geometries preserved: 4 lines + 1 circle.
    line_types = [g for g in info["geometries"] if g["type"] == "LineSegment"]
    circle_types = [g for g in info["geometries"] if g["type"] == "Circle"]
    ellipse_types = [g for g in info["geometries"] if g["type"] == "Ellipse"]
    assert len(line_types) == 4
    assert len(circle_types) == 1
    assert len(ellipse_types) == 1


@requires_freecad
def test_real_freecad_sketch_edit_ellipse_errors(tmp_path: Path) -> None:
    """sketch_edit_ellipse returns structured errors for missing sketch, wrong type, non-ellipse, out-of-range, bad radii, ordering."""
    bridge = FreeCadBridge.from_settings(_settings())
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": "skederrell", "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": "skederrell",
            "workspace": workspace,
            "object_name": "Box1",
            "length": 4.0,
            "width": 4.0,
            "height": 4.0,
        },
    )
    bridge.run(
        "create_sketch",
        {"document_id": "skederrell", "workspace": workspace, "sketch_name": "Sketch1"},
    )
    bridge.run(
        "sketch_add_rectangle",
        {
            "document_id": "skederrell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "x": 0.0,
            "y": 0.0,
            "width": 2.0,
            "height": 1.0,
        },
    )
    bridge.run(
        "sketch_add_ellipse",
        {
            "document_id": "skederrell",
            "workspace": workspace,
            "sketch_name": "Sketch1",
            "center_x": 5.0,
            "center_y": 5.0,
            "major_radius": 4.0,
            "minor_radius": 2.0,
        },
    )

    # Missing sketch.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_ellipse",
            {
                "document_id": "skederrell",
                "workspace": workspace,
                "sketch_name": "NoSuchSketch",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 1.0,
                "minor_radius": 1.0,
            },
        )
    assert "not found" in str(excinfo.value).lower()

    # Wrong object type.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_ellipse",
            {
                "document_id": "skederrell",
                "workspace": workspace,
                "sketch_name": "Box1",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 1.0,
                "minor_radius": 1.0,
            },
        )
    assert "Sketcher::SketchObject" in str(excinfo.value)

    # Non-ellipse geometry (line at index 0).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_ellipse",
            {
                "document_id": "skederrell",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 0,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 1.0,
                "minor_radius": 1.0,
            },
        )
    assert "Ellipse" in str(excinfo.value)

    # Out-of-range index (only 5 geometries).
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_ellipse",
            {
                "document_id": "skederrell",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 99,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 1.0,
                "minor_radius": 1.0,
            },
        )
    assert "out of range" in str(excinfo.value).lower()

    # Zero major radius.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_ellipse",
            {
                "document_id": "skederrell",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 4,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 0.0,
                "minor_radius": 1.0,
            },
        )
    assert "positive" in str(excinfo.value).lower()

    # major_radius less than minor_radius.
    with pytest.raises(FreeCadOperationError) as excinfo:
        bridge.run(
            "sketch_edit_ellipse",
            {
                "document_id": "skederrell",
                "workspace": workspace,
                "sketch_name": "Sketch1",
                "geometry_index": 4,
                "center_x": 0.0,
                "center_y": 0.0,
                "major_radius": 2.0,
                "minor_radius": 5.0,
            },
        )
    assert "greater than or equal" in str(excinfo.value).lower()
