"""Opt-in real Taiga + FreeCAD integration test for the native episode runtime.

Skipped unless ``FREECAD_MCP_FREECAD_EXECUTABLE``, ``FREECAD_MCP_TAIGA_SOURCE``,
``FREECAD_MCP_TAIGA_PYTHON`` and ``FREECAD_MCP_TAIGA_MODEL`` are set.  Exercises
the full M20 Phase 4A path: MCP-level ``taiga_episode`` → isolated torch child
(``runner.py`` ``run_episode`` op) → ``NativeWorkerEnv`` → ``freecadcmd`` native
``Worker.serve()`` → native ``run_episodes`` loop.

The parent test module never imports ``freecad_s1``, ``torch`` or ``FreeCAD``.
"""

from __future__ import annotations

import json
import os

import pytest

from freecad_mcp.config import Settings, load_settings
from freecad_mcp.tools.taiga import taiga_episode


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _skip_unless_configured() -> Settings:
    missing = [
        name
        for name in (
            "FREECAD_MCP_FREECAD_EXECUTABLE",
            "FREECAD_MCP_TAIGA_SOURCE",
            "FREECAD_MCP_TAIGA_PYTHON",
            "FREECAD_MCP_TAIGA_MODEL",
        )
        if not _env(name)
    ]
    if missing:
        pytest.skip(
            "Set " + ", ".join(missing) + " to run the M20 Phase 4A native "
            "episode integration test."
        )
    return load_settings()


def _goal() -> dict:
    return {
        "features": [{"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}}],
        "scale": 40.0,
    }


def test_episode_success() -> None:
    """A simple base_box goal reaches success with IoU >= 0.99."""
    settings = _skip_unless_configured()
    result = taiga_episode(settings, _goal())
    assert result.ok is True, f"episode failed: {result.error!r}"
    assert result.status == "success"
    assert result.success is True
    assert result.match is True
    assert result.iou >= 0.99
    assert result.steps <= result.budget
    assert result.final_state is not None
    assert result.final_state.get("doc_open") is True
    tree = result.final_state.get("tree") or []
    assert any(n.get("type") == "PartDesign::Body" for n in tree), (
        f"final state has no PartDesign Body: {tree!r}"
    )


def test_episode_invalid_goal() -> None:
    """A malformed goal is rejected parent-side before any child spawn."""
    settings = _skip_unless_configured()
    result = taiga_episode(settings, {"features": []})
    assert result.ok is False
    assert result.status == "invalid_goal"
    assert result.error_type == "InvalidGoalError"


def test_episode_with_start_spec() -> None:
    """An explicit start spec is honored and echoed."""
    settings = _skip_unless_configured()
    result = taiga_episode(
        settings,
        _goal(),
        start={"doc_open": True, "workbench": "PartDesignWorkbench", "body": False},
    )
    assert result.ok is True, f"episode failed: {result.error!r}"
    assert result.start is not None
    assert result.start.get("workbench") == "PartDesignWorkbench"


def test_episode_offline_no_network() -> None:
    """The model loads from a local dir; no network is contacted.

    We assert the configured model is a directory (runner.py rejects remote ids)
    and that the episode still succeeds, which proves offline local loading.
    """
    settings = _skip_unless_configured()
    assert settings.taiga_model and os.path.isdir(settings.taiga_model)
    result = taiga_episode(settings, _goal())
    assert result.ok is True