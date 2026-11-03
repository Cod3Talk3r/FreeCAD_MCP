"""Focused diagnostics for the real FreeCAD child process.

These tests run the *actual* isolated FreeCAD child process
(``freecadcmd taiga_runner.py``) and surface its stdout/stderr so the cause of
a child failure can be identified. They are opt-in: they skip unless both
``FREECAD_MCP_FREECAD_EXECUTABLE`` and ``FREECAD_MCP_TAIGA_SOURCE`` are set,
and they fail (never skip) when a configured path is invalid.

FreeCAD and ``freecad_s1`` are loaded only inside the child interpreter; this
parent module never imports them.
"""

from __future__ import annotations

import os

import pytest

from freecad_mcp.integrations.taiga.build import BuildRuntime
from freecad_mcp.integrations.taiga.child import TaigaChildRunner


def _env_values() -> tuple[str | None, str | None]:
    return (
        os.environ.get("FREECAD_MCP_FREECAD_EXECUTABLE"),
        os.environ.get("FREECAD_MCP_TAIGA_SOURCE"),
    )


def _require_configured() -> tuple[str, str]:
    freecad_exe, taiga_source = _env_values()
    if not freecad_exe or not taiga_source:
        pytest.skip(
            "Set FREECAD_MCP_FREECAD_EXECUTABLE and FREECAD_MCP_TAIGA_SOURCE "
            "to run real FreeCAD child diagnostics."
        )
    if not os.path.isfile(freecad_exe):
        pytest.fail(f"Configured FreeCAD executable does not exist: {freecad_exe}")
    if not os.path.isdir(taiga_source):
        pytest.fail(f"Configured Taiga source directory does not exist: {taiga_source}")
    return freecad_exe, taiga_source


def test_diagnostic_freeCAD_child_startup_and_response(tmp_path) -> None:
    """Run a minimal real build through the child and capture stdout/stderr.

    This isolates the first failing operation in the child pipeline:
    FreeCAD startup -> script execution -> freecad_s1 import -> Goal validation
    -> response file writing.

    Any exception (including child startup failure) carries the captured
    child stdout/stderr, which pytest displays on failure.
    """
    freecad_exe, taiga_source = _require_configured()

    runner = TaigaChildRunner(freecad_exe, taiga_source=taiga_source, timeout=120.0)
    runtime = BuildRuntime(child_runner=runner.run, timeout=120.0)

    goal = {
        "features": [
            {"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}},
        ],
        "scale": 40.0,
    }
    # A successful run returns a result; any failure raises with diagnostics.
    result = runtime.run(goal, document_id="taiga_diag", workspace=str(tmp_path))
    assert result.ok is True, f"Build did not succeed: {result.error_type}: {result.error}"


def test_diagnostic_skips_when_environment_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The diagnostic opts out cleanly when env vars are unset."""
    monkeypatch.delenv("FREECAD_MCP_FREECAD_EXECUTABLE", raising=False)
    monkeypatch.delenv("FREECAD_MCP_TAIGA_SOURCE", raising=False)
    freecad_exe, taiga_source = _env_values()
    assert freecad_exe is None and taiga_source is None
    with pytest.raises(pytest.skip.Exception):
        _require_configured()
