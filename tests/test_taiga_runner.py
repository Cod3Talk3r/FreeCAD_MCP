"""Tests for the standalone Taiga-S1 JSON runner.

These tests execute the real runner with the host Python interpreter and a fake
``freecad_s1`` package placed on ``PYTHONPATH``. They do not need Taiga, Torch,
model weights, or network access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from freecad_mcp.integrations.taiga.runner import REQUEST_ENV, RESPONSE_ENV

_RUNNER = Path(__file__).resolve().parents[1] / "src" / "freecad_mcp" / "integrations" / "taiga" / "runner.py"

_STATE = {
    "doc_open": False,
    "workbench": "StartWorkbench",
    "shape": {"bbox": [0.0, 0.0, 0.0]},
}
_GOAL = {"features": []}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _fake_taiga(tmp_path: Path, *, torch: bool = True, safetensors: bool = True) -> Path:
    """Create a fake ``freecad_s1`` package matching the verified API shape."""
    fake = tmp_path / "fake_site"
    _write(fake / "freecad_s1" / "__init__.py", "")
    _write(fake / "freecad_s1" / "model" / "__init__.py", "")
    _write(
        fake / "freecad_s1" / "schema.py",
        """
        class _Record:
            @classmethod
            def from_json(cls, payload):
                if not isinstance(payload, dict):
                    raise TypeError("record must be an object")
                return cls(payload)

        class State(_Record):
            def __init__(self, payload):
                for key in ("doc_open", "workbench", "shape"):
                    if key not in payload:
                        raise KeyError(key)
                if not isinstance(payload["shape"], dict) or "bbox" not in payload["shape"]:
                    raise KeyError("shape.bbox")

        class Goal(_Record):
            def __init__(self, payload):
                if "features" not in payload:
                    raise KeyError("features")
        """,
    )
    _write(
        fake / "freecad_s1" / "model" / "net.py",
        """
        import json
        from pathlib import Path

        def from_pretrained(name_or_path, device="cpu"):
            path = Path(name_or_path)
            if not path.is_dir():
                raise AssertionError("a local directory is required")
            config = json.loads((path / "config.json").read_text(encoding="utf-8"))
            return {"config": config["config"], "device": device}
        """,
    )
    _write(
        fake / "freecad_s1" / "rollout.py",
        """
        import math
        import os

        class Policy:
            def __init__(self, model, device):
                self.model = model
                self.device = device

            def score(self, state, goal, actions):
                mode = os.environ.get("FAKE_SCORE_MODE", "ok")
                if mode == "raise":
                    raise RuntimeError("fake scoring failure")
                if mode == "missing":
                    return {actions[0]: 1.0}
                if mode == "extra":
                    result = {a: 1.0 / len(actions) for a in actions}
                    result["Bogus_Action"] = 0.1
                    return result
                if mode == "nan":
                    result = {a: 1.0 / len(actions) for a in actions}
                    result[actions[0]] = float("nan")
                    return result
                if mode == "bool":
                    result = {a: 1.0 / len(actions) for a in actions}
                    result[actions[0]] = True
                    return result
                if mode == "text":
                    result = {a: 1.0 / len(actions) for a in actions}
                    result[actions[0]] = "high"
                    return result
                if mode == "nonsense":
                    return ["not", "a", "mapping"]

                total = len(actions) * (len(actions) + 1) / 2
                scores = {action: (index + 1) / total for index, action in enumerate(actions)}
                return dict(sorted(scores.items(), key=lambda item: -item[1]))
        """,
    )
    if torch:
        _write(fake / "torch.py", "")
    if safetensors:
        _write(fake / "safetensors.py", "")
    _write(
        fake / "sitecustomize.py",
        """
        import os
        import sys

        blocked = {name for name in os.environ.get("FAKE_BLOCK_MODULES", "").split(",") if name}

        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                if any(name == item or name.startswith(item + ".") for item in blocked):
                    raise ImportError("blocked optional dependency: " + name)
                return None

        if blocked:
            sys.meta_path.insert(0, _Blocker())
        """,
    )
    return fake


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"config": {}}), encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"fake-weights")
    return model


def _run(
    tmp_path: Path,
    request: dict,
    *,
    fake: Path | None = None,
    block: tuple[str, ...] = (),
    score_mode: str = "ok",
    timeout: float = 30.0,
) -> tuple[int, dict | None, str, str]:
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    env = dict(os.environ)
    if fake is None:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = str(fake)
    env[REQUEST_ENV] = str(request_path)
    env[RESPONSE_ENV] = str(response_path)
    env["FAKE_SCORE_MODE"] = score_mode
    if block:
        env["FAKE_BLOCK_MODULES"] = ",".join(block)

    completed = subprocess.run(
        [sys.executable, str(_RUNNER)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )

    response = None
    if response_path.is_file():
        response = json.loads(response_path.read_text(encoding="utf-8"))
    return completed.returncode, response, completed.stdout, completed.stderr


def _score_request(model: Path, *, actions: list[str] | None = None, state=None, goal=None) -> dict:
    return {
        "op": "score_actions",
        "params": {
            "state": _STATE if state is None else state,
            "goal": _GOAL if goal is None else goal,
            "actions": ["PartDesign_Body", "Std_New"] if actions is None else actions,
            "model": str(model),
            "device": "cpu",
        },
    }


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_available_with_fake_taiga(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)
    model = _model_dir(tmp_path)

    code, response, stdout, _ = _run(tmp_path, {"op": "status", "params": {"model": str(model)}}, fake=fake)

    assert code == 0
    assert stdout == ""
    assert response is not None
    assert response["ok"] is True
    result = response["result"]
    assert result["available"] is True
    assert result["reason"] is None
    assert result["model_available"] is True
    assert result["model_error"] is None


def test_status_reports_invalid_model_without_failing(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)

    code, response, _, _ = _run(
        tmp_path,
        {"op": "status", "params": {"model": str(tmp_path / "missing-model")}},
        fake=fake,
    )

    assert code == 0
    assert response is not None
    result = response["result"]
    assert result["available"] is True
    assert result["model_available"] is False
    assert "does not exist" in result["model_error"]


def test_status_reports_missing_taiga(tmp_path: Path) -> None:
    code, response, _, _ = _run(tmp_path, {"op": "status", "params": {}}, fake=None)

    assert code == 0
    assert response is not None
    result = response["result"]
    assert result["available"] is False
    assert result["reason"] == "not_installed"


def test_status_reports_missing_torch(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path, torch=False)

    code, response, _, _ = _run(
        tmp_path,
        {"op": "status", "params": {}},
        fake=fake,
        block=("torch",),
    )

    assert code == 0
    assert response is not None
    assert response["result"]["reason"] == "missing_dependency"
    assert "torch" in response["result"]["detail"]


def test_status_reports_missing_safetensors(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path, safetensors=False)

    code, response, _, _ = _run(
        tmp_path,
        {"op": "status", "params": {}},
        fake=fake,
        block=("safetensors",),
    )

    assert code == 0
    assert response is not None
    assert response["result"]["reason"] == "missing_dependency"
    assert "safetensors" in response["result"]["detail"]


# ---------------------------------------------------------------------------
# score_actions success
# ---------------------------------------------------------------------------


def test_score_actions_success(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)
    model = _model_dir(tmp_path)

    code, response, stdout, _ = _run(tmp_path, _score_request(model), fake=fake)

    assert code == 0
    assert stdout == ""
    assert response is not None
    assert response["ok"] is True
    scores = response["result"]["scores"]
    assert {item["action"] for item in scores} == {"PartDesign_Body", "Std_New"}
    assert sum(item["probability"] for item in scores) == pytest.approx(1.0)
    assert all(0.0 <= item["probability"] <= 1.0 for item in scores)


# ---------------------------------------------------------------------------
# model validation
# ---------------------------------------------------------------------------


def _assert_invalid_request(tmp_path: Path, request: dict, fake: Path) -> None:
    code, response, _, _ = _run(tmp_path, request, fake=fake)
    assert code == 0
    assert response is not None
    assert response["ok"] is False
    assert response["error"]["type"] == "InvalidRequest"


def test_score_rejects_missing_model_directory(tmp_path: Path) -> None:
    _assert_invalid_request(tmp_path, _score_request(tmp_path / "nope"), _fake_taiga(tmp_path))


def test_score_rejects_remote_model_id(tmp_path: Path) -> None:
    _assert_invalid_request(
        tmp_path,
        _score_request(Path("shhivv/taiga-s1")),
        _fake_taiga(tmp_path),
    )


def test_score_rejects_missing_config_file(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"weights")

    _assert_invalid_request(tmp_path, _score_request(model), fake)


def test_score_rejects_malformed_config_file(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{not json", encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")

    _assert_invalid_request(tmp_path, _score_request(model), fake)


def test_score_rejects_config_without_config_object(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"architecture": "S1Model"}), encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")

    _assert_invalid_request(tmp_path, _score_request(model), fake)


def test_score_rejects_missing_weights_file(tmp_path: Path) -> None:
    fake = _fake_taiga(tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(json.dumps({"config": {}}), encoding="utf-8")

    _assert_invalid_request(tmp_path, _score_request(model), fake)


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actions",
    [
        [],
        ["Same", "Same"],
        ["Ok", ""],
        ["Ok", 3],
        "Std_New",
    ],
)
def test_score_rejects_invalid_actions(tmp_path: Path, actions) -> None:
    model = _model_dir(tmp_path)
    _assert_invalid_request(
        tmp_path,
        _score_request(model, actions=actions),
        _fake_taiga(tmp_path),
    )


def test_score_rejects_invalid_state(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)
    _assert_invalid_request(
        tmp_path,
        _score_request(model, state={"doc_open": True}),
        _fake_taiga(tmp_path),
    )


def test_score_rejects_invalid_goal(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)
    _assert_invalid_request(
        tmp_path,
        _score_request(model, goal={}),
        _fake_taiga(tmp_path),
    )


# ---------------------------------------------------------------------------
# score validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    ["missing", "extra", "nan", "bool", "text", "nonsense"],
)
def test_score_rejects_malformed_upstream_results(tmp_path: Path, mode: str) -> None:
    model = _model_dir(tmp_path)

    code, response, _, _ = _run(
        tmp_path,
        _score_request(model),
        fake=_fake_taiga(tmp_path),
        score_mode=mode,
    )

    assert code == 0
    assert response is not None
    assert response["ok"] is False
    assert response["error"]["type"] == "Incompatible"


def test_score_reports_scoring_exception(tmp_path: Path) -> None:
    model = _model_dir(tmp_path)

    code, response, _, _ = _run(
        tmp_path,
        _score_request(model),
        fake=_fake_taiga(tmp_path),
        score_mode="raise",
    )

    assert code == 0
    assert response is not None
    assert response["ok"] is False
    assert response["error"]["type"] == "RuntimeError"
    assert "fake scoring failure" in response["error"]["message"]


def test_unknown_operation_is_rejected(tmp_path: Path) -> None:
    code, response, _, _ = _run(tmp_path, {"op": "nope", "params": {}}, fake=_fake_taiga(tmp_path))

    assert code == 0
    assert response is not None
    assert response["ok"] is False
    assert response["error"]["type"] == "InvalidRequest"
