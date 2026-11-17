"""Parent-side Method A workflow helpers for M19.

Provides concise model-state snapshots and normalized artifact/revision
references (P2) and deterministic multi-step workflow execution (P3)
by reusing existing bridge operations and workspace metadata.
This module must remain FreeCAD-free (no FreeCAD/Part/FreeCADGui imports).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from freecad_mcp.freecad_bridge import workspace as ws_mod
from freecad_mcp.freecad_bridge.runner import OPERATIONS

from .workflow_contracts import (
    DocumentArtifactRef,
    ModelStateReference,
    ModelStateSnapshot,
    ModelStateSummary,
    ObjectStateSummary,
    WorkflowResult,
    WorkflowStepRequest,
    WorkflowStepResult,
)

#: Maximum number of per-object summaries included in a snapshot.
MAX_SNAPSHOT_OBJECTS = 50

#: Operation names used for validate-after behavior (runner operation names).
_VALIDATE_OPS = {"validate"}

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
        raw = op_runner("validate", common_params)
        validate_result = dict(raw) if isinstance(raw, Mapping) else None
    except Exception as exc:  # noqa: BLE001 - surface as unavailable info
        notes.append(f"validate unavailable: {type(exc).__name__}: {exc}")

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

def execute_workflow(
    *,
    document_id: str,
    steps: list[WorkflowStepRequest],
    workspace: str | None = None,
    op_runner: OpRunner,
) -> WorkflowResult:
    """Execute an ordered list of allowlisted operations sequentially (M19 P3).

    Reuses ``op_runner`` (DirectBackend/bridge execution path) for operations and
    validation/inspection helpers. Stops on first step failure by default. Later
    steps are recorded as skipped and not executed.
    """
    try:
        normalized_doc = _normalized_document_id(document_id)
    except Exception as exc:  # noqa: BLE001
        return WorkflowResult(
            ok=False,
            status="failed",
            document_id=str(document_id),
            workspace_root=None,
            total_steps=len(steps),
            completed_steps=0,
            steps=[],
            error={"message": str(exc), "error_type": type(exc).__name__},
            artifact_ref=None,
        )

    wks = _resolve_workspace(workspace)
    workspace_root = str(wks.root.resolve())
    ws_arg = workspace or ""
    step_results: list[WorkflowStepResult] = []
    failed = False
    completed_steps = 0

    for idx, step in enumerate(steps):
        if failed:
            step_results.append(
                WorkflowStepResult(
                    step_index=idx,
                    operation=step.operation,
                    label=step.label,
                    status="skipped",
                    ok=False,
                    result=None,
                    error={
                        "message": "skipped due to earlier workflow step failure",
                        "error_type": "SkippedStep",
                    },
                    inspection=None,
                    validation=None,
                    artifact_ref=None,
                    state_ref=None,
                )
            )
            continue

        # Build effective params with consistent workflow document/workspace context.
        params = dict(step.parameters or {})
        if "document_id" in params and params["document_id"] not in (None, "", normalized_doc):
            msg = (
                f"step parameter document_id {params['document_id']!r} conflicts with "
                f"workflow document_id {normalized_doc!r}"
            )
            step_results.append(
                WorkflowStepResult(
                    step_index=idx,
                    operation=step.operation,
                    label=step.label,
                    status="failed",
                    ok=False,
                    result=None,
                    error={"message": msg, "error_type": "ValueError"},
                )
            )
            failed = True
            continue
        # Do not force document/workspace into ops that may not accept them, except
        # preserve if caller provided; for ops that take document context, ensure
        # consistency when operation name is known to use document_id.
        if "document_id" in params or step.operation in OPERATIONS:
            params.setdefault("document_id", normalized_doc)
        if "workspace" in params or step.operation in OPERATIONS:
            params.setdefault("workspace", ws_arg)

        if step.operation not in OPERATIONS:
            step_results.append(
                WorkflowStepResult(
                    step_index=idx,
                    operation=step.operation,
                    label=step.label,
                    status="failed",
                    ok=False,
                    result=None,
                    error={
                        "message": f"unknown or non-allowlisted operation: {step.operation!r}",
                        "error_type": "UnknownOperation",
                    },
                )
            )
            failed = True
            continue

        # Execute operation via existing execution path.
        op_result: dict[str, Any] | None = None
        try:
            raw = op_runner(step.operation, params)
            op_result = dict(raw) if isinstance(raw, Mapping) else {"value": raw}
        except Exception as exc:  # noqa: BLE001
            step_results.append(
                WorkflowStepResult(
                    step_index=idx,
                    operation=step.operation,
                    label=step.label,
                    status="failed",
                    ok=False,
                    result=None,
                    error={"message": str(exc), "error_type": type(exc).__name__},
                )
            )
            failed = True
            continue

        # validate_after: honor if requested, report separately from operation success.
        validation_payload: dict[str, Any] | None = None
        validation_failed = False
        if step.validate_after:
            try:
                vraw = op_runner(
                    "validate",
                    {"document_id": normalized_doc, "workspace": ws_arg},
                )
                vdict = dict(vraw) if isinstance(vraw, Mapping) else {"value": vraw}
                validation_payload = {"ok": True, "result": vdict}
            except Exception as exc:  # noqa: BLE001
                validation_failed = True
                validation_payload = {
                    "ok": False,
                    "error": {"message": str(exc), "error_type": type(exc).__name__},
                    "operation_succeeded": True,
                }

        # inspect_after: reuse existing read-only snapshot/state functionality.
        inspection_payload: dict[str, Any] | None = None
        inspection_failed = False
        artifact_ref = None
        state_ref = None
        inspect_requested = bool(step.inspect_after)
        if inspect_requested:
            try:
                snapshot = build_model_state_snapshot(
                    normalized_doc, workspace=workspace, op_runner=op_runner
                )
                artifact_ref = snapshot.artifact_ref.model_dump(mode="json")
                state_ref = snapshot.state_ref.model_dump(mode="json")
                inspection_payload = {
                    "ok": True,
                    "snapshot": snapshot.model_dump(mode="json"),
                }
                # Snapshot collection is resilient and records unavailable ops in notes;
                # if core inspection operations were unavailable, treat as inspection failure.
                unavailable_notes = [
                    n for n in snapshot.summary.notes if " unavailable:" in n
                ]
                if unavailable_notes:
                    inspection_failed = True
                    inspection_payload = {
                        "ok": False,
                        "error": {
                            "message": "; ".join(unavailable_notes),
                            "error_type": "InspectionUnavailable",
                        },
                        "operation_succeeded": True,
                        "snapshot": snapshot.model_dump(mode="json"),
                    }
                    artifact_ref = None
                    state_ref = None
                # If caller requested specific inspection operations (list), run them too.
                # Core snapshot operations are always collected once above; do not
                # duplicate them when requested explicitly in inspect_after.
                if not inspection_failed and isinstance(step.inspect_after, list):
                    core_snapshot_ops = {"list_objects", "get_document_hierarchy", "validate"}
                    requested_ops: dict[str, Any] = {}
                    for insp_op in step.inspect_after:
                        if not isinstance(insp_op, str):
                            raise TypeError(f"inspect_after entries must be strings: {insp_op!r}")
                        if insp_op not in OPERATIONS:
                            raise ValueError(f"unknown inspect_after operation: {insp_op!r}")
                        if insp_op in core_snapshot_ops:
                            continue
                        iraw = op_runner(
                            insp_op,
                            {"document_id": normalized_doc, "workspace": ws_arg},
                        )
                        requested_ops[insp_op] = dict(iraw) if isinstance(iraw, Mapping) else iraw
                    if requested_ops:
                        inspection_payload["operations"] = requested_ops
            except Exception as exc:  # noqa: BLE001
                inspection_failed = True
                inspection_payload = {
                    "ok": False,
                    "error": {"message": str(exc), "error_type": type(exc).__name__},
                    "operation_succeeded": True,
                }
                artifact_ref = None
                state_ref = None

        if validation_failed or inspection_failed:
            # Distinguish validation/inspection failure from operation failure.
            err = (
                (validation_payload or {}).get("error")
                if validation_failed
                else (inspection_payload or {}).get("error")
            )
            step_results.append(
                WorkflowStepResult(
                    step_index=idx,
                    operation=step.operation,
                    label=step.label,
                    status="failed",
                    ok=False,
                    result=op_result,
                    error={
                        "message": (err or {}).get("message", "post-step check failed"),
                        "error_type": (err or {}).get(
                            "error_type",
                            "ValidationAfterError" if validation_failed else "InspectionAfterError",
                        ),
                        "operation_succeeded": True,
                        "validation_failed": validation_failed,
                        "inspection_failed": inspection_failed,
                    },
                    inspection=inspection_payload,
                    validation=validation_payload,
                    artifact_ref=artifact_ref,
                    state_ref=state_ref,
                )
            )
            failed = True
            continue

        step_results.append(
            WorkflowStepResult(
                step_index=idx,
                operation=step.operation,
                label=step.label,
                status="completed",
                ok=True,
                result=op_result,
                error=None,
                inspection=inspection_payload,
                validation=validation_payload,
                artifact_ref=artifact_ref,
                state_ref=state_ref,
            )
        )
        completed_steps += 1

    overall_ok = (not failed) and completed_steps == len(steps)
    overall_status = "completed" if overall_ok else "failed"

    final_artifact = None
    try:
        final_artifact = build_document_artifact_ref(
            normalized_doc, workspace=workspace
        ).model_dump(mode="json")
    except Exception:  # noqa: BLE001
        final_artifact = None

    top_error = None
    if not overall_ok:
        # Surface first failing step error for convenience.
        for sr in step_results:
            if sr.status == "failed" and sr.error:
                top_error = dict(sr.error)
                break

    return WorkflowResult(
        ok=overall_ok,
        status=overall_status,
        document_id=normalized_doc,
        workspace_root=workspace_root,
        total_steps=len(steps),
        completed_steps=completed_steps,
        steps=step_results,
        error=top_error,
        artifact_ref=final_artifact,
    )