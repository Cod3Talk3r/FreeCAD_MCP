"""FreeCAD-side Taiga build runner.

This module executes **inside FreeCAD's own Python interpreter**
(``freecadcmd taiga_runner.py``). It is never imported by the MCP server
process.

It reads a single build request (JSON) from the file named by ``--request``
(or the ``FREECAD_MCP_TAIGA_REQUEST`` environment variable for backward
compatibility), runs the Taiga feasibility + Expert execution lifecycle
against a real FreeCAD document using the pinned upstream ``freecad_s1``
implementation, saves the resulting document atomically, performs in-child
post-save validation, and writes a structured response envelope to the file
named by ``--response``.

The parent process never imports ``FreeCAD`` or ``freecad_s1``. Those are
imported lazily, only inside this child process.

Keeping the build logic here - explicit and fixed - is what prevents the
bridge from becoming a general "run this code inside FreeCAD" interface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


# When this module is executed as a script inside FreeCAD, ``__package__`` is
# None, which breaks imports.  Set the package and ensure the package root is
# on sys.path so ``freecad_mcp.freecad_bridge.workspace`` is importable.
_PACKAGE_ROOT = Path(__file__).parent.parent.parent
if __package__ is None or __package__ == "":
    __package__ = "freecad_mcp.freecad_bridge"
    _pkg_dir = str(_PACKAGE_ROOT)
    if _pkg_dir not in sys.path:
        sys.path.insert(0, _pkg_dir)

OP_BUILD = "build"

REQUEST_ENV = "FREECAD_MCP_TAIGA_REQUEST"
RESPONSE_ENV = "FREECAD_MCP_TAIGA_RESPONSE"

#: Exit code the runner returns when it could not even load/write its envelope.
EXIT_ENVELOPE_ERROR = 2

#: Taiga success threshold (volumetric IoU against the detached target).
SUCCESS_IOU = 0.99


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------

def _error_envelope(op: str | None, error_type: str, message: str) -> dict[str, Any]:
    return {"ok": False, "op": op, "error": {"type": error_type, "message": message}}


def _canonical_envelope(op: str, handler_result: Any) -> dict[str, Any]:
    if isinstance(handler_result, dict) and "ok" in handler_result:
        if not isinstance(handler_result["ok"], bool):
            return _error_envelope(op, "ResponseError", "Handler returned a non-boolean 'ok'.")
        envelope = dict(handler_result)
        envelope.setdefault("op", op)
        if envelope["ok"]:
            if "result" not in envelope:
                return _error_envelope(
                    op, "ResponseError", "Handler returned a success envelope without 'result'."
                )
        else:
            error = envelope.get("error")
            if not isinstance(error, dict):
                envelope["error"] = {
                    "type": "OperationError",
                    "message": str(error) if error else "FreeCAD operation failed",
                }
        return envelope
    return {"ok": True, "op": op, "result": handler_result}


def handle_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch a build request. Never raises; failures become error envelopes."""
    op = request.get("op")
    params = request.get("params") or {}
    if not isinstance(op, str):
        return _error_envelope(None, "RequestError", "Missing or non-string 'op'.")
    if not isinstance(params, dict):
        return _error_envelope(op, "RequestError", "'params' must be an object.")

    handler = _OPERATIONS.get(op)
    if handler is None:
        known = ", ".join(sorted(_OPERATIONS))
        return _error_envelope(op, "UnknownOperationError", f"Unknown operation {op!r}. Known: {known}.")

    try:
        handler_result = handler(params)
    except Exception as exc:  # noqa: BLE001 - report any handler failure to the parent
        return _error_envelope(op, type(exc).__name__, str(exc))
    return _canonical_envelope(op, handler_result)


# ---------------------------------------------------------------------------
# Build operation
# ---------------------------------------------------------------------------

def _insert_taiga_source(taiga_source: Any) -> None:
    """Add the Taiga source repository root to sys.path so ``freecad_s1`` is importable."""
    if not taiga_source or not isinstance(taiga_source, str):
        return
    text = taiga_source.strip()
    if not text:
        return
    path = os.path.abspath(text)
    if path not in sys.path:
        sys.path.insert(0, path)


def _document_id(params: dict[str, Any], goal_payload: Any) -> str:
    """Resolve a validated document id, generating one deterministically from the goal."""
    document_id = params.get("document_id") or ""
    if not document_id or not str(document_id).strip():
        digest = hashlib.sha256(
            json.dumps(goal_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        document_id = f"taiga_{digest[:16]}"
    return str(document_id)


def _op_build(params: dict[str, Any]) -> dict[str, Any]:
    """Run feasibility + Expert execution, save, validate, and report."""
    goal_payload = params.get("goal")
    taiga_source = params.get("taiga_source")
    workspace_str = params.get("workspace") or ""

    _insert_taiga_source(taiga_source)

    # Authoritative Taiga schema validation happens inside the child.
    from freecad_s1.schema import Goal  # noqa: PLC0415 - child only

    try:
        goal = Goal.from_json(goal_payload)
    except Exception as exc:
        return _error_envelope(
            OP_BUILD, "InvalidGoal", f"Goal is not valid per upstream schema: {exc}"
        )

    from freecad_mcp.freecad_bridge.workspace import (  # noqa: PLC0415 - child only
        Workspace,
        atomic_save,
        release_lock,
        read_meta,
        try_acquire_lock,
        validate_document_id,
        write_meta,
    )

    try:
        normalized = validate_document_id(_document_id(params, goal_payload))
    except ValueError as exc:
        return _error_envelope(OP_BUILD, "InvalidRequest", str(exc))

    try:
        wks = (
            Workspace.from_env() if not workspace_str else Workspace(root=os.path.normpath(workspace_str))
        )
    except Exception as exc:
        return _error_envelope(OP_BUILD, "WorkspaceError", f"workspace error: {exc}")

    pid = os.getpid()
    lock_path = wks.lock_path(normalized)
    lock_status = try_acquire_lock(lock_path, pid=pid)
    if lock_status.get("status") not in ("acquired", "reacquired"):
        return _error_envelope(
            OP_BUILD, "Locked", lock_status.get("error", "document locked by alive process")
        )

    #: Results accumulated during the build.  Populated step by step so the
    #: finally-block can still report partial progress on failure.
    result: dict[str, Any] = {
        "document_id": normalized,
        "saved_path": str(wks.document_path(normalized)),
        "artifact_written": False,
        "metadata_written": False,
        "iou": 0.0,
        "match": False,
        "steps": 0,
        "budget": 0,
        "done": False,
        "feasible": False,
        "validation_ok": None,
        "validation_errors": [],
    }

    try:
        # ------------------------------------------------------------------
        # Feasibility session (separate document/session from execution).
        # ------------------------------------------------------------------
        from freecad_s1.goals import StartSpec  # noqa: PLC0415 - child only
        from freecad_s1.runtime.episode import GoalBuildError, build_target  # noqa: PLC0415 - child only
        from freecad_s1.runtime.session import HeadlessSession  # noqa: PLC0415 - child only

        feas_session = HeadlessSession()
        feasible = False
        try:
            target_solid = build_target(feas_session, goal)
            result["feasible"] = True
            feasible = True
        except GoalBuildError as exc:
            return _error_envelope(OP_BUILD, "FeasibilityError", str(exc))
        finally:
            feas_session.close()

        # ------------------------------------------------------------------
        # Execution session (separate from feasibility).
        # ------------------------------------------------------------------
        from freecad_s1.expert import expert_plan_length  # noqa: PLC0415 - child only
        from freecad_s1.runtime.session import iou  # noqa: PLC0415 - child only

        exec_session = HeadlessSession()
        try:
            start = StartSpec(doc_open=True, workbench="PartDesignWorkbench")
            exec_session.reset(goal, start)
            plan_length = expert_plan_length(goal, True, "PartDesignWorkbench", False)
            budget = 2 * plan_length + 6
            result["budget"] = budget

            steps = 0
            done = False
            unrecoverable = False
            while steps < budget and not done:
                acts = exec_session.expert()
                if not acts:
                    # Empty Expert result => unrecoverable failure.
                    unrecoverable = True
                    break
                act = acts[0]
                if act == "Done":
                    done = True
                    break
                info = exec_session.step(act)
                steps += 1
                result["steps"] = steps
                if info.get("error"):
                    # During execution Std_Undo is a recovery action; anything
                    # else is an execution failure.
                    if act == "Std_Undo":
                        continue
                    unrecoverable = True
                    break

            result["done"] = done

            # Score the execution solid against the detached feasibility target.
            shape = exec_session.solid()
            value = 0.0
            if shape is not None and target_solid is not None:
                try:
                    value = iou(shape, target_solid)
                except Exception:  # noqa: BLE001 - boolean failure => no overlap
                    value = 0.0
            result["iou"] = value
            match = bool(value >= SUCCESS_IOU)
            result["match"] = match

            if unrecoverable:
                return _error_envelope(
                    OP_BUILD,
                    "ExecutionError",
                    "Expert produced no acceptable action (unrecoverable state).",
                )

            if not done:
                return _error_envelope(
                    OP_BUILD,
                    "ExecutionError",
                    f"Step budget exhausted ({steps}/{budget}) without reaching Done.",
                )

            if not match:
                return _error_envelope(
                    OP_BUILD,
                    "GeometryMismatch",
                    f"Build completed but IoU {value:.4f} < {SUCCESS_IOU}; target not matched.",
                )

            # ------------------------------------------------------------------
            # Persistence: atomic FCStd save while the document is open.
            # ------------------------------------------------------------------
            fcstd_path = wks.document_path(normalized)

            def _write_tmp(tmp_path: str) -> None:
                exec_session.doc.saveAs(str(tmp_path))

            atomic_save(fcstd_path, _write_tmp)
            result["artifact_written"] = True

            # Metadata side-car (separate from the FCStd artifact).
            meta_before = read_meta(wks.meta_path(normalized))
            meta = {
                "revision": int(meta_before.get("revision") or 0) + 1,
                "created_at": meta_before.get("created_at"),
                "document_id": normalized,
                "last_saved_at": None,
            }
            try:
                write_meta(wks.meta_path(normalized), meta)
                result["metadata_written"] = True
            except Exception as exc:  # noqa: BLE001 - report, keep artifact flag
                result["metadata_written"] = False
                result["validation_errors"].append(f"metadata write failed: {exc}")

            exec_session.close()

            # ------------------------------------------------------------------
            # In-child post-save validation: reopen the saved document and
            # validate using PartDesign-aware Body.Tip.Shape inspection.
            # ------------------------------------------------------------------
            import FreeCAD as _FC  # noqa: PLC0415 - child only

            val_doc = None
            try:
                val_doc = _FC.openDocument(str(fcstd_path))
                ok, errors = _validate_saved_document(val_doc)
                result["validation_ok"] = ok
                result["validation_errors"] = errors
            except Exception as exc:  # noqa: BLE001 - report, do not hide artifact
                result["validation_ok"] = False
                result["validation_errors"] = [f"post-save validation failed: {exc}"]
            finally:
                if val_doc is not None:
                    try:
                        _FC.closeDocument(val_doc.Name)
                    except Exception:
                        pass
        finally:
            exec_session.close()

        return {
            "ok": True,
            "op": OP_BUILD,
            "result": result,
        }
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to the parent
        return _error_envelope(OP_BUILD, "ExecutionError", f"FreeCAD build error: {exc}")
    finally:
        try:
            release_lock(lock_path, pid)
        except Exception:
            pass


def _validate_saved_document(doc: Any) -> tuple[bool, list[str]]:
    """PartDesign-aware validation of a saved document.

    Inspects the Body tip shape (``Body.Tip.Shape``) rather than blindly
    validating every object: sketches are wires, origin objects may not have
    meaningful shapes, and a Body tip may be a Compound.
    """
    errors: list[str] = []
    body = None
    for obj in getattr(doc, "Objects", []) or []:
        if getattr(obj, "TypeId", "") == "PartDesign::Body":
            body = obj
            break

    if body is None:
        return True, []

    tip = getattr(body, "Tip", None)
    if tip is None:
        return True, []

    shape = getattr(tip, "Shape", None)
    if shape is None:
        return True, []

    try:
        valid = bool(shape.isValid())
    except Exception as exc:
        return False, [f"could not validate Body.Tip.Shape: {exc}"]

    if not valid:
        errors.append("Body.Tip.Shape.isValid() returned False")
        return False, errors

    try:
        solids = list(getattr(shape, "Solids", []) or [])
    except Exception:
        solids = []

    if solids:
        try:
            volume = float(shape.Volume)
        except Exception:
            volume = 0.0
        if len(solids) != 1:
            errors.append(f"Body.Tip.Shape has {len(solids)} solids, expected 1")
        if volume <= 0:
            errors.append("Body.Tip.Shape has zero or negative volume")
    return (len(errors) == 0), errors


_OPERATIONS: dict[str, Any] = {
    OP_BUILD: _op_build,
}


def main(argv: list[str] | None = None) -> int:
    """Read the request file, run the build, and write the response file.

    Paths may be supplied positionally after a ``--pass`` delimiter (used by
    FreeCAD's ``-c`` execution mode), via ``--request``/``--response`` options
    (for direct Python execution), or via the
    ``FREECAD_MCP_TAIGA_REQUEST``/``FREECAD_MCP_TAIGA_RESPONSE`` environment
    variables for backward compatibility.
    """
    if argv is None:
        argv = sys.argv[1:]

    request_path: str | None = None
    response_path: str | None = None

    # FreeCAD -c mode: request/response paths are passed positionally after
    # the --pass delimiter.  Using positional args avoids FreeCAD's own
    # --response-file option intercepting --response.
    if "--pass" in argv:
        pass_idx = argv.index("--pass")
        remaining = argv[pass_idx + 1:]
        if len(remaining) >= 2:
            request_path = remaining[0]
            response_path = remaining[1]
    else:
        # Backward compatibility: --request/--response options.
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--request", dest="request_path", default=None)
        parser.add_argument("--response", dest="response_path", default=None)
        args, _ = parser.parse_known_args(argv)
        request_path = args.request_path or os.environ.get(REQUEST_ENV)
        response_path = args.response_path or os.environ.get(RESPONSE_ENV)

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


if __name__ == "__main__":
    os.environ["FREECAD_MCP_TAIGA_RUNNER_ACTIVE"] = "1"
    main()
