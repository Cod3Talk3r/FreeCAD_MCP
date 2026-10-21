"""Tests for the optional Taiga-S1 adapter.

The real Taiga-S1 package and Torch are intentionally not installed. The
adapter is exercised against a fake ``freecad_s1`` module so its verified API
usage can be tested without adding a heavyweight optional dependency.
"""

from __future__ import annotations

import subprocess
import sys
import types
from typing import Any

import pytest

from freecad_mcp.integrations import IntegrationStatus, PlanningBackend, UnavailableReason
from freecad_mcp.integrations.taiga import (
    ActionScore,
    ActionScoringResult,
    TaigaBackend,
)
from freecad_mcp.integrations.taiga import adapter as adapter_module


def test_core_and_adapter_import_without_taiga_or_torch() -> None:
    """A fresh interpreter must import the core path with both options blocked."""
    source = """
import sys

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "torch" or name == "freecad_s1" or name.startswith("freecad_s1."):
            raise ImportError("blocked optional dependency: " + name)
        return None

sys.meta_path.insert(0, Blocker())

import freecad_mcp
import freecad_mcp.server
import freecad_mcp.integrations
import freecad_mcp.integrations.taiga

assert "torch" not in sys.modules
assert "freecad_s1" not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_taiga_backend_implements_the_contract() -> None:
    assert isinstance(TaigaBackend(), PlanningBackend)


def test_missing_taiga_is_reported_as_structured_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_import(name: str) -> Any:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(adapter_module.importlib, "import_module", missing_import)

    backend = TaigaBackend()
    status = backend.status()

    assert status == IntegrationStatus.unavailable(
        "taiga-s1",
        UnavailableReason.NOT_INSTALLED,
        detail=status.detail,
    )
    assert status.is_available is False
    assert status.reason is UnavailableReason.NOT_INSTALLED
    assert "freecad_s1" in status.detail

    plan = backend.plan("a plate")
    assert plan.planned is False
    assert plan.steps == ()
    assert plan.reason is UnavailableReason.NOT_INSTALLED

    scored = backend.score_actions({"doc_open": False}, {"features": []}, ["Std_New"])
    assert scored.scored is False
    assert scored.scores == ()
    assert scored.reason is UnavailableReason.NOT_INSTALLED


def test_broken_taiga_import_is_reported_as_incompatible(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_import(name: str) -> Any:
        raise RuntimeError("broken installation")

    monkeypatch.setattr(adapter_module.importlib, "import_module", broken_import)

    status = TaigaBackend().status()

    assert status.is_available is False
    assert status.reason is UnavailableReason.INCOMPATIBLE
    assert "RuntimeError" in status.detail


def test_missing_torch_is_reported_as_a_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = types.ModuleType("freecad_s1")
    fake_module.__version__ = "0.1.0"  # type: ignore[attr-defined]

    monkeypatch.setattr(adapter_module.importlib, "import_module", lambda name: fake_module)
    monkeypatch.setattr(adapter_module, "find_spec", lambda name: None)

    status = TaigaBackend().status()

    assert status.is_available is False
    assert status.reason is UnavailableReason.MISSING_DEPENDENCY
    assert status.version == "0.1.0"
    assert "PyTorch" in status.detail


def test_unsupported_python_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "_MIN_PYTHON", (99, 0))

    status = TaigaBackend().status()

    assert status.is_available is False
    assert status.reason is UnavailableReason.INCOMPATIBLE
    assert "Python" in status.detail


@pytest.fixture
def fake_taiga(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a fake ``freecad_s1`` package matching the verified API shape."""
    calls: dict[str, Any] = {
        "from_pretrained": [],
        "policies": [],
        "scores": [],
    }

    schema = types.ModuleType("freecad_s1.schema")

    class FakeState:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        @classmethod
        def from_json(cls, payload: dict[str, Any]) -> FakeState:
            return cls(payload)

    class FakeGoal:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        @classmethod
        def from_json(cls, payload: dict[str, Any]) -> FakeGoal:
            return cls(payload)

    schema.State = FakeState  # type: ignore[attr-defined]
    schema.Goal = FakeGoal  # type: ignore[attr-defined]

    net = types.ModuleType("freecad_s1.model.net")

    def from_pretrained(model: str, device: str = "cpu") -> object:
        calls["from_pretrained"].append((model, device))
        return object()

    net.from_pretrained = from_pretrained  # type: ignore[attr-defined]

    rollout = types.ModuleType("freecad_s1.rollout")

    class FakePolicy:
        def __init__(self, model: object, device: str) -> None:
            calls["policies"].append((model, device))

        def score(self, state: FakeState, goal: FakeGoal, actions: list[str]) -> dict[str, float]:
            calls["scores"].append((state.payload, goal.payload, list(actions)))
            return {actions[1]: 0.75, actions[0]: 0.25}

    rollout.Policy = FakePolicy  # type: ignore[attr-defined]

    package = types.ModuleType("freecad_s1")
    package.__path__ = []  # type: ignore[attr-defined]
    package.__version__ = "0.1.0"  # type: ignore[attr-defined]

    model_package = types.ModuleType("freecad_s1.model")
    model_package.__path__ = []  # type: ignore[attr-defined]

    modules = {
        "freecad_s1": package,
        "freecad_s1.model": model_package,
        "freecad_s1.model.net": net,
        "freecad_s1.rollout": rollout,
        "freecad_s1.schema": schema,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setattr(adapter_module.importlib, "import_module", lambda name: package)
    monkeypatch.setattr(adapter_module, "find_spec", lambda name: object())

    return calls


def test_adapter_reports_available_with_fake_taiga(fake_taiga: dict[str, Any]) -> None:
    status = TaigaBackend().status()

    assert status.is_available is True
    assert status.reason is None
    assert status.version == "0.1.0"


def test_plan_is_deferred_even_when_taiga_is_available(fake_taiga: dict[str, Any]) -> None:
    result = TaigaBackend().plan("a plate with one hole")

    assert result.planned is False
    assert result.steps == ()
    assert result.reason is UnavailableReason.RUNTIME_CONTEXT_REQUIRED
    assert "live FreeCAD State" in result.detail
    assert "no model-produced command was executed" in result.detail


def test_score_actions_uses_the_verified_taiga_api_without_executing(
    fake_taiga: dict[str, Any],
) -> None:
    backend = TaigaBackend(model="local/taiga-test", device="cpu")
    state = {"doc_open": True, "workbench": "PartDesignWorkbench"}
    goal = {"features": [{"kind": "base_box", "params": {"w": 40.0}}]}

    result = backend.score_actions(state, goal, ["PartDesign_Body", "Std_New"])

    assert result.scored is True
    assert result.reason is None
    assert [score.action for score in result.scores] == ["Std_New", "PartDesign_Body"]
    assert [score.probability for score in result.scores] == [0.75, 0.25]

    assert fake_taiga["from_pretrained"] == [("local/taiga-test", "cpu")]
    assert len(fake_taiga["policies"]) == 1
    assert fake_taiga["scores"] == [(state, goal, ["PartDesign_Body", "Std_New"])]


def test_policy_is_loaded_once(fake_taiga: dict[str, Any]) -> None:
    backend = TaigaBackend()
    actions = ["Std_New", "Done"]

    first = backend.score_actions({}, {}, actions)
    second = backend.score_actions({}, {}, actions)

    assert first.scored is True
    assert second.scored is True
    assert len(fake_taiga["from_pretrained"]) == 1
    assert len(fake_taiga["policies"]) == 1


@pytest.mark.parametrize(
    "actions",
    [
        [],
        ["Std_New", "Std_New"],
        ["Std_New", ""],
        "Std_New",
        None,
    ],
)
def test_invalid_actions_are_rejected(fake_taiga: dict[str, Any], actions: Any) -> None:
    result = TaigaBackend().score_actions({}, {}, actions)

    assert result.scored is False
    assert result.reason is UnavailableReason.INVALID_REQUEST
    assert "distinct" in result.detail


def test_scoring_errors_are_structured(fake_taiga: dict[str, Any]) -> None:
    net = sys.modules["freecad_s1.model.net"]
    net.from_pretrained = lambda model, device="cpu": (_ for _ in ()).throw(  # type: ignore[attr-defined]
        TypeError("incompatible loader")
    )

    result = TaigaBackend().score_actions({}, {}, ["Std_New", "Done"])

    assert result.scored is False
    assert result.reason is UnavailableReason.INCOMPATIBLE
    assert "TypeError" in result.detail


def test_unexpected_score_shape_is_structured(
    fake_taiga: dict[str, Any],
) -> None:
    backend = TaigaBackend()
    backend._policy = types.SimpleNamespace(score=lambda state, goal, actions: {"Std_New": 1.0})

    result = backend.score_actions({}, {}, ["Std_New", "Done"])

    assert result.scored is False
    assert result.reason is UnavailableReason.INCOMPATIBLE
    assert "missing" in result.detail


def test_action_score_rejects_invalid_probabilities() -> None:
    with pytest.raises(ValueError, match="probability"):
        ActionScore("Std_New", 1.5)

    with pytest.raises(ValueError, match="probability"):
        ActionScore("Std_New", float("nan"))


def test_available_scoring_result_requires_scores() -> None:
    with pytest.raises(ValueError, match="at least one score"):
        ActionScoringResult.scored_actions("taiga-s1", ())

    result = ActionScoringResult.unavailable(
        "taiga-s1",
        UnavailableReason.INVALID_REQUEST,
        detail="bad input",
    )
    assert result.scored is False
    assert result.scores == ()
