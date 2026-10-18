"""Smoke tests for the package skeleton.

These tests must run without FreeCAD installed, which is the whole point of
Milestone 0: prove the environment is reproducible before any CAD code exists.
"""

from __future__ import annotations

import freecad_mcp


def test_package_imports_without_freecad() -> None:
    assert freecad_mcp is not None


def test_version_is_exposed_and_non_empty() -> None:
    assert isinstance(freecad_mcp.__version__, str)
    assert freecad_mcp.__version__
