# Testing

## Commands

- Full suite: `uv run pytest` (or `uv run pytest -q`).
- Coding agents should run focused tests related to changed code; full-suite
  verification is maintainer-run unless explicitly requested.
- FreeCAD-gated integration tests: `uv run pytest tests/test_freecad_integration.py -v`
  — skips when no FreeCAD console is found; set `FREECAD_MCP_FREECAD_EXECUTABLE` to override.
- M12 capability discovery (no FreeCAD needed):
  `uv run pytest tests/test_m12_capability_discovery.py tests/test_modeling_tool_params.py tests/test_modeling_schemas.py -q`
- Taiga unit tests (no Taiga/Torch/weights/network needed):
  `uv run pytest tests/test_taiga_runner.py tests/test_taiga_runtime.py tests/test_taiga_adapter.py tests/test_taiga_workflow.py tests/test_taiga_goal.py tests/test_taiga_build.py tests/test_taiga_build_tool.py tests/test_taiga_child.py tests/test_taiga_diagnostic.py tests/test_config.py tests/test_server.py -q`
- Guarded-loop skeleton tests: `uv run pytest tests/test_taiga_workflow.py -q`.
- Real Taiga/FreeCAD integration (opt-in, Windows only):
  `FREECAD_MCP_FREECAD_EXECUTABLE="E:\Softwares\FreeCAD\bin\freecadcmd.exe" FREECAD_MCP_TAIGA_SOURCE="G:\...\taiga-upstream-copy" uv run pytest tests/test_taiga_integration.py tests/test_taiga_diagnostic.py -q -rs`

## Verified results

- 2026-11-09: `uv run --offline pytest -q tests/test_runner.py -k
  partdesign_chamfer` -> **17 passed** (with fillet -> **35 passed** total).
- 2026-11-09: Windows FreeCAD 1.1.4 from WSL,
  `tests/test_freecad_integration.py -k chamfer` -> **3 passed**, with a
  Windows-visible pytest basetemp. Covers Edge7 at 3 mm, multiple edges,
  reopened Base links and Body tip, invalid inputs and disk rollback after a
  failed recompute. Manual GUI comparison remains unverified.
- 2026-11-09: `uv run --offline python -m pytest -q tests/test_app_backends.py -k
  direct_rejects_invalid_requests` -> **148 passed**, including partdesign_thickness
  validation cases (object_name, thickness, reversed, faces: duplicate, boolean,
  malformed, zero, out-of-range, non-list).
- 2026-11-09: `uv run --offline python -m pytest -q tests/test_runner.py -k
  partdesign_thickness` -> **19 passed**; Windows FreeCAD 1.1.4 from WSL,
  `tests/test_freecad_integration.py -k thickness` -> **7 passed**, with a
  Windows-visible pytest basetemp. Covers default (outward, Reversed=False,
  volume increases) and reversed (inward, Reversed=True, volume decreases)
  directions, direction-aware volume validation (inward rejects result >= base;
  outward only requires result != base), single and multiple removed faces,
  invalid face indices (out of range, zero, duplicate), valid solids and Body
  membership, save/reopen persistence with Base LinkSub intact, and disk rollback
  after a failed recompute. Manual GUI comparison remains unverified.
- 2026-11-09: `uv run --offline python -m pytest -q tests/test_runner.py -k
  sketch_revolution` -> **9 passed** (mock: successful rectangle/circle, all
  three axes, partial angle, reuses existing body, missing sketch, wrong object
  type, invalid axis, invalid angle zero/negative/non-finite/>360, empty name,
  handle_request dispatch). `tests/test_app_backends.py -k
  direct_rejects_invalid_requests` -> **148 passed**, including sketch_revolution
  validation cases (sketch_name empty, axis invalid, angle nan/inf/zero/negative/>360/non-finite).
  Real FreeCAD 1.1.4 from WSL: error-handling tests pass (invalid axis, invalid
  angle, missing sketch -> **3 passed**). Successful revolution creation tests
  (default Z_Axis, X_Axis, Y_Axis, partial angle, save/reopen) fail on real
  FreeCAD due to a FreeCAD 1.1.4 Python API limitation:
  `PartDesign::Revolution.ReferenceAxis` set to origin axes produces NULL shape
  ("Reference axis is invalid", Axis=Vector(0,0,0)). The property is accepted
  without error but the computed axis is invalid. Mock tests pass fully.
- 2026-11-09: `uv run --offline python -m pytest -q tests/test_runner.py -k
  partdesign_mirrored` -> **9 passed**; Windows FreeCAD 1.1.4 from WSL,
  `tests/test_freecad_integration.py -k mirrored` -> **4 passed**, with a
  Windows-visible pytest basetemp. Covers XY_Plane, XZ_Plane, YZ_Plane, valid
  solids and volume scaling, Body membership, invalid inputs and disk rollback
  after a failed recompute. Manual GUI comparison remains unverified.
- 2026-11-09: `uv run --offline pytest -q tests/test_runner.py -k
  partdesign_linear_pattern` -> **11 passed**; Windows FreeCAD 1.1.4 from WSL,
  `tests/test_freecad_integration.py -k linear_pattern` -> **4 passed**, with a
  Windows-visible pytest basetemp. Covers X_Axis, Y_Axis, Z_Axis directions,
  valid solids and volume scaling, Body membership and Body.Tip, invalid inputs
  and disk rollback after a failed recompute. Manual GUI comparison remains
  unverified.
- 2026-11-09: `uv run --offline python -m pytest -q tests/test_runner.py -k
  partdesign_polar_pattern` -> **12 passed**; Windows FreeCAD 1.1.4 from WSL,
  `tests/test_freecad_integration.py -k polar_pattern` -> **6 passed**, with a
  Windows-visible pytest basetemp. Covers X_Axis, Y_Axis, Z_Axis directions,
  360-degree full rotation, 45-degree partial angle, valid solids and volume
  scaling, Body membership and Body.Tip, invalid inputs and disk rollback after a
  failed recompute. Manual GUI comparison remains unverified.
- 2026-11-09: `uv run --offline pytest -q tests/test_runner.py -k
  partdesign_fillet` -> **18 passed**; Windows FreeCAD 1.1.4 from WSL,
  `tests/test_freecad_integration.py -k fillet` -> **4 passed**, with a
  Windows-visible pytest basetemp. Covers Edge7 at 3 mm, multiple edges,
  reopened Base links and Body tip, invalid inputs and disk rollback after a
  failed recompute. Manual GUI comparison remains unverified.
- 2026-11-07 (Linux, Python 3.11.18): focused `tests/test_runner.py -k
  "sketch_edit_ellipse or inspect_sketch"` -> **26 passed**; ellipse schema
  tests -> **5 passed**; real FreeCAD 1.1.4 integration
  `tests/test_freecad_integration.py -k "sketch_edit_ellipse"` -> **2 passed**
  (launched Windows `freecadcmd.exe` from WSL via
  `FREECAD_MCP_FREECAD_EXECUTABLE=/mnt/e/Softwares/FreeCAD/bin/freecadcmd.exe`).
  Full suite intentionally not run for M9-D2-B3.
- 2026-11-07 (Linux, Python 3.11.18): `uv run pytest -q -rs` -> **521 passed,
  40 skipped** after sketch line-editing fix. Real FreeCAD 1.1.4 integration
  `tests/test_freecad_integration.py -k "sketch_edit_line"` -> **2 passed**
  (launched Windows `freecadcmd.exe` from WSL; Windows-visible temp path via
  `/tmp/freecadtest` symlink).
- 2026-11-04 (Linux, Python 3.11.18): `uv run pytest -q -rs` -> **385 passed,
  9 skipped** after Phase 4 M2 document & model inspection. Windows FreeCAD 1.1.4
  launched from WSL passed direct ping, version, and a temporary document/tank/
  reopen diagnostic. Native Windows Python and the full real FreeCAD integration
  suite remain unverified.
- 2026-11-04 (Linux): `uv run --no-sync pytest -q -rs` → **362 passed, 9 skipped**
  after Phase 4 M1 application-layer changes. Real FreeCAD/Taiga integration
  was not configured for this run.
- 2026-11-03 (this environment, Linux): `uv run pytest -q` → **335 passed, 9 skipped**
  in ~31 s. Skips: FreeCAD integration tests (console not found) and real-Taiga
  integration tests (env not configured).
- Real Windows integration (FreeCAD 1.1.4, Taiga upstream
  `84fdd19c`): `tests/test_taiga_integration.py` → **2 passed**.

## Limitations

- FreeCAD-dependent and real-Taiga tests skip when their runtime is not configured.
- Never record unverified results as successful.

- 2026-11-11 (M12): `uv run --offline pytest -q tests/test_m12_capability_discovery.py tests/test_modeling_tool_params.py tests/test_modeling_schemas.py` -> **87 passed**.
  - Operation/tool registration parity verified: all 45 operations in `runner.OPERATIONS` have corresponding MCP tools (4 diagnostic ops intentionally not tool-exposed: ping, get_version, validate→validate_document alias, delete_object not yet wired).
  - All 41 `*Params` models verified to expose optional `workspace` field defaulting to None.
  - Unknown operations rejected with clear error.
  - All tools have non-empty descriptions and object-type input schemas.

## Evidence and milestone acceptance

- Unit/schema/backend tests establish project behavior and contracts only; mocked
  FreeCAD APIs are not evidence that a native feature works.
- Real FreeCAD 1.1.4 tests must create the native feature in `freecadcmd` or GUI,
  recompute, verify expected non-null valid geometry and relevant document
  invariants, and cover structured failure behavior.
- Parametric/persistent features require save-close-reopen-recompute and an
  actual parameter edit after reopening. Verify Body membership, `Body.Tip`,
  links and topology references where relevant.
- GUI interaction requires a manual or automated real-GUI check; console tests do
  not prove `FreeCADGui`, selection, panel or workbench behavior.
- MCP end-to-end tests must discover schemas and exercise representative
  create/inspect/edit/validate/save sequences through stdio and the child bridge.
- Taiga workflow fakes prove loop mechanics only. Real Method B requires explicit
  configured runtime/model and actual FreeCAD state/action/execution feedback;
  model downloads must never happen implicitly.
- Roadmap milestone acceptance criteria are in `docs/ROADMAP.md`; report
  unavailable runtime tests as skips/unknown, never as passing evidence.

---
