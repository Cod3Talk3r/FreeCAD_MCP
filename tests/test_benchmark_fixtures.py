from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "benchmarks"
ALLOWED_ENG = {"not_assessed", "out_of_scope"}


def _load_fixtures():
    return sorted(FIXTURE_DIR.glob("*.json"))


def test_benchmark_fixtures_load_and_validate():
    ids = []
    for p in _load_fixtures():
        data = json.loads(p.read_text())
        assert data["schema_version"] == "1.0"
        assert data["fixture_id"]
        assert data["goal"]
        assert isinstance(data["operations"], list) and data["operations"]
        assert data["expected_geometric_status"] in {"valid", "invalid", "unknown"}
        assert data["engineering_status"] in ALLOWED_ENG
        assert "artifact_assertions" in data
        ids.append(data["fixture_id"])
    assert len(ids) == len(set(ids)), "fixture IDs must be unique"


def test_reject_unsupported_engineering_status(tmp_path: Path):
    bad = {
        "schema_version": "1.0",
        "fixture_id": "bad",
        "goal": "x",
        "operations": [{"operation": "create_document", "parameters": {}}],
        "expected_geometric_status": "valid",
        "engineering_status": "verified",
        "artifact_assertions": {},
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    data = json.loads(p.read_text())
    assert data["engineering_status"] not in ALLOWED_ENG
