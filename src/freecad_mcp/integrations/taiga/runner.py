"""Standalone Taiga-S1 JSON runner.

This script is executed in the explicitly configured Taiga Python interpreter,
not in the MCP server process. It reads one JSON request from a file, performs
one operation, and writes one JSON response to another file. ``freecad_s1`` and
``torch`` are imported lazily, only inside the child process.

The runner is deliberately independent of the MCP SDK, FreeCAD, and this
project's package so it can run in an isolated environment that only contains
Taiga-S1 and its dependencies.

Operations:

``status``
    Report whether the Taiga runtime is importable and which local model, if
    any, is configured. This never loads weights or contacts the network.

``score_actions``
    Score a non-empty list of candidate action ids for a validated state and
    goal using the verified ``Policy.score`` API. The model is always loaded
    from a validated local directory; a Hugging Face repository id is never
    used and nothing is downloaded.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REQUEST_ENV = "FREECAD_MCP_TAIGA_REQUEST"
RESPONSE_ENV = "FREECAD_MCP_TAIGA_RESPONSE"

DEFAULT_DEVICE = "cpu"

#: Files read by the verified upstream ``from_pretrained`` implementation.
_CONFIG_FILE = "config.json"
_WEIGHTS_FILE = "model.safetensors"

_TAIGA_MODULES = (
    "freecad_s1",
    "freecad_s1.model.net",
    "freecad_s1.rollout",
    "freecad_s1.schema",
)


class RunnerError(Exception):
    """An error that should be returned to the parent as a structured failure."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _validate_actions(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise RunnerError("InvalidRequest", "actions must be a non-empty list of action ids.")

    actions: list[str] = []
    for action in value:
        if not isinstance(action, str) or not action.strip():
            raise RunnerError("InvalidRequest", "every action id must be a non-empty string.")
        actions.append(action)

    if not actions:
        raise RunnerError("InvalidRequest", "actions must be a non-empty list of action ids.")
    if len(set(actions)) != len(actions):
        raise RunnerError("InvalidRequest", "action ids must be distinct.")
    return actions


def _validate_json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RunnerError("InvalidRequest", f"{name} must be a JSON object.")
    return dict(value)


def _validate_model_directory(model: Any) -> Path:
    """Validate a local Taiga model directory without loading it.

    The upstream ``from_pretrained`` reads ``config.json`` and
    ``model.safetensors`` from the directory. Only a directory is accepted, so
    a Hugging Face repository id can never be used here.
    """
    if not isinstance(model, str) or not model.strip():
        raise RunnerError(
            "InvalidRequest",
            "A local model directory is required; remote model ids are not accepted.",
        )

    path = Path(model).expanduser()
    if not path.is_dir():
        raise RunnerError("InvalidRequest", f"Model directory does not exist: {path}")

    config_path = path / _CONFIG_FILE
    if not config_path.is_file():
        raise RunnerError("InvalidRequest", f"Model directory is missing {_CONFIG_FILE}: {path}")

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunnerError("InvalidRequest", f"Could not read model {_CONFIG_FILE}: {exc}") from exc

    if not isinstance(payload, Mapping) or not isinstance(payload.get("config"), Mapping):
        raise RunnerError(
            "InvalidRequest",
            f"Model {_CONFIG_FILE} must contain a 'config' object.",
        )

    if not (path / _WEIGHTS_FILE).is_file():
        raise RunnerError("InvalidRequest", f"Model directory is missing {_WEIGHTS_FILE}: {path}")

    return path


def _status(params: Mapping[str, Any]) -> dict[str, Any]:
    model = params.get("model")
    model_text = _optional_text(model)

    result: dict[str, Any] = {
        "available": False,
        "reason": None,
        "detail": "",
        "version": _distribution_version("taiga-s1"),
        "python_version": platform.python_version(),
        "torch_version": _distribution_version("torch"),
        "model": model_text,
        "model_available": None,
        "model_error": None,
    }

    if model_text is not None:
        try:
            _validate_model_directory(model_text)
        except RunnerError as exc:
            result["model_available"] = False
            result["model_error"] = str(exc)
        else:
            result["model_available"] = True

    if not _module_available("freecad_s1"):
        result["reason"] = "not_installed"
        result["detail"] = (
            "The 'freecad_s1' package is not importable in the configured Taiga "
            "interpreter. Install Taiga-S1 in that environment; it is not a "
            "dependency of this project."
        )
        return result

    missing_modules = [name for name in _TAIGA_MODULES if not _module_available(name)]
    if missing_modules:
        result["reason"] = "incompatible"
        result["detail"] = (
            "The installed 'freecad_s1' package is missing the verified API "
            f"modules: {', '.join(missing_modules)}."
        )
        return result

    missing_dependencies = [
        name for name in ("torch", "safetensors") if not _module_available(name)
    ]
    if missing_dependencies:
        result["reason"] = "missing_dependency"
        result["detail"] = (
            "The Taiga runtime is missing required Python packages: "
            f"{', '.join(missing_dependencies)}."
        )
        return result

    result["available"] = True
    return result


def _import_taiga() -> tuple[Any, Any, Any, Any]:
    try:
        from freecad_s1.model.net import from_pretrained
        from freecad_s1.rollout import Policy
        from freecad_s1.schema import Goal, State
    except ImportError as exc:
        missing = getattr(exc, "name", "") or ""
        if missing == "freecad_s1" or missing.startswith("freecad_s1."):
            raise RunnerError(
                "NotInstalled",
                f"The 'freecad_s1' package is not importable: {exc}",
            ) from exc
        raise RunnerError(
            "MissingDependency",
            f"A required Taiga dependency is not importable: {exc}",
        ) from exc
    return from_pretrained, Policy, Goal, State


def _score_actions(params: Mapping[str, Any]) -> dict[str, Any]:
    state = _validate_json_object(params.get("state"), "state")
    goal = _validate_json_object(params.get("goal"), "goal")
    actions = _validate_actions(params.get("actions"))
    model_path = _validate_model_directory(params.get("model"))

    device = _optional_text(params.get("device")) or DEFAULT_DEVICE

    from_pretrained, policy_cls, goal_cls, state_cls = _import_taiga()

    try:
        parsed_state = state_cls.from_json(state)
        parsed_goal = goal_cls.from_json(goal)
    except (KeyError, TypeError, ValueError) as exc:
        raise RunnerError("InvalidRequest", f"Invalid state or goal: {exc}") from exc

    try:
        model = from_pretrained(str(model_path), device=device)
        policy = policy_cls(model, device=device)
        raw_scores = policy.score(parsed_state, parsed_goal, actions)
    except Exception as exc:  # noqa: BLE001 - report a structured failure
        raise RunnerError(
            "RuntimeError",
            f"Taiga-S1 scoring failed ({type(exc).__name__}): {exc}",
        ) from exc

    if not isinstance(raw_scores, Mapping):
        raise RunnerError("Incompatible", "Taiga-S1 returned a non-mapping score result.")

    expected = set(actions)
    if set(raw_scores) != expected:
        missing = sorted(expected - set(raw_scores))
        extra = sorted(set(raw_scores) - expected)
        raise RunnerError(
            "Incompatible",
            "Taiga-S1 returned scores that do not match the requested actions "
            f"(missing={missing}, extra={extra}).",
        )

    scores: list[dict[str, Any]] = []
    for action, probability in raw_scores.items():
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise RunnerError(
                "Incompatible",
                f"Taiga-S1 returned a non-numeric probability for {action!r}.",
            )
        value = float(probability)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise RunnerError(
                "Incompatible",
                f"Taiga-S1 returned an invalid probability for {action!r}: {value!r}",
            )
        scores.append({"action": action, "probability": value})

    return {"scores": scores, "device": device}


_HANDLERS = {
    "status": _status,
    "score_actions": _score_actions,
}


def handle_request(request: Any) -> dict[str, Any]:
    """Dispatch one decoded request and return an envelope (without writing it)."""
    if not isinstance(request, Mapping):
        return {
            "ok": False,
            "op": None,
            "error": {"type": "InvalidRequest", "message": "Request must be a JSON object."},
        }

    op = request.get("op")
    params = request.get("params") or {}
    if not isinstance(op, str) or op not in _HANDLERS:
        return {
            "ok": False,
            "op": op if isinstance(op, str) else None,
            "error": {"type": "InvalidRequest", "message": f"Unknown operation: {op!r}"},
        }
    if not isinstance(params, Mapping):
        return {
            "ok": False,
            "op": op,
            "error": {"type": "InvalidRequest", "message": "params must be a JSON object."},
        }

    try:
        result = _HANDLERS[op](params)
    except RunnerError as exc:
        return {
            "ok": False,
            "op": op,
            "error": {"type": exc.error_type, "message": str(exc)},
        }
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to the parent
        return {
            "ok": False,
            "op": op,
            "error": {
                "type": "RuntimeError",
                "message": f"Unexpected runner failure ({type(exc).__name__}): {exc}",
            },
        }

    return {"ok": True, "op": op, "result": result}


def main() -> int:
    """Read one request file, write one response file, and return an exit code."""
    request_path = os.environ.get(REQUEST_ENV)
    response_path = os.environ.get(RESPONSE_ENV)

    if not request_path or not response_path:
        print(
            f"Taiga runner requires {REQUEST_ENV} and {RESPONSE_ENV}.",
            file=sys.stderr,
        )
        return 2

    # Keep any library output off the protocol channel; responses go to a file.
    sys.stdout = sys.stderr

    try:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        envelope: dict[str, Any] = {
            "ok": False,
            "op": None,
            "error": {"type": "InvalidRequest", "message": f"Could not read request: {exc}"},
        }
    else:
        envelope = handle_request(request)

    try:
        Path(response_path).write_text(
            json.dumps(envelope, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"Could not write Taiga response: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the runtime
    raise SystemExit(main())
