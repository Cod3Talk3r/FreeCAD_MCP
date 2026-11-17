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
    """Execute an ordered list of allowlisted operations sequentially (M19 P3/P4).

    Reuses ``op_runner`` (DirectBackend/bridge execution path) for operations and
    validation/inspection helpers. Stops on first step failure by default. Later
    steps are recorded as skipped and not executed.

    When a step has ``recover_on_failure=True``, the step first saves the document
    and creates a pre-step snapshot. If the operation then fails, that exact
    snapshot is restored and verified. Recovery only triggers for operation
    failures (not validation/inspection failures). A recovered step has
    ``status="recovered"`` and ``ok=True`` but is not counted in
    ``completed_steps``. The overall workflow status is ``"recovered"`` if at
    least one step was recovered and no later steps failed; otherwise
    ``"completed"`` if all steps completed, or ``"failed"`` if any step failed
    without recovery.
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
    any_recovered = False
    actual_failure = False  # True if a step genuinely failed (not recovery stop)

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
                    recovery=None,
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
                    recovery=None,
                )
            )
            failed = True
            actual_failure = True
            continue
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
                    recovery=None,
                )
            )
            failed = True
            actual_failure = True
            continue

        # Pre-step preparation for recovery-enabled steps.
        pre_step_snapshot_revision: int | None = None
        if step.recover_on_failure:
            # 1. Save the document to ensure current live state is persisted.
            save_result = op_runner("save_document", {"document_id": normalized_doc, "workspace": ws_arg})
            if not save_result.get("ok"):
                step_results.append(
                    WorkflowStepResult(
                        step_index=idx,
                        operation=step.operation,
                        label=step.label,
                        status="failed",
                        ok=False,
                        result=None,
                        error={
                            "message": f"pre-step save failed: {save_result.get('error', 'unknown error')}",
                            "error_type": "PreStepSaveFailed",
                        },
                        recovery={
                            "attempted": False,
                            "target_revision": None,
                            "restoration_ok": False,
                            "validation_ok": False,
                            "preservation_snapshot_revision": None,
                            "error": "pre-step save failed",
                        },
                    )
                )
                failed = True
                actual_failure = True
                continue

            # 2. Create a snapshot at the new persisted revision.
            snapshot_result = op_runner("create_snapshot", {"document_id": normalized_doc, "workspace": ws_arg})
            if not snapshot_result.get("ok"):
                step_results.append(
                    WorkflowStepResult(
                        step_index=idx,
                        operation=step.operation,
                        label=step.label,
                        status="failed",
                        ok=False,
                        result=None,
                        error={
                            "message": f"pre-step snapshot failed: {snapshot_result.get('error', 'unknown error')}",
                            "error_type": "PreStepSnapshotFailed",
                        },
                        recovery={
                            "attempted": False,
                            "target_revision": None,
                            "restoration_ok": False,
                            "validation_ok": False,
                            "preservation_snapshot_revision": None,
                            "error": "pre-step snapshot failed",
                        },
                    )
                )
                failed = True
                actual_failure = True
                continue

            # 3. Capture the exact revision from the snapshot creation response.
            pre_step_snapshot_revision = snapshot_result.get("result", {}).get("revision")
            if pre_step_snapshot_revision is None:
                # Fallback: try to get from top-level if structure differs
                pre_step_snapshot_revision = snapshot_result.get("revision")

        # Execute operation via existing execution path.
        op_result: dict[str, Any] | None = None
        op_error: dict[str, Any] | None = None
        operation_succeeded = True  # Assume success unless exception or explicit ok=False
        try:
            raw = op_runner(step.operation, params)
            op_result = dict(raw) if isinstance(raw, Mapping) else {"value": raw}
            # Support both envelope format (with "ok" field) and direct result format.
            if "ok" in op_result:
                operation_succeeded = bool(op_result.get("ok"))
                if not operation_succeeded:
                    op_error = op_result.get("error", {"message": "operation failed", "error_type": "OperationFailed"})
        except Exception as exc:  # noqa: BLE001
            operation_succeeded = False
            op_error = {"message": str(exc), "error_type": type(exc).__name__}
            op_result = None

        # Recovery path: only for operation failures when recover_on_failure is True.
        recovery_info: dict[str, Any] | None = None
        if not operation_succeeded and step.recover_on_failure and pre_step_snapshot_revision is not None:
            # Attempt recovery by restoring the pre-step snapshot.
            restore_result = op_runner(
                "restore_snapshot",
                {"document_id": normalized_doc, "revision": pre_step_snapshot_revision, "workspace": ws_arg},
            )
            restoration_ok = restore_result.get("ok", False)
            validation_ok = False
            preservation_rev = None
            recovery_error = None

            if restoration_ok:
                # restore_snapshot already performs internal reopen validation.
                # Run additional workflow-level verification: fresh state snapshot + validate.
                try:
                    verify_snapshot = build_model_state_snapshot(
                        normalized_doc, workspace=workspace, op_runner=op_runner
                    )
                    # Check that core inspection operations were available and validation passed.
                    verify_notes = verify_snapshot.summary.notes
                    unavailable = [n for n in verify_notes if " unavailable:" in n]
                    if unavailable:
                        validation_ok = False
                        recovery_error = f"post-recovery verification unavailable: {'; '.join(unavailable)}"
                    else:
                        # Run validate to confirm geometric validity.
                        vraw = op_runner("validate", {"document_id": normalized_doc, "workspace": ws_arg})
                        vdict = dict(vraw) if isinstance(vraw, Mapping) else {}
                        # Handle envelope format: {ok: true, result: {all_valid: ...}}
                        if vdict.get("ok") is False:
                            validation_ok = False
                            recovery_error = f"post-recovery validate failed: {vdict.get('error', 'unknown error')}"
                        else:
                            result_data = vdict.get("result", vdict)
                            validation_ok = result_data.get("all_valid", False)
                            if not validation_ok:
                                failures = result_data.get("objects", [])
                                fail_msgs = [f"{o.get('name')}: {o.get('valid')}" for o in failures if not o.get("valid")]
                                recovery_error = f"post-recovery validation failed: {'; '.join(fail_msgs) or 'invalid geometry'}"
                except Exception as exc:  # noqa: BLE001
                    validation_ok = False
                    recovery_error = f"post-recovery verification error: {exc}"

                preservation_snapshot = restore_result.get("result", {}).get("preservation_snapshot")
                if preservation_snapshot:
                    preservation_rev = preservation_snapshot.get("revision")

            if not restoration_ok:
                recovery_error = restore_result.get("error", "restore failed")

            recovery_info = {
                "attempted": True,
                "target_revision": pre_step_snapshot_revision,
                "restoration_ok": restoration_ok,
                "validation_ok": validation_ok,
                "preservation_snapshot_revision": preservation_rev,
                "error": recovery_error,
            }

            if restoration_ok and validation_ok:
                # Recovery successful: mark as recovered, do not count as completed.
                any_recovered = True
                step_results.append(
                    WorkflowStepResult(
                        step_index=idx,
                        operation=step.operation,
                        label=step.label,
                        status="recovered",
                        ok=True,
                        result=op_result,  # Original failed operation result
                        error=op_error,    # Preserve original operation error
                        inspection=None,
                        validation=None,
                        artifact_ref=None,
                        state_ref=None,
                        recovery=recovery_info,
                    )
                )
                # Handle recovery_action: stop (default) or continue to next step.
                if step.recovery_action == "stop":
                    # Stop workflow after successful recovery; do not mark as actual failure.
                    # Subsequent steps will be skipped.
                    failed = True
                    stop_after_recovery = True
                else:
                    stop_after_recovery = False
                if stop_after_recovery:
                    # Mark remaining steps as skipped
                    for remaining_idx in range(idx + 1, len(steps)):
                        remaining_step = steps[remaining_idx]
                        step_results.append(
                            WorkflowStepResult(
                                step_index=remaining_idx,
                                operation=remaining_step.operation,
                                label=remaining_step.label,
                                status="skipped",
                                ok=False,
                                result=None,
                                error={
                                    "message": "skipped due to earlier workflow step recovery (stop)",
                                    "error_type": "SkippedStep",
                                },
                                inspection=None,
                                validation=None,
                                artifact_ref=None,
                                state_ref=None,
                                recovery=None,
                            )
                        )
                    break
                continue
            else:
                # Recovery failed: mark step as failed with recovery details.
                step_results.append(
                    WorkflowStepResult(
                        step_index=idx,
                        operation=step.operation,
                        label=step.label,
                        status="failed",
                        ok=False,
                        result=op_result,
                        error={
                            "message": op_error.get("message", "operation failed") if op_error else "operation failed",
                            "error_type": op_error.get("error_type", "OperationFailed") if op_error else "OperationFailed",
                            "recovery_failed": True,
                            "recovery_error": recovery_error,
                        },
                        inspection=None,
                        validation=None,
                        artifact_ref=None,
                        state_ref=None,
                        recovery=recovery_info,
                    )
                )
                failed = True
                actual_failure = True
                continue

        # Non-recovery path (or recovery not enabled/triggered).
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
                # The validate operation returns an envelope with ok and result containing all_valid.
                # Check if the operation itself succeeded and if validation passed.
                if vdict.get("ok") is False:
                    validation_failed = True
                    validation_payload = {
                        "ok": False,
                        "error": vdict.get("error", {"message": "validate failed", "error_type": "ValidateError"}),
                        "operation_succeeded": operation_succeeded,
                    }
                else:
                    result_data = vdict.get("result", vdict)
                    all_valid = result_data.get("all_valid", True)
                    if not all_valid:
                        validation_failed = True
                        validation_payload = {
                            "ok": True,
                            "result": result_data,
                            "validation_failed": True,
                        }
                    else:
                        validation_payload = {"ok": True, "result": result_data}
            except Exception as exc:  # noqa: BLE001
                validation_failed = True
                validation_payload = {
                    "ok": False,
                    "error": {"message": str(exc), "error_type": type(exc).__name__},
                    "operation_succeeded": operation_succeeded,
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
                        "operation_succeeded": operation_succeeded,
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
                    "operation_succeeded": operation_succeeded,
                }
                artifact_ref = None
                state_ref = None

        # Determine step outcome.
        if not operation_succeeded:
            # Operation failed and no recovery (or recovery not enabled).
            step_results.append(
                WorkflowStepResult(
                    step_index=idx,
                    operation=step.operation,
                    label=step.label,
                    status="failed",
                    ok=False,
                    result=op_result,
                    error={
                        "message": op_error.get("message", "operation failed") if op_error else "operation failed",
                        "error_type": op_error.get("error_type", "OperationFailed") if op_error else "OperationFailed",
                    },
                    inspection=None,
                    validation=None,
                    artifact_ref=None,
                    state_ref=None,
                    recovery=recovery_info,
                )
            )
            failed = True
            actual_failure = True
            continue

        if validation_failed or inspection_failed:
            # Validation/inspection failure (recovery not triggered for these by default).
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
                        "operation_succeeded": operation_succeeded,
"validation_failed": validation_failed,
                        "inspection_failed": inspection_failed,
                    },
                    inspection=inspection_payload,
                    validation=validation_payload,
                    artifact_ref=artifact_ref,
                    state_ref=state_ref,
                    recovery=None,
                )
            )
            failed = True
            actual_failure = True
            continue

        # Step completed successfully.
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
                recovery=None,
            )
        )
        completed_steps += 1

    # Determine overall workflow outcome.
    # P3 semantics: completed_steps counts ONLY status == "completed".
    # P4: recovered steps have ok=True but are NOT counted in completed_steps.
    if actual_failure:
        overall_ok = False
        overall_status = "failed"
    elif any_recovered:
        overall_ok = False  # Workflow did not fully complete; at least one step was recovered.
        overall_status = "recovered"
    else:
        overall_ok = True
        overall_status = "completed"

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