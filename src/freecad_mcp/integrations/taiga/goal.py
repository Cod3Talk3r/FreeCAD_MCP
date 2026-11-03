"""Structural validation for Taiga Goal JSON (parent-side, pre-execution)."""

from __future__ import annotations

import math
from typing import Any


class InvalidGoalError(ValueError):
    """Raised when a Goal is structurally invalid."""


_SUPPORTED_TOP_LEVEL = {"features", "level", "scale", "target"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_feature(feature: Any, index: int) -> dict[str, Any]:
    if not isinstance(feature, dict):
        raise InvalidGoalError(f"features[{index}] must be an object.")
    if "kind" not in feature or not isinstance(feature["kind"], str) or not feature["kind"].strip():
        raise InvalidGoalError(f"features[{index}].kind must be a non-empty string.")
    if "params" in feature and feature["params"] is not None:
        if not isinstance(feature["params"], dict):
            raise InvalidGoalError(f"features[{index}].params must be an object.")
        for key, value in feature["params"].items():
            if not isinstance(key, str) or not key.strip():
                raise InvalidGoalError(f"features[{index}].params has an invalid key.")
            if not _is_number(value):
                raise InvalidGoalError(
                    f"features[{index}].params[{key!r}] must be a finite number (bool/NaN/Infinity rejected)."
                )
    else:
        feature = dict(feature)
        feature.setdefault("params", {})
    # Preserve only known keys; keep order of features.
    normalized = {"kind": feature["kind"].strip()}
    normalized["params"] = dict(feature.get("params") or {})
    return normalized


def validate_goal(goal: Any) -> dict[str, Any]:
    if not isinstance(goal, dict):
        raise InvalidGoalError("Goal must be a JSON object.")
    unknown = set(goal) - _SUPPORTED_TOP_LEVEL
    if unknown:
        raise InvalidGoalError(f"Unsupported Goal fields: {sorted(unknown)}.")
    if "features" not in goal:
        raise InvalidGoalError("Goal requires a non-empty 'features' list.")
    features = goal["features"]
    if not isinstance(features, list) or not features:
        raise InvalidGoalError("'features' must be a non-empty list.")
    validated_features = [_validate_feature(f, i) for i, f in enumerate(features)]

    result: dict[str, Any] = {"features": validated_features}

    if "level" in goal and goal["level"] is not None:
        level = goal["level"]
        if isinstance(level, bool) or not isinstance(level, int):
            raise InvalidGoalError("'level' must be an integer.")
        result["level"] = level

    if "scale" in goal and goal["scale"] is not None:
        scale = goal["scale"]
        if not _is_number(scale) or scale <= 0:
            raise InvalidGoalError("'scale' must be a positive finite number.")
        result["scale"] = scale

    if "target" in goal and goal["target"] is not None:
        target = goal["target"]
        if not isinstance(target, dict):
            raise InvalidGoalError("'target' must be an object if supplied.")
        result["target"] = dict(target)

    return result
