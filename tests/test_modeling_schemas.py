"""Tests for M4 modeling tool schemas (TankParams, TubularCoilParams)."""

from __future__ import annotations

import pytest

from freecad_mcp.tools.modeling import TankParams, TubularCoilParams


def test_tank_params_valid():
    """Valid TankParams should be accepted."""
    t = TankParams(document_id="mydoc", length=10.0, width=5.0, height=3.0)
    assert t.document_id == "mydoc"
    assert t.length == 10.0
    assert t.width == 5.0
    assert t.height == 3.0
    assert t.object_name == "Tank1"


def test_tank_params_defaults():
    """TankParams should use defaults when not specified."""
    t = TankParams(document_id="mydoc")
    assert t.length == 20.0
    assert t.width == 15.0
    assert t.height == 10.0
    assert t.object_name == "Tank1"


def test_tubular_coil_params_valid():
    """Valid TubularCoilParams should be accepted."""
    tc = TubularCoilParams(
        document_id="mydoc",
        coil_radius=15.0,
        tube_radius=3.0,
        num_turns=10,
        pitch=8.0,
    )
    assert tc.coil_radius == 15.0
    assert tc.tube_radius == 3.0
    assert tc.num_turns == 10
    assert tc.pitch == 8.0


def test_tubular_coil_params_defaults():
    """TubularCoilParams should use defaults when not specified."""
    tc = TubularCoilParams(document_id="mydoc")
    assert tc.coil_radius == 10.0
    assert tc.tube_radius == 2.0
    assert tc.num_turns == 5
    assert tc.pitch == 5.0


def test_tubular_coil_params_zero_coil_radius():
    """Zero coil_radius is accepted by schema (validated manually in handler)."""
    tc = TubularCoilParams(document_id="mydoc", coil_radius=0)
    assert tc.coil_radius == 0


def test_tubular_coil_params_negative_tube_radius():
    """Negative tube_radius is accepted by schema (validated manually in handler)."""
    tc = TubularCoilParams(document_id="mydoc", tube_radius=-5.0)
    assert tc.tube_radius == -5.0