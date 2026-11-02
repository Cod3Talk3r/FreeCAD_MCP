"""Optional integration tests against a real Taiga-S1 runtime.

These tests are skipped unless both ``FREECAD_MCP_TAIGA_PYTHON`` and
``FREECAD_MCP_TAIGA_MODEL`` are set to an existing environment and a valid
local model directory. They never install, download, or modify anything.

The scoring test additionally requires a caller-supplied state, goal, and
action list, because the exact JSON schema belongs to the installed upstream
version and must not be guessed here:

* ``FREECAD_MCP_TAIGA_TEST_STATE``: JSON object accepted by ``State.from_json``
* ``FREECAD_MCP_TAIGA_TEST_GOAL``: JSON object accepted by ``Goal.from_json``
* ``FREECAD_MCP_TAIGA_TEST_ACTIONS``: JSON list of distinct action ids
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from freecad_mcp.integrations.taiga import TaigaBackend

_PYTHON = os.environ.get("FREECAD_MCP_TAIGA_PYTHON", "").strip()
_MODEL = os.environ.get("FREECAD_MCP_TAIGA_MODEL", "").strip()

pytestmark = pytest.mark.skipif(
    not (_PYTHON and _MODEL and Path(_PYTHON).is_file() and Path(_MODEL).is_dir()),
    reason=(
        "real Taiga-S1 integration test requires FREECAD_MCP_TAIGA_PYTHON "
        "and FREECAD_MCP_TAIGA_MODEL to point at an installed runtime and "
        "local model directory"
    ),
)


def _backend() -> TaigaBackend:
    return TaigaBackend(
        python=_PYTHON,
        model=_MODEL,
        device=os.environ.get("FREECAD_MCP_TAIGA_DEVICE", "cpu"),
    )


def test_real_runtime_status_is_available() -> None:
    report = _backend().status_report()

    assert report.status.is_available is True, report.status.detail
    assert report.model_available is True, report.model_error
    assert report.python_version


@pytest.mark.skipif(
    not (
        os.environ.get("FREECAD_MCP_TAIGA_TEST_STATE")
        and os.environ.get("FREECAD_MCP_TAIGA_TEST_GOAL")
        and os.environ.get("FREECAD_MCP_TAIGA_TEST_ACTIONS")
    ),
    reason=(
        "set FREECAD_MCP_TAIGA_TEST_STATE, FREECAD_MCP_TAIGA_TEST_GOAL, and "
        "FREECAD_MCP_TAIGA_TEST_ACTIONS to exercise real scoring"
    ),
)
def test_real_runtime_scores_requested_actions() -> None:
    state = json.loads(os.environ["FREECAD_MCP_TAIGA_TEST_STATE"])
    goal = json.loads(os.environ["FREECAD_MCP_TAIGA_TEST_GOAL"])
    actions = json.loads(os.environ["FREECAD_MCP_TAIGA_TEST_ACTIONS"])

    result = _backend().score_actions(state, goal, actions)

    assert result.scored is True, result.detail
    assert {score.action for score in result.scores} == set(actions)
    assert all(0.0 <= score.probability <= 1.0 for score in result.scores)
