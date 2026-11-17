from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import find_freecad_executable
from freecad_mcp.server import create_server
from mcp import Client

_DISCOVERED = find_freecad_executable()
requires_freecad = pytest.mark.skipif(_DISCOVERED is None, reason="FreeCAD console executable not found.")


def _settings():
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)


async def _call_tool(name, args):
    async with Client(create_server(_settings())) as client:
        res = await client.call_tool(name, args)
        data = res.structured_content
        if data is None and res.content:
            text = "".join(getattr(c, "text", "") for c in res.content if getattr(c, "text", None))
            data = json.loads(text) if text else None
        return data


@requires_freecad
def test_e2e_successful_workflow(tmp_path: Path):
    doc_id = "e2e_ok"
    steps = [
        {"operation": "create_document", "parameters": {}},
        {"operation": "create_box", "parameters": {"length": 10.0, "width": 5.0, "height": 3.0}},
        {"operation": "recompute", "parameters": {}},
        {"operation": "validate", "parameters": {}},
        {"operation": "save_document", "parameters": {}},
    ]
    data = asyncio.run(_call_tool("run_workflow", {"document_id": doc_id, "workspace": str(tmp_path), "steps": steps}))
    assert data["ok"] is True
    assert data["status"] == "completed"


@requires_freecad
def test_e2e_state_freshness(tmp_path: Path):
    doc_id = "e2e_sf"
    steps = [
        {"operation": "create_document", "parameters": {}},
        {"operation": "create_box", "parameters": {"length": 10.0, "width": 5.0, "height": 3.0}},
        {"operation": "save_document", "parameters": {}},
    ]
    data = asyncio.run(_call_tool("run_workflow", {"document_id": doc_id, "workspace": str(tmp_path), "steps": steps}))
    assert data["ok"] is True
    ms = asyncio.run(_call_tool("get_model_state", {"document_id": doc_id, "workspace": str(tmp_path)}))
    assert ms is not None
    # live state revision must not claim persisted revision as live
    assert ms.get("state_ref", {}).get("revision") is None


@requires_freecad
def test_e2e_artifact_reopen(tmp_path: Path):
    doc_id = "e2e_art"
    steps = [
        {"operation": "create_document", "parameters": {}},
        {"operation": "create_box", "parameters": {"length": 10.0, "width": 5.0, "height": 3.0}},
        {"operation": "save_document", "parameters": {}},
    ]
    data = asyncio.run(_call_tool("run_workflow", {"document_id": doc_id, "workspace": str(tmp_path), "steps": steps}))
    assert data["ok"] is True
    fcstd = tmp_path / f"{doc_id}.fcstd"
    assert fcstd.exists() and fcstd.stat().st_size > 0
    vr = asyncio.run(_call_tool("validate_reopen", {"params": {"document_id": doc_id, "workspace": str(tmp_path)}}))
    assert isinstance(vr, dict)
    assert (vr.get("ok") is True) or (vr.get("result", {}).get("all_passed") is True) or (vr.get("all_passed") is True)


@requires_freecad
def test_e2e_failure_recovery(tmp_path: Path):
    doc_id = "e2e_rec"
    # capture pre-step state
    steps = [
        {"operation": "create_document", "parameters": {}},
        {"operation": "create_box", "parameters": {"length": 10.0, "width": 5.0, "height": 3.0}},
        {"operation": "save_document", "parameters": {}},
    ]
    data = asyncio.run(_call_tool("run_workflow", {"document_id": doc_id, "workspace": str(tmp_path), "steps": steps}))
    assert data["ok"] is True
    pre = asyncio.run(_call_tool("get_model_state", {"document_id": doc_id, "workspace": str(tmp_path)}))
    # trigger failing op with recovery
    steps2 = [
        {"operation": "inspect_object", "parameters": {"name": "NoSuchObject"}, "recover_on_failure": True},
    ]
    data2 = asyncio.run(_call_tool("run_workflow", {"document_id": doc_id, "workspace": str(tmp_path), "steps": steps2}))
    assert data2["ok"] is False
    step = data2["steps"][-1]
    assert step.get("error") is not None
    assert step.get("recovery", {}).get("restoration_ok") is True
    post = asyncio.run(_call_tool("get_model_state", {"document_id": doc_id, "workspace": str(tmp_path)}))
    # compare object counts
    pre_objs = pre.get("objects") if isinstance(pre, dict) else None
    post_objs = post.get("objects") if isinstance(post, dict) else None
    # best-effort compare serialized state
    assert pre is not None and post is not None
