"""FreeCAD-side operation runner.

This module executes **inside FreeCAD's own Python interpreter**
(``freecadcmd runner.py``). It is never imported by the MCP server process.

It reads a request envelope (JSON) from the file named by ``FREECAD_MCP_REQUEST``,
dispatches the named operation to a fixed handler, and writes a response envelope
(JSON) to the file named by ``FREECAD_MCP_RESPONSE``.

Keeping the operations here - explicit and fixed - is what prevents the bridge
from becoming a general "run this code inside FreeCAD" interface.

``FreeCAD``/``Part`` are imported *lazily inside handlers* so that the envelope
and dispatch logic remain importable and unit-testable on the host interpreter.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

OP_GET_VERSION = "get_version"
OP_PING = "ping"

REQUEST_ENV = "FREECAD_MCP_REQUEST"
RESPONSE_ENV = "FREECAD_MCP_RESPONSE"

#: Exit code the runner returns when it could not even load/write its envelope.
EXIT_ENVELOPE_ERROR = 2


def _op_ping(params: dict[str, Any]) -> dict[str, Any]:
    return {"message": params.get("message", "pong")}


def _op_get_version(params: dict[str, Any]) -> dict[str, Any]:
    import FreeCAD  # noqa: PLC0415 - imported lazily; only available inside FreeCAD

    raw = [str(part) for part in FreeCAD.Version()]
    result: dict[str, Any] = {
        "version": ".".join(raw[0:3]),
        "version_raw": raw,
        "build_date": raw[5] if len(raw) > 5 else None,
        "revision": raw[7] if len(raw) > 7 else None,
        "gui_available": False,
        "python_version": sys.version.split()[0],
        "freecad_python_executable": sys.executable,
    }

    try:
        import Part  # noqa: F401, PLC0415 - probe availability only
    except Exception as exc:  # noqa: BLE001 - report any import failure verbatim
        result["part_available"] = False
        result["part_error"] = f"{type(exc).__name__}: {exc}"
    else:
        result["part_available"] = True

    return result


#: The complete, explicit set of operations the bridge can perform.
OPERATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    OP_PING: _op_ping,
    OP_GET_VERSION: _op_get_version,
}


def handle_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch a request envelope. Never raises; failures become error envelopes."""
    op = request.get("op")
    params = request.get("params") or {}
    if not isinstance(op, str):
        return _error_envelope(None, "RequestError", "Missing or non-string 'op'.")
    if not isinstance(params, dict):
        return _error_envelope(op, "RequestError", "'params' must be an object.")

    handler = OPERATIONS.get(op)
    if handler is None:
        known = ", ".join(sorted(OPERATIONS))
        return _error_envelope(op, "UnknownOperationError", f"Unknown operation {op!r}. Known: {known}.")

    try:
        return {"ok": True, "op": op, "result": handler(params)}
    except Exception as exc:  # noqa: BLE001 - report any handler failure to the parent
        return _error_envelope(op, type(exc).__name__, str(exc))


def _error_envelope(op: str | None, error_type: str, message: str) -> dict[str, Any]:
    return {"ok": False, "op": op, "error": {"type": error_type, "message": message}}


def main(argv: list[str] | None = None) -> int:
    """Read the request file, dispatch, and write the response file."""
    del argv  # the runner takes no arguments; paths come from the environment
    request_path = os.environ.get(REQUEST_ENV)
    response_path = os.environ.get(RESPONSE_ENV)
    if not request_path or not response_path:
        print(f"{REQUEST_ENV} and {RESPONSE_ENV} must be set.", file=sys.stderr)
        return EXIT_ENVELOPE_ERROR

    try:
        with open(request_path, encoding="utf-8") as handle:
            request = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"Failed to read request: {exc}", file=sys.stderr)
        return EXIT_ENVELOPE_ERROR

    response = handle_request(request if isinstance(request, dict) else {})

    try:
        with open(response_path, "w", encoding="utf-8") as handle:
            json.dump(response, handle)
    except OSError as exc:
        print(f"Failed to write response: {exc}", file=sys.stderr)
        return EXIT_ENVELOPE_ERROR

    return 0


if __name__ == "__main__" or REQUEST_ENV in os.environ:
    # FreeCAD runs a script file with ``__name__`` set to the module name (not
    # "__main__"), so the environment guard is what makes the runner fire when
    # launched as ``freecadcmd runner.py``. No ``sys.exit`` here: FreeCAD owns
    # process teardown, and the parent relies on the response file, not the code.
    main()
