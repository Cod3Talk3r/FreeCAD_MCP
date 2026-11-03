"""Opt-in real FreeCAD/Taiga integration tests.

These are skipped unless ``FREECAD_MCP_FREECAD_EXECUTABLE`` and
``FREECAD_MCP_TAIGA_SOURCE`` are both set.

FreeCAD and ``freecad_s1`` are intentionally not imported by this parent
module; they are loaded inside the isolated FreeCAD child process. The child
process determines whether they are importable, and import or initialization
failures surface as integration test failures with structured error
information rather than skips.
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


def _skip_unless_configured() -> None:
    freecad_exe, taiga_source = _env_values()
    if not freecad_exe or not taiga_source:
        pytest.skip(
            "Set FREECAD_MCP_FREECAD_EXECUTABLE and FREECAD_MCP_TAIGA_SOURCE "
            "to run real FreeCAD/Taiga integration tests."
        )


def test_real_taiga_build_skips_when_environment_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The integration test opts out cleanly when env vars are unset."""
    monkeypatch.delenv("FREECAD_MCP_FREECAD_EXECUTABLE", raising=False)
    monkeypatch.delenv("FREECAD_MCP_TAIGA_SOURCE", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _skip_unless_configured()


def test_real_taiga_build_end_to_end(tmp_path) -> None:
    """Build a real Taiga goal in an isolated FreeCAD child process.

    Skips only when the required environment variables are absent.  When both
    are set, invalid paths fail the test rather than skipping.  FreeCAD and
    ``freecad_s1`` are loaded inside the child; import/init failures surface
    here as failures with structured error information.
    """
    freecad_exe, taiga_source = _env_values()
    _skip_unless_configured()

    if not os.path.isfile(freecad_exe):
        pytest.fail(f"Configured FreeCAD executable does not exist: {freecad_exe}")
    if not os.path.isdir(taiga_source):
        pytest.fail(f"Configured Taiga source directory does not exist: {taiga_source}")

    runner = TaigaChildRunner(freecad_exe, taiga_source=taiga_source, timeout=120.0)
    runtime = BuildRuntime(child_runner=runner.run, timeout=120.0)

    goal = {
        "features": [
            {"kind": "base_box", "params": {"w": 40.0, "d": 30.0, "h": 8.0}},
        ],
        "scale": 40.0,
    }
    result = runtime.run(goal, document_id="taiga_it", workspace=str(tmp_path))

    assert result.ok is True, f"Build failed: {result.error_type}: {result.error}"
    assert result.status == "completed"
    assert result.result is not None, "Successful build must include a result payload"

    child_result = result.result
    assert child_result["match"] is True
    assert child_result["artifact_written"] is True
    assert child_result["document_id"] == "taiga_it"
    assert child_result["saved_path"] is not None
    assert os.path.isfile(child_result["saved_path"])
