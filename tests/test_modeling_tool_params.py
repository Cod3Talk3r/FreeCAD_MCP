"""Regression test for the modeling tool parameter schemas.

Every modeling tool handler builds its bridge payload with
``"workspace": params.workspace or ""``.  If the matching ``*Params`` model does
not declare a ``workspace`` field, the handler raises
``AttributeError: '<Model>' object has no attribute 'workspace'`` before the
FreeCAD bridge is ever contacted, and the client only sees the opaque
``Error executing tool <name>``.

This test pins the contract: every parameter model used by a modeling tool must
expose an optional ``workspace`` that defaults to ``None``.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from freecad_mcp.tools import modeling

#: Every ``*Params`` model referenced by a modeling tool handler, together with
#: the minimum payload each one needs to validate.
MODELING_PARAMS_CASES: tuple[tuple[Any, dict[str, Any]], ...] = (
    (modeling.CreateDocumentParams, {"document_id": "doc"}),
    (modeling.CreateBoxParams, {"document_id": "doc"}),
    (modeling.CreateCylinderParams, {"document_id": "doc"}),
    (
        modeling.SetParameterParams,
        {"document_id": "doc", "object_name": "Box", "property_name": "Length", "value": 1.0},
    ),
    (modeling.RecomputeParams, {"document_id": "doc"}),
    (modeling.ListObjectsParams, {"document_id": "doc"}),
    (modeling.InspectObjectParams, {"document_id": "doc", "object_name": "Box"}),
    (modeling.ValidateDocParams, {"document_id": "doc"}),
    (modeling.SaveDocumentParams, {"document_id": "doc"}),
    (modeling.ExportStepParams, {"document_id": "doc"}),
    (modeling.TankParams, {"document_id": "doc"}),
    (modeling.TubularCoilParams, {"document_id": "doc"}),
)

_MODELING_PARAMS_MODELS = tuple(model for model, _ in MODELING_PARAMS_CASES)
_IDS = tuple(model.__name__ for model, _ in MODELING_PARAMS_CASES)


@pytest.mark.parametrize("params_model", _MODELING_PARAMS_MODELS, ids=_IDS)
def test_params_model_exposes_workspace_field(params_model: type) -> None:
    """A params model must declare ``workspace`` or the handler raises AttributeError."""
    assert "workspace" in params_model.model_fields, (
        f"{params_model.__name__} is missing the 'workspace' field; the matching "
        f"tool handler reads params.workspace and would raise AttributeError."
    )

    field = params_model.model_fields["workspace"]
    assert field.default is None
    assert field.annotation == Optional[str]


@pytest.mark.parametrize(
    ("params_model", "payload"),
    MODELING_PARAMS_CASES,
    ids=_IDS,
)
def test_params_model_workspace_defaults_to_none(params_model: type, payload: dict[str, Any]) -> None:
    """Omitting ``workspace`` must yield ``None`` so the bridge uses its default."""
    instance = params_model(**payload)
    assert instance.workspace is None
