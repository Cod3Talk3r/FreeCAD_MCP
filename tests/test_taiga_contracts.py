"""Focused tests for M20 Phase 1B Taiga-agnostic contracts.

Covers: versioned CAD state contract (serialization, validation, versioning,
missing-field behavior), the fail-closed action registry (completeness and
unknown/unmapped actions), parameter translation (missing and ambiguous
references, no Taiga defaults), and import isolation from FreeCAD/Taiga.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest
from pydantic import ValidationError

from freecad_mcp.app.taiga_contracts import (
    ACTION_REGISTRY,
    CADNode,
    CADSelectionItem,
    CADShapeDiagnostics,
    CADState,
    CADStateConversionError,
    MappingKind,
    ParameterTranslationResult,
    ReferenceContext,
    UnknownActionError,
    VERIFIED_ACTION_IDS,
    get_action_mapping,
    is_executable,
    is_terminal,
    translate_parameters,
)
from freecad_mcp.freecad_bridge.runner import OPERATIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_state() -> CADState:
    return CADState(
        source="test",
        doc_open=True,
        workbench="PartDesignWorkbench",
        edit=None,
        has_body=True,
        undo_available=False,
        tree=[
            CADNode(
                name="Body",
                type="PartDesign::Body",
                parent=-1,
                depth=0,
                num={"tip": 1.0, "valid": 1.0},
                geo={},
                cons={},
            ),
            CADNode(
                name="Sketch",
                type="Sketcher::SketchObject",
                parent=0,
                depth=1,
                num={"n_geo": 4.0, "n_constraints": 2.0, "dof": 0.0},
                geo={"line": 4},
                cons={"Horizontal": 1, "Distance": 1},
            ),
        ],
        selection=[
            CADSelectionItem(
                kind="face",
                object="Pad",
                object_type="PartDesign::Pad",
                subs=["Face1"],
                normal=(0.0, 0.0, 1.0),
                offset=5.0,
                count=1,
            )
        ],
        recent=["PartDesign_Pad"],
        shape=CADShapeDiagnostics(
            available=True,
            valid=True,
            volume=1000.0,
            area=600.0,
            bbox=(10.0, 10.0, 10.0),
            n_faces=6,
            n_edges=12,
            n_solids=1,
            face_dirs=["+Z", "-Z"],
        ),
        collected_at="2026-11-17T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# State contract
# ---------------------------------------------------------------------------


def test_state_schema_version_default_and_rejection() -> None:
    state = CADState(source="test")
    assert state.schema_version == 1
    assert state.missing_required_fields() == [
        "doc_open",
        "workbench",
        "shape.bbox",
        "shape.valid",
        "shape.volume",
        "shape.n_faces",
        "shape.n_edges",
        "shape.n_solids",
        "shape.face_dirs",
    ]

    with pytest.raises(ValidationError):
        CADState(source="test", schema_version=2)  # type: ignore[arg-type]


def test_state_serialization_roundtrip() -> None:
    original = _full_state()
    dumped = original.model_dump(mode="json")
    restored = CADState.model_validate(dumped)
    assert restored == original
    # Tuples survive as lists through JSON mode and re-validate to tuples.
    assert restored.shape.bbox == (10.0, 10.0, 10.0)
    assert restored.selection[0].normal == (0.0, 0.0, 1.0)


def test_state_to_taiga_state_json_full() -> None:
    payload = _full_state().to_taiga_state_json()
    # Required keys Taiga State.from_json reads unconditionally.
    assert payload["doc_open"] is True
    assert payload["workbench"] == "PartDesignWorkbench"
    assert payload["shape"]["bbox"] == [10.0, 10.0, 10.0]
    assert payload["tree"][1]["name"] == "Sketch"
    assert payload["tree"][1]["geo"] == {"line": 4}
    assert payload["selection"][0]["normal"] == [0.0, 0.0, 1.0]
    assert payload["selection"][0]["object"] == "Pad"
    assert payload["ui"] is None


def test_state_missing_required_fields_fail_closed() -> None:
    state = CADState(source="test")  # nothing captured
    expected = [
        "doc_open",
        "workbench",
        "shape.bbox",
        "shape.valid",
        "shape.volume",
        "shape.n_faces",
        "shape.n_edges",
        "shape.n_solids",
        "shape.face_dirs",
    ]
    assert state.missing_required_fields() == expected
    with pytest.raises(CADStateConversionError) as excinfo:
        state.to_taiga_state_json()
    assert set(excinfo.value.missing_fields) == set(expected)


def test_state_unavailable_fields_are_not_fabricated() -> None:
    state = CADState(
        source="m19_snapshot",
        doc_open=None,
        workbench=None,
        shape=CADShapeDiagnostics(available=False),
        unavailable_fields=["doc_open", "workbench", "shape.bbox", "selection", "tree"],
    )
    # Unknown values stay None; they are never replaced with defaults.
    assert state.doc_open is None
    assert state.workbench is None
    assert state.shape.bbox is None
    assert state.shape.valid is None
    with pytest.raises(CADStateConversionError):
        state.to_taiga_state_json()


def test_state_selection_without_normal_cannot_convert() -> None:
    state = CADState(
        source="test",
        doc_open=True,
        workbench="PartDesignWorkbench",
        shape=CADShapeDiagnostics(available=True, bbox=(1.0, 1.0, 1.0)),
        selection=[CADSelectionItem(kind="face", object="Pad", object_type="PartDesign::Pad")],
    )
    assert "selection[].normal" in state.missing_required_fields()
    with pytest.raises(CADStateConversionError):
        state.to_taiga_state_json()


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------


def test_registry_operations_are_allowlisted() -> None:
    for action_id, mapping in ACTION_REGISTRY.items():
        if mapping.operation is not None:
            assert mapping.operation in OPERATIONS, (
                f"{action_id} maps to non-allowlisted operation {mapping.operation!r}"
            )


def test_registry_covers_declared_catalogue_and_is_fail_closed() -> None:
    # Representative coverage from the pinned actions.CATALOGUE.
    expected = {
        "Std_New",
        "Std_Workbench:PartDesignWorkbench",
        "Std_Undo",
        "Std_ViewFitAll",
        "Done",
        "Select:Face+Z",
        "Select:Edges|Z",
        "PartDesign_Body",
        "PartDesign_NewSketch",
        "PartDesign_Pad",
        "PartDesign_Revolution",
        "PartDesign_Pocket",
        "PartDesign_Groove",
        "PartDesign_Hole",
        "PartDesign_Fillet",
        "PartDesign_Chamfer",
        "PartDesign_Draft",
        "PartDesign_Thickness",
        "PartDesign_Mirrored",
        "PartDesign_LinearPattern",
        "PartDesign_PolarPattern",
        "Part_Box",
        "Part_Cylinder",
        "Sketcher_CreateRectangle",
        "Sketcher_CreateCircle",
        "Sketcher_CreateHexagon",
        "Sketcher_CreateLine",
        "Sketcher_CreatePoint",
        "Sketcher_ConstrainDistanceX",
        "Sketcher_ConstrainDistanceY",
        "Sketcher_ConstrainDiameter",
        "Sketcher_ConstrainRadius",
        "Sketcher_ConstrainLock",
        "Sketcher_ConstrainHorizontal",
        "Sketcher_ConstrainVertical",
        "Sketcher_ToggleConstruction",
        "Sketcher_LeaveSketch",
    }
    assert expected <= set(VERIFIED_ACTION_IDS)

    # Unknown / unsupported / terminal are never executable.
    assert is_executable("Bogus_Action") is False
    assert is_executable("PartDesign_Draft") is False
    assert is_executable("Std_Undo") is False
    assert is_executable("Done") is False
    assert is_terminal("Done") is True
    assert is_terminal("PartDesign_Pad") is False

    with pytest.raises(UnknownActionError):
        get_action_mapping("Bogus_Action")


def test_registry_mapping_kinds_and_representative_mappings() -> None:
    assert ACTION_REGISTRY["PartDesign_Pad"].mapping_kind is MappingKind.CONDITIONAL
    assert ACTION_REGISTRY["PartDesign_Pad"].operation == "sketch_pad"
    assert ACTION_REGISTRY["Part_Box"].operation == "create_box"
    assert ACTION_REGISTRY["Part_Cylinder"].operation == "create_cylinder"
    assert ACTION_REGISTRY["PartDesign_Body"].operation == "create_body"
    assert ACTION_REGISTRY["PartDesign_NewSketch"].operation == "create_sketch"
    assert ACTION_REGISTRY["Sketcher_ConstrainHorizontal"].operation == "sketch_add_constraint"
    assert ACTION_REGISTRY["Sketcher_ConstrainHorizontal"].constraint_type == "Horizontal"

    # Partial mapping injects a definitional constant, not a Taiga default.
    hexagon = ACTION_REGISTRY["Sketcher_CreateHexagon"]
    assert hexagon.mapping_kind is MappingKind.PARTIAL
    assert hexagon.operation == "sketch_add_regular_polygon"
    assert hexagon.injected_params == {"sides": 6}

    # Unsupported and terminal carry no operation.
    assert ACTION_REGISTRY["PartDesign_Draft"].mapping_kind is MappingKind.UNSUPPORTED
    assert ACTION_REGISTRY["PartDesign_Draft"].operation is None
    assert ACTION_REGISTRY["Std_Undo"].operation is None

    # Every executable mapping records its operation.
    for mapping in ACTION_REGISTRY.values():
        if mapping.executable:
            assert mapping.operation is not None


# ---------------------------------------------------------------------------
# Parameter translation
# ---------------------------------------------------------------------------


def test_translate_direct_success_and_missing_parameter() -> None:
    ok = translate_parameters(
        "Part_Box", {"document_id": "d", "length": 10, "width": 5, "height": 3}
    )
    assert ok.ok is True
    assert ok.operation == "create_box"
    assert ok.parameters == {"document_id": "d", "length": 10, "width": 5, "height": 3}

    missing = translate_parameters("Part_Box", {"document_id": "d", "length": 10, "width": 5})
    assert missing.ok is False
    assert [i.code for i in missing.issues] == ["missing_parameter"]
    assert missing.issues[0].parameter == "height"


def test_translate_rejects_non_numeric_parameter() -> None:
    result = translate_parameters(
        "Part_Box", {"document_id": "d", "length": "wide", "width": 5, "height": 3}
    )
    assert result.ok is False
    assert "invalid_parameter" in {i.code for i in result.issues}


def test_translate_conditional_resolves_reference_and_requires_length() -> None:
    ok = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d", "length": 5.0},
        ReferenceContext(sketch_name="Sketch", body_name="Body"),
    )
    assert ok.ok is True
    assert ok.operation == "sketch_pad"
    assert ok.parameters["sketch_name"] == "Sketch"
    assert ok.parameters["length"] == 5.0

    # Missing length must fail; Taiga/MCP permissive defaults are NOT applied.
    missing = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d"},
        ReferenceContext(sketch_name="Sketch", body_name="Body"),
    )
    assert missing.ok is False
    assert any(i.code == "missing_parameter" and i.parameter == "length" for i in missing.issues)
    assert "length" not in missing.parameters


def test_translate_missing_and_ambiguous_profile_reference() -> None:
    missing = translate_parameters("PartDesign_Pad", {"document_id": "d", "length": 5.0})
    assert missing.ok is False
    assert any(i.code == "missing_reference" for i in missing.issues)

    ambiguous = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d", "length": 5.0},
        ReferenceContext(sketch_candidates=["Sketch", "Sketch001"]),
    )
    assert ambiguous.ok is False
    assert any(i.code == "ambiguous_reference" for i in ambiguous.issues)


def test_translate_fillet_edges_and_solid_reference() -> None:
    ok = translate_parameters(
        "PartDesign_Fillet",
        {"document_id": "d", "radius": 2.0, "edge_indices": [7, 9]},
        ReferenceContext(solid_name="Pad", body_name="Body"),
    )
    assert ok.ok is True
    assert ok.operation == "partdesign_fillet"
    assert ok.parameters["object_name"] == "Pad"
    assert ok.parameters["edge_indices"] == [7, 9]

    ambiguous = translate_parameters(
        "PartDesign_Fillet",
        {"document_id": "d", "radius": 2.0, "edge_indices": [7]},
        ReferenceContext(solid_candidates=["Pad", "Pad001"]),
    )
    assert ambiguous.ok is False
    assert any(i.code == "ambiguous_reference" for i in ambiguous.issues)

    missing = translate_parameters(
        "PartDesign_Fillet", {"document_id": "d", "radius": 2.0, "edge_indices": [7]}
    )
    assert missing.ok is False
    assert any(i.code == "missing_reference" for i in missing.issues)


def test_translate_hole_base_face_ambiguity() -> None:
    result = translate_parameters(
        "PartDesign_Hole",
        {"document_id": "d", "sketch_name": "Sketch", "diameter": 4.0, "depth": 10.0},
        ReferenceContext(solid_name="Pad", face_candidates=["Face1", "Face2"]),
    )
    assert result.ok is False
    assert any(
        i.code == "ambiguous_reference" and i.parameter == "base_face" for i in result.issues
    )


def test_translate_dimensional_constraint_requires_value() -> None:
    missing = translate_parameters(
        "Sketcher_ConstrainRadius",
        {"document_id": "d", "sketch_name": "Sketch", "first_geometry_index": 0},
    )
    assert missing.ok is False
    assert any(
        i.code == "missing_parameter" and i.parameter == "value" for i in missing.issues
    )

    ok = translate_parameters(
        "Sketcher_ConstrainRadius",
        {"document_id": "d", "sketch_name": "Sketch", "first_geometry_index": 0, "value": 3.0},
    )
    assert ok.ok is True
    assert ok.parameters["constraint_type"] == "Radius"
    assert ok.parameters["value"] == 3.0


def test_translate_non_dimensional_constraint_omits_value() -> None:
    ok = translate_parameters(
        "Sketcher_ConstrainHorizontal",
        {"document_id": "d", "sketch_name": "Sketch", "first_geometry_index": 1},
    )
    assert ok.ok is True
    assert ok.parameters["constraint_type"] == "Horizontal"
    assert "value" not in ok.parameters


def test_translate_hexagon_injects_sides() -> None:
    ok = translate_parameters(
        "Sketcher_CreateHexagon",
        {"document_id": "d", "sketch_name": "Sketch", "radius": 5.0},
    )
    assert ok.ok is True
    assert ok.operation == "sketch_add_regular_polygon"
    assert ok.parameters["sides"] == 6


def test_translate_unsupported_and_unknown_actions() -> None:
    unsupported = translate_parameters("PartDesign_Draft", {"document_id": "d"})
    assert unsupported.ok is False
    assert unsupported.issues[0].code == "unsupported_action"

    unknown = translate_parameters("Nope_Not_An_Action", {"document_id": "d"})
    assert unknown.ok is False
    assert unknown.issues[0].code == "unknown_action"

    # raise_for_errors surfaces a structured error.
    with pytest.raises(Exception) as excinfo:
        unknown.raise_for_errors()
    assert excinfo.typename == "ParameterTranslationError"


def test_translate_result_defaults_are_safe() -> None:
    # No parameters and no references: nothing is fabricated.
    result = translate_parameters("PartDesign_Pad")
    assert isinstance(result, ParameterTranslationResult)
    assert result.ok is False
    assert result.parameters == {}


# ---------------------------------------------------------------------------
# M1 — featurization-safe shape diagnostics
# ---------------------------------------------------------------------------


def _shape(**overrides: object) -> CADShapeDiagnostics:
    data: dict[str, object] = dict(
        available=True,
        valid=True,
        volume=1000.0,
        bbox=(10.0, 5.0, 3.0),
        n_faces=6,
        n_edges=12,
        n_solids=1,
        face_dirs=["+Z", "-Z"],
    )
    data.update(overrides)
    return CADShapeDiagnostics(**data)  # type: ignore[arg-type]


def _state_with_shape(shape: CADShapeDiagnostics) -> CADState:
    return CADState(
        source="test",
        doc_open=True,
        workbench="PartDesignWorkbench",
        shape=shape,
    )


def test_shape_area_is_optional() -> None:
    state = _state_with_shape(_shape(area=None))
    assert "shape.area" not in state.missing_required_fields()
    assert state.to_taiga_state_json()["shape"]["area"] is None


def test_empty_face_dirs_is_allowed() -> None:
    state = _state_with_shape(_shape(face_dirs=[]))
    assert "shape.face_dirs" not in state.missing_required_fields()
    assert state.to_taiga_state_json()["shape"]["face_dirs"] == []


def test_unavailable_shape_blocks_conversion() -> None:
    state = _state_with_shape(CADShapeDiagnostics(available=False))
    missing = state.missing_required_fields()
    for field in (
        "shape.bbox",
        "shape.valid",
        "shape.volume",
        "shape.n_faces",
        "shape.n_edges",
        "shape.n_solids",
        "shape.face_dirs",
    ):
        assert field in missing
    with pytest.raises(CADStateConversionError):
        state.to_taiga_state_json()


@pytest.mark.parametrize(
    "field",
    ["valid", "volume", "bbox", "n_faces", "n_edges", "n_solids", "face_dirs"],
)
def test_each_shape_metric_missing_blocks_conversion(field: str) -> None:
    state = _state_with_shape(_shape(**{field: None}))
    assert f"shape.{field}" in state.missing_required_fields()
    with pytest.raises(CADStateConversionError):
        state.to_taiga_state_json()


def test_converted_shape_has_no_none_metrics() -> None:
    payload = _state_with_shape(_shape()).to_taiga_state_json()
    for key in (
        "valid",
        "volume",
        "bbox",
        "n_faces",
        "n_edges",
        "n_solids",
        "face_dirs",
    ):
        assert payload["shape"][key] is not None


# ---------------------------------------------------------------------------
# M2 — fail-closed Body resolution
# ---------------------------------------------------------------------------


def _pad_refs(**kwargs: object) -> ReferenceContext:
    return ReferenceContext(sketch_name="Sketch", **kwargs)  # type: ignore[arg-type]


def test_body_explicit_is_used() -> None:
    result = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d", "length": 5.0},
        _pad_refs(body_name="Body"),
    )
    assert result.ok is True
    assert result.parameters["body_name"] == "Body"


def test_body_single_candidate_is_used() -> None:
    result = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d", "length": 5.0},
        _pad_refs(body_candidates=["Body"]),
    )
    assert result.ok is True
    assert result.parameters["body_name"] == "Body"


def test_body_missing_is_fail_closed() -> None:
    result = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d", "length": 5.0},
        _pad_refs(),
    )
    assert result.ok is False
    assert any(
        i.code == "missing_reference" and i.parameter == "body_name"
        for i in result.issues
    )


def test_body_multiple_candidates_is_ambiguous() -> None:
    result = translate_parameters(
        "PartDesign_Pad",
        {"document_id": "d", "length": 5.0},
        _pad_refs(body_candidates=["Body", "Body001"]),
    )
    assert result.ok is False
    assert any(
        i.code == "ambiguous_reference" and i.parameter == "body_name"
        for i in result.issues
    )


def test_solid_feature_mapping_requires_body() -> None:
    result = translate_parameters(
        "PartDesign_Fillet",
        {"document_id": "d", "radius": 2.0, "edge_indices": [7]},
        ReferenceContext(solid_name="Pad", edge_indices=[7]),
    )
    assert result.ok is False
    assert any(i.parameter == "body_name" for i in result.issues)


def test_create_body_is_exempt_from_body_resolution() -> None:
    result = translate_parameters("PartDesign_Body", {"document_id": "d"})
    assert result.ok is True
    assert "body_name" not in result.parameters


# ---------------------------------------------------------------------------
# M3 — semantic parameter validation (Distance / Thickness)
# ---------------------------------------------------------------------------


def test_distance_one_geometry_without_second_index() -> None:
    result = translate_parameters(
        "Sketcher_ConstrainDistance",
        {
            "document_id": "d",
            "sketch_name": "Sketch",
            "first_geometry_index": 0,
            "value": 12.0,
        },
    )
    assert result.ok is True
    assert "second_geometry_index" not in result.parameters


def test_distance_two_geometry_with_second_index() -> None:
    result = translate_parameters(
        "Sketcher_ConstrainDistance",
        {
            "document_id": "d",
            "sketch_name": "Sketch",
            "first_geometry_index": 0,
            "second_geometry_index": 1,
            "value": 12.0,
            "two_geometry": True,
        },
    )
    assert result.ok is True
    assert result.parameters["second_geometry_index"] == 1
    assert "two_geometry" not in result.parameters


def test_distance_two_geometry_without_second_index_fails() -> None:
    result = translate_parameters(
        "Sketcher_ConstrainDistance",
        {
            "document_id": "d",
            "sketch_name": "Sketch",
            "first_geometry_index": 0,
            "value": 12.0,
            "two_geometry": True,
        },
    )
    assert result.ok is False
    assert any(
        i.code == "missing_parameter" and i.parameter == "second_geometry_index"
        for i in result.issues
    )


def test_distance_second_index_must_be_numeric() -> None:
    result = translate_parameters(
        "Sketcher_ConstrainDistance",
        {
            "document_id": "d",
            "sketch_name": "Sketch",
            "first_geometry_index": 0,
            "value": 12.0,
            "second_geometry_index": "1",
        },
    )
    assert result.ok is False
    assert any(
        i.code == "invalid_parameter" and i.parameter == "second_geometry_index"
        for i in result.issues
    )


def test_thickness_faces_default_is_all_faces() -> None:
    result = translate_parameters(
        "PartDesign_Thickness",
        {"document_id": "d", "object_name": "Pad", "thickness": 2.0},
        ReferenceContext(solid_name="Pad", body_name="Body"),
    )
    assert result.ok is True
    # faces is optional; MCP's default [] is the explicit all-faces behavior.
    assert "faces" not in result.parameters


# ---------------------------------------------------------------------------
# Import isolation
# ---------------------------------------------------------------------------


def test_core_contracts_import_without_optional_dependencies() -> None:
    code = textwrap.dedent(
        """
        import sys

        BLOCK = ("FreeCAD", "torch", "freecad_s1")

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if any(name == b or name.startswith(b + ".") for b in BLOCK):
                    raise ImportError("blocked optional dependency: " + name)
                return None

        sys.meta_path.insert(0, Blocker())

        import freecad_mcp.app.taiga_contracts as m
        import freecad_mcp.app as app

        assert "FreeCAD" not in sys.modules
        assert "torch" not in sys.modules
        assert "freecad_s1" not in sys.modules
        # Contracts are usable without any optional dependency present.
        state = m.CADState(source="probe")
        assert state.missing_required_fields() == [
            "doc_open", "workbench", "shape.bbox", "shape.valid", "shape.volume",
            "shape.n_faces", "shape.n_edges", "shape.n_solids", "shape.face_dirs",
        ]
        assert app.translate_parameters("Part_Box", {"document_id": "d"}) is not None
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout