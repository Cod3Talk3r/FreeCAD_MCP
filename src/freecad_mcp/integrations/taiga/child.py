"""Parent-side Taiga child runner.

Launches an isolated FreeCAD child process with :mod:`freecad_bridge.taiga_runner`
and exchanges a single JSON request/response envelope per build. This is:

* **local only** - a child process on this machine; no sockets, no network;
* **explicit** - only the ``build`` operation can run;
* **bounded** - every build has a timeout, and the child is killed on expiry;
* **environment-variable safe** - request/response paths are passed as
  command-line arguments (``--request``/``--response``), so the build does not
  depend on custom environment variables reaching the FreeCAD Python process
  (which ``freecadcmd.exe`` does not reliably preserve).

The parent process never imports ``FreeCAD`` or ``freecad_s1``. Those are
imported only inside the child interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from freecad_mcp.pathing import for_target, is_windows_path

#: Trim captured streams before embedding them in error messages.
_STREAM_TAIL = 4000


def _tail(text: str | None) -> str:
    if not text:
        return ""
    return text[-_STREAM_TAIL:]


def _child_diagnostic(completed: Any) -> str:
    """Build a diagnostic message from a completed child process run.

    Works for both :class:`subprocess.CompletedProcess` and
    :class:`subprocess.TimeoutExpired`, which do not share the same attributes.
    """
    parts = []
    rc = getattr(completed, "returncode", None)
    if rc is not None:
        parts.append(f"exit code {rc}")
    else:
        parts.append("timeout")
    out = _tail(getattr(completed, "stdout", None))
    err = _tail(getattr(completed, "stderr", None))
    if out:
        parts.append(f"stdout: {out}")
    if err:
        parts.append(f"stderr: {err}")
    return " | ".join(parts)


__all__ = ["TaigaChildRunner"]


class TaigaChildRunner:
    """Run the Taiga build in an isolated FreeCAD child process.

    Args:
        freecad_executable: Path to the FreeCAD console executable
            (``freecadcmd`` / ``FreeCADCmd``).
        taiga_runner_path: Optional override for the shipped
            ``freecad_bridge.taiga_runner`` script.
        timeout: Default per-build timeout in seconds.
    """

    def __init__(
        self,
        freecad_executable: str | Path,
        *,
        taiga_runner_path: str | Path | None = None,
        taiga_source: str | Path | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.freecad_executable = Path(freecad_executable)
        self.taiga_runner_path = (
            Path(taiga_runner_path)
            if taiga_runner_path is not None
            else Path(__file__).parent.parent.parent / "freecad_bridge" / "taiga_runner.py"
        )
        self._taiga_source = str(taiga_source).strip() if taiga_source else None
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("Taiga child runner timeout must be positive.")

        if not self.freecad_executable.is_file():
            raise FileNotFoundError(f"FreeCAD executable not found: {self.freecad_executable}")
        if not self.taiga_runner_path.is_file():
            raise FileNotFoundError(f"Taiga runner script missing: {self.taiga_runner_path}")

    def run(self, payload: Mapping[str, Any], timeout: float | None = None) -> Mapping[str, Any]:
        """Execute the build payload in FreeCAD and return the response mapping.

        The returned mapping is the raw child response envelope
        (``{"ok": bool, ...}``).  Failures to start/communicate with the child
        are raised so the caller can classify them.

        Raises:
            TimeoutError: If FreeCAD does not finish within the timeout.
            FileNotFoundError: If the child produces no response.
            ValueError: If the response is not valid JSON.
        """
        effective_timeout = self.timeout if timeout is None else float(timeout)
        if effective_timeout <= 0:
            raise ValueError("build timeout must be positive.")

        params = dict(payload.get("params") or {})
        # The child needs the Taiga source root to import freecad_s1. Pass it
        # through the request payload (argv carries the file paths; env vars
        # are not relied upon, since freecadcmd.exe strips them).
        if self._taiga_source and "taiga_source" not in params:
            params["taiga_source"] = self._taiga_source
        payload = {"op": payload.get("op", "build"), "params": params}

        # Determine whether the request/response file paths must be translated
        # for the target process (e.g. a Windows freecadcmd.exe launched from
        # WSL2 cannot reach Linux temp paths).
        windows_target = is_windows_path(str(self.freecad_executable)) and os.name != "nt"

        with tempfile.TemporaryDirectory(prefix="freecad-mcp-taiga-") as workdir:
            work = Path(workdir)
            request_path = work / "request.json"
            response_path = work / "response.json"
            request_path.write_text(json.dumps(payload), encoding="utf-8")

            req_arg = for_target(request_path, "windows" if windows_target else "native")
            resp_arg = for_target(response_path, "windows" if windows_target else "native")

            # FreeCAD 1.1.4's freecadcmd.exe does not accept arbitrary
            # --request/--response options; --response is intercepted as its
            # internal --response-file option.  Use -c to execute the runner
            # script and --pass to deliver request/response paths as positional
            # arguments, which FreeCAD does not interpret as options.
            taiga_runner_path_str = str(self.taiga_runner_path)
            runner_code = (
                "import runpy; runpy.run_path(" + repr(taiga_runner_path_str) + ", run_name='__main__')"
            )

            env = dict(os.environ)

            try:
                completed = subprocess.run(  # noqa: S603 - explicit argv, no shell
                    [
                        str(self.freecad_executable),
                        "-c",
                        runner_code,
                        "--pass",
                        req_arg,
                        resp_arg,
                    ],
                    env=env,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=effective_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # Surface as a built-in timeout so callers can classify it.
                raise TimeoutError(
                    f"FreeCAD did not finish the build within {effective_timeout:.0f}s. "
                    f"{_child_diagnostic(exc)}"
                ) from exc

            if not response_path.is_file():
                raise FileNotFoundError(
                    f"FreeCAD produced no build response ({_child_diagnostic(completed)})."
                )

            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"Could not parse FreeCAD build response ({_child_diagnostic(completed)}): {exc}"
                ) from exc

        if not isinstance(response, Mapping):
            raise ValueError(f"FreeCAD build response is not a mapping: {response!r}")
        return response
