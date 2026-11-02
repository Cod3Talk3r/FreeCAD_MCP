"""Tests for the parent-side Taiga runtime subprocess client."""

from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from freecad_mcp.integrations.base import UnavailableReason
from freecad_mcp.integrations.taiga.runtime import (
    TaigaRuntime,
    TaigaRuntimeExecutionError,
    TaigaRuntimeNotConfiguredError,
    TaigaRuntimeNotFoundError,
    TaigaRuntimeOperationError,
    TaigaRuntimeRequestError,
    TaigaRuntimeResponseError,
    TaigaRuntimeTimeoutError,
    unavailable_reason_for,
)


def _runner(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "fake_runner.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _runtime(tmp_path: Path, source: str, *, timeout: float = 30.0) -> TaigaRuntime:
    return TaigaRuntime(sys.executable, runner_path=_runner(tmp_path, source), timeout=timeout)


def test_success_round_trip(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        """
        import json, os
        from pathlib import Path
        request = json.loads(Path(os.environ["FREECAD_MCP_TAIGA_REQUEST"]).read_text(encoding="utf-8"))
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text(
            json.dumps({"ok": True, "op": request["op"], "result": {"echo": request["params"]}}),
            encoding="utf-8",
        )
        """,
    )

    assert runtime.run("score_actions", {"value": 7}) == {"echo": {"value": 7}}


def test_status_convenience_forwards_model(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        """
        import json, os
        from pathlib import Path
        request = json.loads(Path(os.environ["FREECAD_MCP_TAIGA_REQUEST"]).read_text(encoding="utf-8"))
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text(
            json.dumps({"ok": True, "op": request["op"], "result": request["params"]}),
            encoding="utf-8",
        )
        """,
    )

    assert runtime.status(model="C:/models/taiga") == {"model": "C:/models/taiga"}
    assert runtime.status() == {}


def test_non_mapping_result_is_wrapped(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        """
        import json, os
        from pathlib import Path
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text(
            json.dumps({"ok": True, "op": "ping", "result": 42}),
            encoding="utf-8",
        )
        """,
    )

    assert runtime.run("ping") == {"value": 42}


def test_missing_interpreter_is_reported(tmp_path: Path) -> None:
    with pytest.raises(TaigaRuntimeNotFoundError, match="interpreter"):
        TaigaRuntime(tmp_path / "missing-python.exe")


def test_missing_runner_is_reported(tmp_path: Path) -> None:
    with pytest.raises(TaigaRuntimeNotFoundError, match="runner"):
        TaigaRuntime(sys.executable, runner_path=tmp_path / "missing-runner.py")


def test_unconfigured_interpreter_is_reported(tmp_path: Path) -> None:
    runtime = TaigaRuntime(None)
    with pytest.raises(TaigaRuntimeNotConfiguredError, match="FREECAD_MCP_TAIGA_PYTHON"):
        runtime.run("status")


def test_timeout_is_reported(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "import time; time.sleep(5)", timeout=0.2)
    started = time.monotonic()

    with pytest.raises(TaigaRuntimeTimeoutError) as excinfo:
        runtime.run("score_actions")

    assert excinfo.value.timeout == 0.2
    assert time.monotonic() - started < 4.0
    assert unavailable_reason_for(excinfo.value) is UnavailableReason.TIMEOUT


def test_nonzero_exit_without_response_is_reported(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "raise SystemExit(3)")

    with pytest.raises(TaigaRuntimeExecutionError) as excinfo:
        runtime.run("status")

    assert excinfo.value.returncode == 3
    assert unavailable_reason_for(excinfo.value) is UnavailableReason.RUNTIME_ERROR


def test_missing_response_file_is_reported(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "pass")

    with pytest.raises(TaigaRuntimeExecutionError, match="no response"):
        runtime.run("status")


def test_invalid_json_response_is_reported(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        """
        import os
        from pathlib import Path
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text("{not json", encoding="utf-8")
        """,
    )

    with pytest.raises(TaigaRuntimeResponseError, match="parse"):
        runtime.run("status")


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"ok": "yes"}',
        '{"ok": true}',
    ],
)
def test_malformed_envelope_is_reported(tmp_path: Path, payload: str) -> None:
    runtime = _runtime(
        tmp_path,
        f"""
        import os
        from pathlib import Path
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text({payload!r}, encoding="utf-8")
        """,
    )

    with pytest.raises(TaigaRuntimeResponseError):
        runtime.run("status")


def test_mismatched_response_op_is_reported(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        """
        import json, os
        from pathlib import Path
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text(
            json.dumps({"ok": True, "op": "score_actions", "result": {}}),
            encoding="utf-8",
        )
        """,
    )

    with pytest.raises(TaigaRuntimeResponseError, match="does not match"):
        runtime.run("status")


@pytest.mark.parametrize(
    ("child_type", "expected"),
    [
        ("NotInstalled", UnavailableReason.NOT_INSTALLED),
        ("MissingDependency", UnavailableReason.MISSING_DEPENDENCY),
        ("Incompatible", UnavailableReason.INCOMPATIBLE),
        ("InvalidRequest", UnavailableReason.INVALID_REQUEST),
        ("Timeout", UnavailableReason.TIMEOUT),
        ("RuntimeError", UnavailableReason.RUNTIME_ERROR),
    ],
)
def test_structured_child_errors_are_mapped(
    tmp_path: Path,
    child_type: str,
    expected: UnavailableReason,
) -> None:
    runtime = _runtime(
        tmp_path,
        f"""
        import json, os
        from pathlib import Path
        Path(os.environ["FREECAD_MCP_TAIGA_RESPONSE"]).write_text(
            json.dumps({{"ok": False, "op": "status", "error": {{"type": {child_type!r}, "message": "child failed"}}}}),
            encoding="utf-8",
        )
        """,
    )

    with pytest.raises(TaigaRuntimeOperationError) as excinfo:
        runtime.run("status")

    assert excinfo.value.error_type == child_type
    assert "child failed" in str(excinfo.value)
    assert unavailable_reason_for(excinfo.value) is expected


def test_non_json_request_is_rejected(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "pass")

    with pytest.raises(TaigaRuntimeRequestError):
        runtime.run("score_actions", {"bad": object()})

    assert unavailable_reason_for(TaigaRuntimeRequestError("bad")) is UnavailableReason.INVALID_REQUEST


def test_non_positive_timeout_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        TaigaRuntime(sys.executable, timeout=0)
