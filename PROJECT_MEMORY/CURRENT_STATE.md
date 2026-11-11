# Current State

## Latest verified state

- HEAD: `44f994e` — feat(sketch): add geometry deletion (branch `feat/freecad-mcp`).
- Working tree contains uncommitted sketch geometry tools, the verified
  `sketch_edit_line` fix, and the verified `sketch_edit_ellipse` orientation
  fix; do not stage or commit without explicit request.
- Phase 4 M1 (shared application backend layer) and M2 (document & model
  inspection) are committed.
- Full test suite verified locally (Python 3.11.18): `uv run pytest -q -rs` →
  **385 passed, 9 skipped** (~31 s). Skips are FreeCAD-console-missing and
  opt-in real-Taiga integration tests; no FreeCAD / Taiga runtime available in
  this Linux environment.
- Real Windows integration test (`tests/test_taiga_integration.py`): **2 passed**
  against FreeCAD 1.1.4 at `E:\Softwares\FreeCAD\bin\freecadcmd.exe` with Taiga
  source at `G:\Cources\py\Projects\taiga-upstream-copy`.
- CLI argument transport uses FreeCAD's `--pass` mechanism (positional args
  after `--pass`) because `freecadcmd.exe` strips custom env vars and
  intercepts `--response` as its internal `--response-file` option.

## Implemented

- Milestones 0–7 are code-complete:
  - MCP server skeleton + `server_info` / `freecad_status` diagnostics.
  - FreeCAD bridge via one-shot child process (`freecad_bridge/`).
  - Direct parametric modeling tools: create box/cylinder, set_parameter,
    recompute, list/inspect, validate, save, export STEP, `boolean_cut`, tank,
    tubular coil.
  - Optional Taiga-S1 integration: isolated one-shot subprocess runtime with
    `taiga_status` and `taiga_score_actions`.
  - **Taiga build execution (`taiga_build`):** real isolated FreeCAD child
    process runs feasibility + Expert-driven execution loop, atomic FCStd save,
    metadata side-car, and in-child post-save validation (PartDesign-aware
    `Body.Tip.Shape` inspection).

## Product roadmap baseline (verified 2026-11-11)

- Canonical forward plan: [`docs/ROADMAP.md`](../docs/ROADMAP.md). Historical
  M0–M11 labels are preserved; planned roadmap continues at M12.
- **M12 — Capability evidence and tool discoverability (in progress):**
  - `tests/test_m12_capability_discovery.py` — operation/tool registration parity,
    unknown-operation rejection, tool description presence, schema validity.
  - `tests/test_modeling_tool_params.py` — schema coverage extended to all 41
    `*Params` models (was 22).
  - `PROJECT_MEMORY/EVIDENCE.md` — evidence format and tier definitions (T1–T6).
- `src/freecad_mcp/freecad_bridge/runner.py` owns the explicit `OPERATIONS`
  allowlist. It includes document/primitive/parameter/placement/recompute/
  inspection/validation/save/export, tank/coil, Cut/Fuse/Common, Sketcher
  geometry creation/edit/inspection, and the implemented PartDesign feature
  families. Verify operation details in source and tests rather than relying on
  this summary.
- Native FreeCAD Workbench and dockable GUI panel exist in
  `Mod/freecad_mcp_gui/`; panel calls `runner.handle_request` and shares
  operation logic. It is not yet a mature tree/selection/assembly manager.
- Taiga status/scoring are isolated and optional; `taiga_build` executes a
  particular Expert-driven child build with atomic save and post-save validation.
  General live state capture, action enumeration, typed interactive execution,
  and fair Method A/B comparison remain incomplete.
- Bounded disposable console probes on FreeCAD 1.1.4 Revision 20260928 verified
  imports for Part, PartDesign, Sketcher, Assembly, Import, TechDraw, Path, Fem,
  Mesh, Draft, Arch and Spreadsheet. A PartDesign Pad recomputed to a valid solid
  and survived FCStd reopen plus parametric Length edit. A basic Sketcher
  horizontal constraint probe remained under-constrained. ImportGui is not
  available in console mode.
- Assembly module and native workbench source are present, but headless creation
  of `Assembly::Joint` by `Document.addObject` fails (`not a document object
  type`). Native hierarchy/joint solving/persistence remain unverified; no MCP or
  GUI Assembly abstraction should be planned before the roadmap's real-runtime
  gate passes.
- No production code was changed for the roadmap milestone. The worktree already
  contained modified `CURRENT_STATE.md`, `DECISIONS.md`, `TESTING.md`, and
  `CHANGELOG.md` before this task; preserve that unrelated work.

## Exposed MCP tools

`server_info`, `freecad_status`, `create_document`, `create_box`,
`create_cylinder`, `set_parameter`, `recompute`, `list_objects`,
`inspect_object`, `validate_document`, `save_document`, `export_step`,
`boolean_cut`, `boolean_fuse`, `boolean_common`, `tank`, `tubular_coil`,
`sketch_groove`, `sketch_pad`, `sketch_pocket`, `sketch_revolution`,
`partdesign_fillet`, `partdesign_chamfer`,
`partdesign_linear_pattern`, `partdesign_polar_pattern`,
`partdesign_mirrored`, `partdesign_thickness`,
`taiga_status`, `taiga_score_actions`, `taiga_build`.

## New files (Phase 1–3)

- `src/freecad_mcp/pathing.py` — shared path translation (WSL2↔Windows via
  `wslpath`, no shell interpolation).
- `src/freecad_mcp/freecad_bridge/taiga_runner.py` — FreeCAD-side Taiga build
  runner (child process only; imports FreeCAD/freecad_s1 lazily).
- `src/freecad_mcp/integrations/taiga/child.py` — parent-side `TaigaChildRunner`
  (argv-based `--pass` transport, diagnostics, error classification).
- `src/freecad_mcp/tools/taiga.py` — `taiga_build` MCP tool registration plus
  `TaigaBuildParams`/`TaigaBuildResult` contracts.

## New config settings

- `FREECAD_MCP_TAIGA_SOURCE` — Taiga-S1 source repository root (child imports
  `freecad_s1` from here). `None` → Taiga build unavailable.
- `FREECAD_MCP_TAIGA_BUILD_TIMEOUT_SECONDS` — per-build timeout (default 120s).

## Outstanding / known limitations

- Milestone 8 (documentation/release preparation) not started.
- FreeCAD required from Milestone 2 on; developed against FreeCAD 1.1.x on a
  Windows host; system Python cannot import FreeCAD; bridge uses the FreeCAD
  console interpreter.
- `subprocess.run` timeout does not kill the child on expiry (Python stdlib
  limitation); orphaned lock files handled by workspace stale-lock detection.
- Real FreeCAD integration not runnable in this Linux environment; gated behind
  `FREECAD_MCP_FREECAD_EXECUTABLE` + `FREECAD_MCP_TAIGA_SOURCE`.

## Task-specific notes

- 2026-11-08: real FreeCAD 1.1.4 Pocket integration tests pass for normal and
  reversed cuts. Pad and Pocket must be added to their Body; adding a standalone
  sketch changes Body.Tip, so the Pocket operation restores the prior Tip first.
  Reopened documents verify feature links, solid validity, volume, and cut depth.
  Windows FreeCAD can run from WSL; test workspaces must use Windows-visible paths.
- 2026-11-09: M11-B `partdesign_chamfer` (native PartDesign::Chamfer) implemented
  across runner.py, modeling.py, direct.py, panel.py, tests/test_runner.py,
  tests/test_app_backends.py, tests/test_freecad_integration.py. Verified on real
  FreeCAD 1.1.4: `PartDesign::Chamfer.Base` is `App::PropertyLinkSub` accepting
  `(object, [subelement_names])` such as `(Pad, ["Edge7"])`. The Chamfer `Size`
  property is set, `UseAllEdges = False`, `ChamferType` defaults to 'Equal distance'.
  Selective edge chamfering produces a valid solid and advances Body.Tip to the
  Chamfer. A native console probe using `(Pad, ['Edge7'])`, size 3 mm produced a
  valid solid and made Chamfer the Body tip. The runner uses Base subelement names
  rather than assigning detached edges to AddSubShape. Real-FreeCAD selective valid
  chamfer tests pass for one edge (Edge7) and multiple edges ([7, 9]); invalid
  inputs, save/reopen persistence, and failed-recompute disk rollback verified.
- 2026-11-09: M11-C `partdesign_linear_pattern` (native PartDesign::LinearPattern)
  implemented across runner.py, modeling.py, direct.py, panel.py,
  tests/test_runner.py, tests/test_app_backends.py, tests/test_freecad_integration.py.
  Verified on real FreeCAD 1.1.4: `PartDesign::LinearPattern` requires `BaseFeature`
  (target feature), `Originals = [target]` (required — without it the shape is NULL),
  `Direction` as a LinkSub reference `(object, [edge_subname])` to an edge parallel
  to the requested principal axis, `Length` (positive float), and `Occurrences`
  (int >= 2). `FreeCAD.Vector` (not `Part.Vector`) is used for axis direction
  comparison. Edge direction is found by checking each edge's tangent against the
  requested axis vector. All three axes (X_Axis, Y_Axis, Z_Axis) produce valid
  solids; volume scales with occurrence count. Save/reopen persistence and
  parametric editability verified. Real-FreeCAD tests pass for all three axes;
  invalid inputs, save/reopen persistence, and failed-recompute disk rollback verified.
- 2026-11-09: M11-D `partdesign_polar_pattern` (native PartDesign::PolarPattern)
  implemented across runner.py, modeling.py, direct.py, panel.py,
  tests/test_runner.py, tests/test_app_backends.py, tests/test_freecad_integration.py.
  Verified on real FreeCAD 1.1.4: `PartDesign::PolarPattern` requires `BaseFeature`
  (target feature), `Originals = [target]` (required — without it the shape is NULL),
  `Axis` as `App::PropertyLinkSub` `(object, [edge_subname])` referencing an edge
  parallel to the requested principal axis, `Angle` (finite positive float up to 360
  degrees), and `Occurrences` (int >= 2). `FreeCAD.Vector` is used for axis direction
  comparison; edge direction is found via `tangentAt(0.5)`. All three axes produce a
  single valid solid; volume scales non-linearly with angle and occurrences
  (360°×4 occurrences = 32000 for an 8000-volume base). Save/reopen persistence and
  parametric editability verified. Real-FreeCAD tests pass for all three axes,
  360° full rotation, 45° partial angle, invalid inputs, save/reopen persistence,
  and failed-recompute disk rollback.
- 2026-11-09: M11-E `partdesign_mirrored` (native PartDesign::Mirrored)
  implemented across runner.py, modeling.py, direct.py, panel.py,
  tests/test_runner.py, tests/test_app_backends.py, tests/test_freecad_integration.py.
  Verified on real FreeCAD 1.1.4: `PartDesign::Mirrored` requires `BaseFeature`
  (target feature), `Originals = [target]` (required — without it the shape is NULL),
  `MirrorPlane` as `App::PropertyLinkSub` `(object, [face_subname])` referencing a
  face whose normal is parallel to the requested principal plane normal
  (XY_Plane → Z normal, XZ_Plane → Y normal, YZ_Plane → X normal). Face normal is
  found via `normalAt(0.5, 0.5)`. All three planes produce a single valid solid
  (volume = 2 × base volume). Save/reopen persistence and parametric editability
  verified. Real-FreeCAD tests pass for all three planes, invalid inputs,
  save/reopen persistence, and failed-recompute disk rollback.
- 2026-11-09: M11-F `partdesign_thickness` (native PartDesign::Thickness)
  implemented across runner.py, modeling.py, direct.py, panel.py,
  tests/test_runner.py, tests/test_app_backends.py, tests/test_freecad_integration.py.
  Verified on real FreeCAD 1.1.4: `PartDesign::Thickness.BaseFeature` links the
  target feature, `Value` (Quantity, settable as float in mm) is the wall
  thickness, `Reversed` (bool) controls the offset direction. FreeCAD 1.1.4
  exposes only Mode='Skin' (no Inward/Outward enumeration). Faces to remove are
  set via `Base` as a LinkSub `(object, [face_subname1, ...])` (e.g. `(Pad,
  ['Face6'])`); 1-based face indices are converted to `FaceN` subnames. Without
  removed faces the solid matches base volume (ineffective); with removed faces
  volume reduces for inward direction and increases for outward direction (e.g.
  24000 -> 10128 for one face inward). Volume validation is direction-aware:
  inward requires result volume < base volume; outward only requires result
  volume != base volume. `body.addObject()` must be called explicitly to advance
  Body.Tip. Save/reopen persistence and parametric editability verified.
  Real-FreeCAD tests pass for outward (volume increases) and inward (volume
  decreases) directions, single and multiple removed faces, invalid face indices,
  save/reopen persistence, and failed-recompute disk rollback.
- 2026-11-09: M11-G `sketch_revolution` (native PartDesign::Revolution)
  **VERIFIED FIX on real FreeCAD 1.1.4:**
  `ReferenceAxis` now uses sketch edge subnames (`(sketch, ["EdgeN"])`) instead of
  `(body.Origin, [axis])` or origin axis object references.

  VERIFIED FreeCAD 1.1.4 behavior:
  - Body.Origin.Group is EMPTY; axes are in Origin.OriginFeatures but referencing
    them gives "out of allowed scope" or "Unsupported geometry type".
  - OriginFeature X_Axis/Y_Axis: "Revolve axis intersects the sketch" → NULL.
  - OriginFeature Z_Axis: "Axis must not be perpendicular to the sketch plane" → NULL.
  - **Sketch edge subnames WORK:** (sketch, ["Edge1"]) → Axis=Vector(1,0,0), valid
    solid, Volume=113097. (sketch, ["Edge2"]) → Axis=Vector(0,1,0), valid solid,
    Volume=150796.

  Added `_find_revolution_axis_edge()` (runner.py:5467) that finds sketch edges
  parallel to the requested global axis, accounting for sketch Placement rotation.
  X_Axis and Y_Axis are supported via sketch edges. Z_Axis cannot be supported on
  XY-plane sketches (FreeCAD rejects perpendicular axis) → clear validation error.

  All 14 mock tests (test_runner.py) and 9 backend tests (test_app_backends.py)
  pass; full suites 479 passed. Real-FreeCAD integration tests updated:
  X/Y axis tests expect success with tree assertion; Z_Axis test expects rejection;
  invalid axis/angle/missing-sketch tests unchanged; parametric editability and
  save/reopen tests use X_Axis.
- Shared agent skills and persistent project memory were added under
  `.agents/skills/` and `PROJECT_MEMORY/`; root `AGENTS.md` routes agents to them.
- `gen_traceback.py` is an unrelated untracked file; do not modify or stage it.

## Taiga-S1 integration (verified 2026-11-03)

### Runtime compatibility (VERIFIED)
- **Upstream:** `/mnt/g/tmp_kilo_exp/taiga_src`, pinned commit
  `84fdd19c487658a59805531fa306fa40e8f72555`
- **FreeCAD:** 1.1.4 on Windows at `E:\Softwares\FreeCAD`, embedded Python 3.11.14
- **Launch from WSL2:** Possible when `FREECAD_PYTHON` and `FREECAD_LIB` set to
  Windows paths; `freecad_s1` must be on PYTHONPATH for FreeCAD Python

### Expert-driven execution (VERIFIED)
- Goal parsing works without external deps (`freecad_s1.schema.Goal.from_json`)
- Expert-driven Worker execution works without PyTorch, safetensors, or trained
  model weights
- Worker executes Goals via Expert actions; learned Policy is optional

### Build lifecycle (VERIFIED on Windows)
- Feasibility session + separate execution session; `build_target` returns
  detached target shape valid after feasibility session closes
- `Std_Undo` = failure in feasibility, recovery in execution
- Empty Expert result (`[]`) = unrecoverable failure; `Done` = completion
- Step budget: `2 * expert_plan_length + 6`
- IoU threshold: `>= 0.99` for success
- Detached target does not leak into execution session
- Atomic FCStd save (temp `.fcstd` + `os.replace`) while document open
- Metadata side-car separate from FCStd; `artifact_written=true,
  metadata_written=false` when metadata fails
- Lock held through feasibility, execution, save, metadata, post-save validation;
  released in `finally` on every path
- In-child post-save validation reopens saved artifact via `FreeCAD.openDocument`
- PartDesign-aware validation inspects `Body.Tip.Shape` only (not sketches,
  origin objects, wires)

### Architecture (VERIFIED decisions)
- **Method A:** OpenCode/LLM → FreeCAD MCP → direct FreeCAD modeling tools
- **Method B:** OpenCode/LLM + Taiga Task Planner Skill → Goal JSON → FreeCAD
  MCP → Taiga Worker + Expert → FreeCAD
- `freecad_bridge/runner.py` remains Method A only; no Taiga execution logic
- Parent process never imports `FreeCAD` or `freecad_s1`; child imports them
  lazily
- Request/response via argv (`--pass` positional args), not env vars
- FreeCAD child alone owns the document lock

### Current status (VERIFIED)
- `taiga_build` **implemented and committed** (`1949a5b`).
- Next: Milestone 8 (documentation/release preparation).

### Sketch line editing API (VERIFIED 2026-11-07, FreeCAD 1.1.4)

- FreeCAD 1.1.4 `Sketcher.SketchObject` has **no** `setGeometry`, `setLine`, or
  `replaceGeometry` method. The earlier `setGeometry` implementation was invalid.
- `Geometry` is an assignable property (list). Installed FreeCAD examples use the
  supported pattern: mutate the geometry copy's `StartPoint`/`EndPoint`, then
  assign the full list back (`sketch.Geometry = geometry_list`), followed by
  `doc.recompute()`.
  - `Mod/Sketcher/SketcherExample.py` assigns `f.Geometry = [...]`.
  - `Mod/Draft/draftguitools/gui_edit_sketcher_objects.py` mutates a line and
    assigns `obj.Geometry = [line]`.
- Verified on real FreeCAD 1.1.4: whole-list assignment keeps the same index,
  unrelated geometry, constraints, construction state, and geometry ID when the
  existing geometry object is mutated and reassigned. Constructing a *new*
  `Part.LineSegment` changes the geometry ID (construction defaults can also
  change), so prefer mutate-and-reassign.
- `sketch_edit_line` now uses this pattern; it still rejects geometry referenced
  by constraints.
- Tests: full suite 521 passed, 40 skipped (Linux); real FreeCAD integration
  `tests/test_freecad_integration.py -k "sketch_edit_line"` 2 passed against
  FreeCAD 1.1.4.

### Sketch ellipse rotation API (VERIFIED 2026-11-07, FreeCAD 1.1.4)

- `Part.Ellipse` has **no** `MajorDirection` or `MinorDirection` attributes.
  The real orientation API is `AngleXU` (radians, read-only in practice),
  `XAxis`/`YAxis` (read-only in practice), `Axis`, `Location`/`Center`.
  Setting `AngleXU`, `XAxis`, or `YAxis` silently has no effect.
- `Part.Ellipse.rotate(placement)` mutates the conic in place. It accepts exactly
  one `Base.Placement` argument (not axis+angle, not Rotation). The placement's
  rotation angle is interpreted in **degrees** in FreeCAD 1.1.4 (e.g.
  `Rotation(Vector(0,0,1), 30)` → `AngleXU = pi/6`).
- Absolute orientation: read current `AngleXU` (degrees), then rotate by
  `rotation_deg - current_angle_deg` about the ellipse centre. Rotating by the
  raw requested angle is relative and fails to reset/rotate predictably on
  repeated edits.
- Verified on real FreeCAD 1.1.4: repeated edits (30° → 0° → 45° → 360° →
  -30°) set the requested absolute angle; centre/radii correct; geometry index,
  geometry ID, construction state, unrelated geometry, and constraints
  unchanged; orientation and construction state preserved after save/reopen.
- `sketch_edit_ellipse` now uses mutate-and-reassign of `sketch.Geometry` plus
  `Part.Ellipse.rotate(Placement(center, Rotation(+Z, delta_deg)))`; constraint
  rejection behavior unchanged.
- Tests: focused `tests/test_runner.py -k "sketch_edit_ellipse or inspect_sketch"`
  26 passed; real FreeCAD integration
  `tests/test_freecad_integration.py -k "sketch_edit_ellipse"` 2 passed
  (Windows `freecadcmd.exe` launched from WSL).

### Phase 0 verification (2026-11-03) — read-only experiments
All run against real FreeCAD 1.1.4 embedded Python and Taiga upstream at
`/mnt/g/tmp_kilo_exp/taiga_src`. No repo source modified during experiments.

- **E1 path/envelope I/O — PASS:** Windows `G:\`, WSL `/tmp`, and UNC
  `\\wsl.localhost\Debian\tmp\` all readable/writable. Paths with spaces +
  non-ASCII OK. `freecadcmd.exe` strips custom env vars. Workaround: argv
  transport via `--pass`.
- **E2 build_target detached lifecycle + IoU — PASS:** detached Compound
  (1 solid, vol 9147.61) valid after session close; Expert reaches `Done`;
  IoU = 1.0 >= 0.99.
- **E3 atomic save/metadata/locking — PASS:** atomic_save OK; meta read/write
  OK; locking acquire/reacquire/reclaim-stale OK.
- **E4 post-save validation + Method A/B isolation — PASS:** inspect
  `Body.Tip.Shape` instead of every object; Method A runner independent of
  Taiga.

---
