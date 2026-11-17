"""Parent-side Method A workflow helpers for M19 P2.

Provides concise model-state snapshots and normalized artifact/revision
references by reusing existing bridge operations and workspace metadata.
This module must remain FreeCAD-free (no FreeCAD/Part/FreeCADGui imports).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from freecad_mcp.freecad_bridge import workspace as ws_mod

from .workflow_contracts import (
    DocumentArtifactRef,
    ModelStateReference,
    ModelStateSnapshot,
    ModelStateSummary,
    ObjectStateSummary,
)

#: Maximum number of per-object summaries included in a snapshot.
MAX_SNAPSHOT_OBJECTS = 50

OpRunner = Callable[[str, dict[str, Any]], Mapping[str, Any] | dict[str, Any]]


def _resolve_workspace(workspace: str | None) -> ws_mod.Workspace:
    if workspace:
        return ws_mod.Workspace(root=Path(os.path.normpath(workspace)).resolve())
    return ws_mod.Workspace.from_env()


def _normalized_document_id(document_id: str) -> str:
    return ws_mod.validate_document_id(document_id)


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def build_document_artifact_ref(
    document_id: str, *, workspace: str | None = None
) -> DocumentArtifactRef:
    """Build normalized artifact/revision references from workspace metadata/files."""
    normalized = _normalized_document_id(document_id)
    wks = _resolve_workspace(workspace)
    root = str(wks.root.resolve())

    fcstd_path = str(wks.document_path(normalized))
    meta_path = str(wks.meta_path(normalized))
    fcstd_exists = Path(fcstd_path).is_file()
    meta_exists = Path(meta_path).is_file()
    meta = ws_mod.read_meta(Path(meta_path)) if meta_exists else {}

    snapshots = ws_mod.list_snapshots(wks, normalized)
    snapshot_count = len(snapshots)
    latest_snapshot_revision = None
    latest_snapshot_path = None
    if snapshots:
        latest = snapshots[-1]
        latest_snapshot_revision = _to_int(latest.get("revision"))
        latest_snapshot_path = latest.get("snapshot_path")

    return DocumentArtifactRef(
        document_id=normalized,
        workspace_root=root,
        fcstd_path=fcstd_path,
        fcstd_exists=fcstd_exists,
        meta_path=meta_path,
        meta_exists=meta_exists,
        revision=_to_int(meta.get("revision")) if meta_exists else None,
        created_at=meta.get("created_at") if meta_exists else None,
        last_saved_at=meta.get("last_saved_at") if meta_exists else None,
        last_modified_at=meta.get("last_modified_at") if meta_exists else None,
        latest_snapshot_revision=latest_snapshot_revision,
        latest_snapshot_path=latest_snapshot_path,
        snapshot_count=snapshot_count,
    )


def _sorted_objects(raw_objects: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_objects, list):
        return []
    items = [obj for obj in raw_objects if isinstance(obj, dict)]
    return sorted(items, key=lambda obj: str(obj.get("name", "")))


def _validity_map(validate_result: dict[str, Any] | None) -> dict[str, bool]:
    if not validate_result:
        return {}
    raw = validate_result.get("objects")
    if not isinstance(raw, list):
        return {}
    out: dict[str, bool] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        valid = item.get("valid")
        if isinstance(name, str) and isinstance(valid, bool):
            out[name] = valid
    return out


def build_model_state_snapshot(
    document_id: str,
    *,
    workspace: str | None = None,
    op_runner: OpRunner,
) -> ModelStateSnapshot:
    """Collect a concise, bounded model-state snapshot using existing operations.

    ``op_runner(operation, parameters)`` must execute an allowlisted bridge/runner
    operation and return its payload dict (same shape as DirectBackend data).
    """
    normalized = _normalized_document_id(document_id)
    wks = _resolve_workspace(workspace)
    workspace_root = str(wks.root.resolve())
    collected_at = datetime.now(timezone.utc).isoformat()
    notes: list[str] = []

    common_params: dict[str, Any] = {"document_id": normalized, "workspace": workspace or ""}

    list_result: dict[str, Any] | None = None
    hierarchy_result: dict[str, Any] | None = None
    validate_result: dict[str, Any] | None = None

    try:
        raw = op_runner("list_objects", common_params)
        list_result = dict(raw) if isinstance(raw, Mapping) else None
    except Exception as exc:  # noqa: BLE001 - surface as unavailable info
        notes.append(f"list_objects unavailable: {type(exc).__name__}: {exc}")

    try:
        raw = op_runner("get_document_hierarchy", common_params)
        hierarchy_result = dict(raw) if isinstance(raw, Mapping) else None
    except Exception as exc:  # noqa: BLE001 - surface as unavailable info
        notes.append(f"get_document_hierarchy unavailable: {type(exc).__name__}: {exc}")

    try:
        raw = op_runner("validate_document", common_params)
        validate_result = dict(raw) if isinstance(raw, Mapping) else None
    except Exception as exc:  # noqa: BLE001 - surface as unavailable info
        notes.append(f"validate_document unavailable: {type(exc).__name__}: {exc}")

    # Build artifact ref after live inspection attempts so persisted metadata reflects
    # current workspace sidecar state (e.g. revision changes). This does not imply
    # unsaved in-memory changes were persisted.
    artifact_ref = build_document_artifact_ref(normalized, workspace=workspace)

    objects_raw = _sorted_objects(list_result.get("objects") if list_result else None)
    validity = _validity_map(validate_result)
    summaries: list[ObjectStateSummary] = []
    for obj in objects_raw[:MAX_SNAPSHOT_OBJECTS]:
        name = str(obj.get("name", ""))
        summaries.append(
            ObjectStateSummary(
                name=name,
                type=str(obj.get("type", "")),
                label=obj.get("label") if isinstance(obj.get("label"), str) else None,
                shape_type=obj.get("shape_type") if isinstance(obj.get("shape_type"), str) else None,
                volume=float(obj["volume"]) if isinstance(obj.get("volume"), (int, float)) and not isinstance(obj.get("volume"), bool) else None,
                valid=validity.get(name),
            )
        )

    truncated = len(objects_raw) > MAX_SNAPSHOT_OBJECTS
    if truncated:
        notes.append(
            f"object summaries truncated to {MAX_SNAPSHOT_OBJECTS} of {len(objects_raw)} objects"
        )

    hierarchy_available = isinstance(hierarchy_result, dict) and isinstance(
        hierarchy_result.get("hierarchy"), list
    )
    root_hierarchy_count = (
        len(hierarchy_result.get("hierarchy", [])) if hierarchy_available and hierarchy_result else 0
    )
    if not hierarchy_available:
        notes.append("ownership hierarchy unavailable")

    validation_available = isinstance(validate_result, dict) and "all_valid" in validate_result
    all_valid = (
        validate_result.get("all_valid")
        if validation_available and isinstance(validate_result.get("all_valid"), bool)
        else None
    )
    if not validation_available:
        notes.append("validation results unavailable")

    if list_result is None:
        notes.append("object listing unavailable")
        object_count = 0
    else:
        object_count = len(objects_raw)

    # Do not populate live snapshot revision from persisted sidecar or list_objects meta:
    # existing mechanisms cannot establish that the live in-memory snapshot corresponds
    # to an exact persisted revision (unsaved changes may exist).
    if artifact_ref.revision is not None or (
        list_result
        and isinstance(list_result.get("meta"), dict)
        and _to_int(list_result.get("meta", {}).get("revision")) is not None
    ):
        notes.append(
            "live snapshot revision not independently established from persisted metadata"
        )
    revision = None

    state_ref = ModelStateReference(
        document_id=normalized,
        workspace_root=workspace_root,
        revision=revision,
        freshness="live_inspection",
        collected_at=collected_at,
    )

    summary = ModelStateSummary(
        object_count=object_count,
        root_hierarchy_count=root_hierarchy_count,
        all_valid=all_valid,
        objects=summaries,
        hierarchy_available=hierarchy_available,
        validation_available=validation_available,
        truncated=truncated,
        notes=sorted(set(notes)),
    )

    return ModelStateSnapshot(
        document_id=normalized,
        workspace_root=workspace_root,
        state_ref=state_ref,
        artifact_ref=artifact_ref,
        summary=summary,
    )