"""M13.2: Object metadata and stable object identity tests.

Unit/schema tests run without FreeCAD. Real-FreeCAD integration tests are
skipped when the freecad console executable is unavailable.
"""

from __future__ import annotations

import subprocess
import textwrap
from typing import Any
from unittest.mock import patch

import pytest

from freecad_mcp.config import Settings
from freecad_mcp.freecad_bridge import FreeCadBridge, find_freecad_executable
from freecad_mcp.pathing import for_target
from freecad_mcp.tools import modeling

_DISCOVERED = find_freecad_executable()

requires_freecad = pytest.mark.skipif(
    _DISCOVERED is None,
    reason="FreeCAD console executable not found (install FreeCAD or set FREECAD_MCP_FREECAD_EXECUTABLE).",
)


def _settings() -> Settings:
    return Settings(freecad_executable=str(_DISCOVERED), freecad_timeout_seconds=120)


class _FakeServer:
    """Stand-in MCP server that records the functions registered via @server.tool."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, name: str, **kwargs: Any):
        def deco(fn):
            self.tools[name] = fn
            return fn

        return deco


def _registered_tools() -> dict[str, Any]:
    server = _FakeServer()
    modeling._register_tools(server, Settings(freecad_timeout_seconds=120))
    return server.tools


# ---------------------------------------------------------------------------
# Schema tests (no FreeCAD required)
# ---------------------------------------------------------------------------


def test_object_info_has_label_field_defaults_none() -> None:
    info = modeling.ObjectInfo(name="Box1", type="Part::Box")
    assert info.name == "Box1"
    assert info.type == "Part::Box"
    assert info.label is None


def test_object_info_accepts_label_and_shape_fields() -> None:
    info = modeling.ObjectInfo(
        name="Box1",
        type="Part::Box",
        label="My Renamed Box",
        shape_type="Solid",
        volume=150.0,
        length=10.0,
        width=5.0,
        height=3.0,
    )
    assert info.label == "My Renamed Box"
    assert info.shape_type == "Solid"
    assert info.volume == 150.0


def test_document_list_has_metadata_fields_defaults_none() -> None:
    doc_list = modeling.DocumentList(document_id="doc", objects=[])
    assert doc_list.document_id == "doc"
    assert doc_list.revision is None
    assert doc_list.created_at is None
    assert doc_list.last_saved_at is None
    assert doc_list.last_modified_at is None


def test_document_list_accepts_metadata_fields() -> None:
    doc_list = modeling.DocumentList(
        document_id="doc",
        objects=[],
        revision=3,
        created_at="2026-01-01T00:00:00Z",
        last_saved_at="2026-01-02T00:00:00Z",
        last_modified_at="2026-01-02T00:00:00Z",
    )
    assert doc_list.revision == 3
    assert doc_list.created_at == "2026-01-01T00:00:00Z"
    assert doc_list.last_saved_at == "2026-01-02T00:00:00Z"
    assert doc_list.last_modified_at == "2026-01-02T00:00:00Z"


# ---------------------------------------------------------------------------
# MCP tool handler tests (mocked bridge, no FreeCAD required)
# ---------------------------------------------------------------------------


class _FakeBridge:
    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, op: str, params: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((op, params))
        return dict(self.result)


def test_inspect_object_tool_exposes_label_and_type_from_info(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _registered_tools()
    fn = tools["inspect_object"]

    bridge = _FakeBridge(
        {
            "document_id": "doc",
            "object_name": "Box1",
            "info": {
                "name": "Box1",
                "type": "Part::Box",
                "label": "My Renamed Box",
                "shape_type": "Solid",
                "volume": 150.0,
                "length": 10.0,
                "width": 5.0,
                "height": 3.0,
            },
        }
    )
    with patch.object(modeling, "_make_bridge", return_value=bridge):
        result = fn(modeling.InspectObjectParams(document_id="doc", object_name="Box1"))

    assert isinstance(result, modeling.ObjectInfo)
    assert result.name == "Box1"
    assert result.type == "Part::Box"
    assert result.label == "My Renamed Box"
    assert result.shape_type == "Solid"
    assert result.volume == 150.0
    assert result.length == 10.0
    assert bridge.calls[0][0] == "inspect_object"
    assert bridge.calls[0][1]["object_name"] == "Box1"


def test_inspect_object_tool_falls_back_to_params_name_when_info_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _registered_tools()
    fn = tools["inspect_object"]

    bridge = _FakeBridge({"document_id": "doc", "object_name": "Box1", "info": {}})
    with patch.object(modeling, "_make_bridge", return_value=bridge):
        result = fn(modeling.InspectObjectParams(document_id="doc", object_name="Box1"))

    assert result.name == "Box1"
    assert result.type == ""
    assert result.label is None


def test_list_objects_tool_populates_document_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _registered_tools()
    fn = tools["list_objects"]

    bridge = _FakeBridge(
        {
            "document_id": "doc",
            "objects": [
                {"name": "Box1", "type": "Part::Box", "shape_type": "Solid", "volume": 150.0, "label": "Box"}
            ],
            "meta": {
                "revision": 2,
                "created_at": "2026-01-01T00:00:00Z",
                "last_saved_at": "2026-01-02T00:00:00Z",
                "last_modified_at": "2026-01-02T00:00:00Z",
            },
        }
    )
    with patch.object(modeling, "_make_bridge", return_value=bridge):
        result = fn(modeling.ListObjectsParams(document_id="doc"))

    assert isinstance(result, modeling.DocumentList)
    assert result.document_id == "doc"
    assert result.revision == 2
    assert result.created_at == "2026-01-01T00:00:00Z"
    assert result.last_saved_at == "2026-01-02T00:00:00Z"
    assert result.last_modified_at == "2026-01-02T00:00:00Z"
    assert len(result.objects) == 1
    assert result.objects[0].name == "Box1"
    assert result.objects[0].label == "Box"


def test_list_objects_tool_handles_missing_meta_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _registered_tools()
    fn = tools["list_objects"]

    bridge = _FakeBridge({"document_id": "doc", "objects": []})
    with patch.object(modeling, "_make_bridge", return_value=bridge):
        result = fn(modeling.ListObjectsParams(document_id="doc"))

    assert result.revision is None
    assert result.created_at is None
    assert result.last_saved_at is None
    assert result.last_modified_at is None


# ---------------------------------------------------------------------------
# Real-FreeCAD integration tests (skipped without freecadcmd)
# ---------------------------------------------------------------------------


@requires_freecad
def test_real_identity_and_metadata_across_save_reopen(tmp_path) -> None:
    """Create a box, save, reopen in a fresh bridge process; Name is stable, metadata exposed."""
    bridge = FreeCadBridge.from_settings(_settings())
    doc_id = "m132_identity"
    workspace = str(tmp_path)

    bridge.run("create_document", {"document_id": doc_id, "workspace": workspace})
    bridge.run(
        "create_box",
        {
            "document_id": doc_id,
            "object_name": "Box1",
            "length": 10.0,
            "width": 5.0,
            "height": 3.0,
            "workspace": workspace,
        },
    )
    bridge.run("save_document", {"document_id": doc_id, "workspace": workspace})

    # Fresh bridge process simulates document reopen.
    bridge2 = FreeCadBridge.from_settings(_settings())
    listed = bridge2.run("list_objects", {"document_id": doc_id, "workspace": workspace})

    assert len(listed["objects"]) == 1
    obj = listed["objects"][0]
    assert obj["name"] == "Box1"
    assert obj["type"] == "Part::Box"

    meta = listed["meta"]
    assert isinstance(meta, dict)
    assert "revision" in meta
    assert isinstance(meta["revision"], int) and meta["revision"] >= 0
    # Save should have persisted a revision and a last-saved timestamp.
    assert meta["revision"] >= 1
    assert isinstance(meta.get("last_saved_at"), str) and meta["last_saved_at"]

    info = bridge2.run(
        "inspect_object",
        {"document_id": doc_id, "object_name": "Box1", "workspace": workspace},
    )
    assert info["info"]["name"] == "Box1"
    assert info["info"]["type"] == "Part::Box"
    # Label equals Name at creation (has not been renamed).
    assert info["info"]["label"] == "Box1"


@requires_freecad
def test_real_label_change_does_not_change_internal_name(tmp_path) -> None:
    """Changing the mutable Label must not change the internal Name; both persist across save/reopen."""
    target = "windows" if str(_DISCOVERED).lower().endswith(".exe") else "native"
    fcstd = for_target(tmp_path / "label_probe.fcstd", target)
    code = textwrap.dedent(f"""
        import FreeCAD as App
        doc = App.newDocument("LabelProbe")
        box = doc.addObject("Part::Box", "Box1")
        doc.recompute()
        name_at_creation = box.Name
        box.Label = "Renamed Box"
        doc.recompute()
        assert box.Name == name_at_creation == "Box1", (box.Name, name_at_creation)
        assert box.Label == "Renamed Box"
        doc.saveAs({fcstd!r})
        doc2 = App.openDocument({fcstd!r})
        box2 = doc2.getObject("Box1")
        assert box2.Name == "Box1", box2.Name
        assert box2.Label == "Renamed Box", box2.Label
        print("LABEL_STABILITY_OK")
    """)
    process = subprocess.run(
        [str(_DISCOVERED), "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "LABEL_STABILITY_OK" in process.stdout, process.stdout + process.stderr
