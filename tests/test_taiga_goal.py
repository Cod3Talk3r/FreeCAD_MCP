"""Tests for the parent-side Taiga Goal structural validator."""

from __future__ import annotations

import math

import pytest

from freecad_mcp.integrations.taiga.goal import InvalidGoalError, validate_goal


def _base_goal() -> dict:
    return {"features": [{"kind": "base_box", "params": {"w": 40, "d": 30, "h": 8}}]}


def test_valid_goal_passes_through() -> None:
    result = validate_goal(_base_goal())
    assert result == {
        "features": [{"kind": "base_box", "params": {"w": 40, "d": 30, "h": 8}}]
    }


def test_valid_goal_with_all_optional_fields() -> None:
    goal = {
        "features": [{"kind": "hole", "params": {"r": 3, "x": 10, "y": 0}}],
        "level": 2,
        "scale": 40.0,
        "target": {"bbox": [0, 0, 0, 10, 10, 10]},
    }
    result = validate_goal(goal)
    assert result["level"] == 2
    assert result["scale"] == 40.0
    assert result["target"] == {"bbox": [0, 0, 0, 10, 10, 10]}


def test_missing_features_raises() -> None:
    with pytest.raises(InvalidGoalError, match="non-empty 'features'"):
        validate_goal({})


def test_empty_features_raises() -> None:
    with pytest.raises(InvalidGoalError, match="non-empty"):
        validate_goal({"features": []})


def test_features_not_a_list_raises() -> None:
    with pytest.raises(InvalidGoalError, match="non-empty"):
        validate_goal({"features": "base_box"})


def test_feature_must_be_an_object() -> None:
    with pytest.raises(InvalidGoalError, match=r"features\[0\] must be an object"):
        validate_goal({"features": ["base_box"]})


def test_feature_kind_required_and_non_empty() -> None:
    with pytest.raises(InvalidGoalError, match="kind must be a non-empty string"):
        validate_goal({"features": [{"params": {}}]})
    with pytest.raises(InvalidGoalError, match="kind must be a non-empty string"):
        validate_goal({"features": [{"kind": "   "}]})


def test_params_must_be_an_object() -> None:
    with pytest.raises(InvalidGoalError, match="params must be an object"):
        validate_goal({"features": [{"kind": "base_box", "params": 5}]})


def test_params_key_must_be_non_empty_string() -> None:
    with pytest.raises(InvalidGoalError, match="invalid key"):
        validate_goal({"features": [{"kind": "base_box", "params": {"": 1}}]})


def test_boolean_param_is_rejected() -> None:
    with pytest.raises(InvalidGoalError, match="finite number"):
        validate_goal({"features": [{"kind": "base_box", "params": {"w": True}}]})


def test_nan_param_is_rejected() -> None:
    with pytest.raises(InvalidGoalError, match="finite number"):
        validate_goal({"features": [{"kind": "base_box", "params": {"w": float("nan")}}]})


def test_infinity_param_is_rejected() -> None:
    with pytest.raises(InvalidGoalError, match="finite number"):
        validate_goal({"features": [{"kind": "base_box", "params": {"w": float("inf")}}]})


def test_negative_infinity_param_is_rejected() -> None:
    with pytest.raises(InvalidGoalError, match="finite number"):
        validate_goal({"features": [{"kind": "base_box", "params": {"w": float("-inf")}}]})


def test_int_params_are_accepted() -> None:
    result = validate_goal({"features": [{"kind": "base_box", "params": {"w": 10, "d": 5}}]})
    assert result["features"][0]["params"] == {"w": 10, "d": 5}


def test_scale_must_be_positive_finite_number() -> None:
    for bad_scale in [0, -1, float("nan"), float("inf"), True, "1.0"]:
        with pytest.raises(InvalidGoalError, match="scale"):
            validate_goal({**_base_goal(), "scale": bad_scale})


def test_valid_scale_is_preserved() -> None:
    result = validate_goal({**_base_goal(), "scale": 40.0})
    assert result["scale"] == 40.0


def test_level_must_be_an_integer() -> None:
    for bad_level in [1.0, True, "1"]:
        with pytest.raises(InvalidGoalError, match="level"):
            validate_goal({**_base_goal(), "level": bad_level})
    # None is explicitly accepted as "not supplied" and omitted from output.
    result = validate_goal({**_base_goal(), "level": None})
    assert "level" not in result


def test_valid_level_is_preserved() -> None:
    result = validate_goal({**_base_goal(), "level": 2})
    assert result["level"] == 2


def test_optional_fields_may_be_omitted() -> None:
    result = validate_goal(_base_goal())
    assert "level" not in result
    assert "scale" not in result
    assert "target" not in result


def test_target_must_be_an_object() -> None:
    with pytest.raises(InvalidGoalError, match="target"):
        validate_goal({**_base_goal(), "target": [1, 2, 3]})


def test_target_is_preserved_without_geometry_trust() -> None:
    target = {"shape": "compound", "volume": 9147.61}
    result = validate_goal({**_base_goal(), "target": target})
    assert result["target"] == target


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(InvalidGoalError, match="Unsupported Goal fields"):
        validate_goal({**_base_goal(), "bogus": 1})


def test_goal_is_not_mutated() -> None:
    goal = {
        "features": [
            {"kind": "base_box", "params": {"w": 40, "d": 30, "h": 8}},
            {"kind": "hole", "params": {"r": 3}},
        ],
        "level": 2,
        "scale": 40.0,
    }
    before = {
        "features": [
            {"kind": f["kind"], "params": dict(f["params"])} for f in goal["features"]
        ],
        "level": goal["level"],
        "scale": goal["scale"],
    }
    validate_goal(goal)
    assert goal == before


def test_feature_order_is_preserved() -> None:
    goal = {
        "features": [
            {"kind": "base_box", "params": {"w": 40}},
            {"kind": "hole", "params": {"r": 3, "x": 10}},
            {"kind": "hole", "params": {"r": 3, "x": -10}},
        ]
    }
    result = validate_goal(goal)
    kinds = [f["kind"] for f in result["features"]]
    assert kinds == ["base_box", "hole", "hole"]
    xs = [f["params"]["x"] for f in result["features"] if "x" in f["params"]]
    assert xs == [10, -10]


def test_params_default_to_empty_dict() -> None:
    result = validate_goal({"features": [{"kind": "base_box"}]})
    assert result["features"][0]["params"] == {}


def test_non_object_goal_raises() -> None:
    with pytest.raises(InvalidGoalError, match="Goal must be a JSON object"):
        validate_goal("not a goal")
    with pytest.raises(InvalidGoalError, match="Goal must be a JSON object"):
        validate_goal(None)
    with pytest.raises(InvalidGoalError, match="Goal must be a JSON object"):
        validate_goal([1, 2, 3])
