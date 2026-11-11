# Decisions

Significant decisions with rationale. Keep concise; source code wins on conflict.

- **D1 — One-shot child process for FreeCAD (M2):** bridge talks to
  `freecadcmd` via JSON request/response envelopes; timeout (default 60 s) and
  typed errors. Rationale: local-only, bounded, no service. Alternative
  (persistent process / sockets) deferred.
- **D2 — Explicit validated tools, no generated-Python execution (M3):** MCP
  surface exposes typed operations; arbitrary Python inside FreeCAD rejected.
  Rationale: safety and verifiability.
- **D3 — Optional dependency-free integration contract (M5):** `integrations/base.py`
  defined before any backend work; core never imports optional deps.
- **D4 — Isolated one-shot Taiga-S1 runtime (M6):** explicit interpreter via
  `FREECAD_MCP_TAIGA_PYTHON`, strict local model dir, status + score only
  (`plan()` → `RUNTIME_CONTEXT_REQUIRED`). Rationale: isolation; upstream
  worker/sockets rejected pending verified PartDesign boundary. Pinned upstream
  revision: `84fdd19c487658a59805531fa306fa40e8f72555`.
- **D5 — Evidence over assumption:** FreeCAD APIs, MCP SDK, OpenCode MCP config
  format, and Taiga-S1 interfaces verified before reliance; nothing invented.
- **D6 — Taiga Worker execution via Expert (2026-11-02):** Use Expert-driven
  execution (no PyTorch/weights) for initial integration. Policy is optional.
  Windows FreeCAD 1.1.4 compatible when configured with `FREECAD_PYTHON`/
  `FREECAD_LIB` pointing to Windows paths. Rationale: avoids heavy deps,
  verified working.
- **D7 — Method A/B separation (2026-11-02):** Preserve two distinct approaches
  sharing infrastructure. Method B uses Goal JSON + Taiga Worker
  creating/saving its own document, MCP inspects artifact separately (avoid
  concurrent control). Rationale: verified execution pattern, clear separation
  of concerns.
- **D8 — Taiga build execution via isolated FreeCAD child (2026-11-03):**
  `taiga_build` tool → `BuildRuntime` → `TaigaChildRunner` → `freecadcmd`.
  Child runs feasibility + Expert execution loop, atomic save, metadata, and
  PartDesign-aware post-save validation. Rationale: keeps server process
  FreeCAD-free; verified against real FreeCAD 1.1.4.
- **D9 — argv `--pass` transport (2026-11-03):** Request/response paths passed
  as positional args after `--pass` in `freecadcmd.exe -c "runpy.run_path(...)"
  --pass <req> <resp>`. Rationale: `freecadcmd.exe` strips custom environment
  variables (E1) and intercepts `--response` as its internal `--response-file`
  option; positional args after `--pass` are passed through uninterpreted.
- **D10 — No Taiga logic in `freecad_bridge/runner.py` (2026-11-03):** runner.py
  remains Method A only; Taiga execution lives in
  `freecad_bridge/taiga_runner.py` and `integrations/taiga/child.py`.
  Rationale: preserve Method A independence; avoid conflating distinct bridges.
- **D11 — Shared application boundary (2026-11-04):** expose data contracts and
  backend adapters without MCP, Qt, FreeCAD, or LLM dependencies. Preserve the
  existing direct and Taiga headless runtimes; active-document execution and its
  ownership semantics require separate design and verification.
- **D12 — Document & model inspection contracts (2026-11-04):** add
  `app/inspection.py` with `ShapeInfo`, `ObjectInspection`, `DocumentInspection`,
  `InspectionRequest`, `InspectionResult` and `InspectionBackend` protocol, plus
  `DirectInspectionBackend` delegating to `FreeCadBridge`. `document_id` added as
  an optional first-class field on `BuildRequest`. Inspection reuses existing
  bridge operations (`list_objects`, `inspect_object`) and never imports
  FreeCAD in the parent process.

- **D13 — Sketch line edits via `sketch.Geometry` assignment (2026-11-07):**
  FreeCAD 1.1.4 has no `setGeometry`/`setLine`/`replaceGeometry`. Edit an
  existing line by mutating the existing geometry copy and assigning the full
  geometry list back (`sketch.Geometry = geometry_list`), then recompute. This
  matches FreeCAD's installed Draft editing code and preserves index, unrelated
  geometry, constraints, construction state, and geometry ID. Reject geometry
  referenced by constraints. Rationale: verified against real FreeCAD 1.1.4;
  delete-and-re-add is not equivalent.
- **D14 — Sketch ellipse rotation via in-place `Part.Ellipse.rotate`
  (2026-11-07):** FreeCAD 1.1.4 `Part.Ellipse` has no
  `MajorDirection`/`MinorDirection`; `AngleXU`/`XAxis`/`YAxis` are effectively
  read-only. Use mutate-and-reassign of `sketch.Geometry` and
  `Part.Ellipse.rotate(Placement(center, Rotation(+Z, delta_degrees)))` on the
  existing geometry object. `rotate` takes one `Placement` and interprets its
  angle in degrees. Apply an absolute rotation by subtracting the current
  `AngleXU`. Preserves index, geometry ID, construction state, unrelated
  geometry, and constraints; verified across save/reopen. Rationale: replacing
  the geometry risks changing identity; the orientation properties are not
  writable.

Add new dated decisions here; do not record trivial implementation details.

- **D15 — PartDesign::LinearPattern API (2026-11-09):** Verified on real
  FreeCAD 1.1.4: `PartDesign::LinearPattern.BaseFeature` is the target feature,
  `Originals = [target]` is required (without it the shape is NULL),
  `Direction` is an `App::PropertyLinkSub` referencing an edge parallel to the
  requested principal axis `(object, [edge_subname])`, `Length` is a positive
  float, `Occurrences` is an int >= 2. `FreeCAD.Vector` (not `Part.Vector`) is
  used for axis direction comparison. Edge direction is determined by checking
  each edge's `tangentAt(0.5)` against the requested axis vector. All three axes
  (X_Axis, Y_Axis, Z_Axis) produce valid solids; volume scales with occurrence
  count. `body.addObject()` must be called explicitly to advance `Body.Tip`.

- **D16 — PartDesign::PolarPattern API (2026-11-09):** Verified on real
  FreeCAD 1.1.4: `PartDesign::PolarPattern.BaseFeature` is the target feature,
  `Originals = [target]` is required (without it the shape is NULL),
  `Axis` is an `App::PropertyLinkSub` referencing an edge parallel to the
  requested principal axis `(object, [edge_subname])`, `Angle` is a finite
  positive float up to 360 degrees, `Occurrences` is an int >= 2.
  `FreeCAD.Vector` is used for axis direction comparison; edge direction is
  determined by checking each edge's `tangentAt(0.5)` against the requested axis
  vector. All three axes produce a single valid solid; volume scales non-linearly
  with angle and occurrences. `body.addObject()` must be called explicitly to
  advance `Body.Tip`.

- **D17 — PartDesign::Mirrored API (2026-11-09):** Verified on real
  FreeCAD 1.1.4: `PartDesign::Mirrored.BaseFeature` is the target feature,
  `Originals = [target]` is required (without it the shape is NULL),
  `MirrorPlane` is an `App::PropertyLinkSub` `(object, [face_subname])`
  referencing a face whose normal is parallel to the requested principal plane
  normal (XY_Plane → Z normal, XZ_Plane → Y normal, YZ_Plane → X normal). Face
  normal is determined by checking each face's `normalAt(0.5, 0.5)` against the
  requested plane normal vector. All three planes produce a single valid solid
  (volume = 2 × base volume). `body.addObject()` must be called explicitly to
  advance `Body.Tip`.

- **D18 — PartDesign::Thickness API (2026-11-09):** Verified on real
  FreeCAD 1.1.4: `PartDesign::Thickness.BaseFeature` is the target feature,
  `Value` (App::PropertyQuantity) is the wall thickness (settable as a float in
  mm), and `Reversed` (App::PropertyBool) controls the offset direction.
  FreeCAD 1.1.4 exposes only `Mode='Skin'` in the Mode enumeration — Inward/
  Outward are NOT valid — so direction is expressed solely through `Reversed`.
  Faces to remove are set via `Base` as a LinkSub `(object,
  [face_subname1, ...])` (e.g. `(Pad, ['Face6'])`); 1-based face indices are
  converted to `FaceN` subnames. Without removed faces the resulting solid
  matches the base volume (ineffective hollow); with removed faces volume
  reduces for inward direction and increases for outward direction (e.g.
  24000 -> 10128 for one face on a 40x30x20 pad). Volume validation is
  direction-aware: inward (`Reversed=True`) requires result volume < base
  volume; outward (`Reversed=False`) only requires result volume != base
  volume. `body.addObject()` must be called explicitly to advance `Body.Tip`.

- **D19 — PartDesign::Revolution API (2026-11-09):** Verified on real
  FreeCAD 1.1.4: `PartDesign::Revolution` properties include `Profile` (sketch
  object), `ReferenceAxis` (LinkSub: `(DocumentObject, [subnames])`), `Angle`
  (Quantity in degrees), `Reversed` (bool), `Axis` (computed Vector), `Base`
  (computed Vector). Origin axes `X_Axis`, `Y_Axis`, `Z_Axis` are `App::Line`
  objects listed in `doc.Objects`, children of `Body.Origin`. The handler creates
  a `PartDesign::Body` if the sketch is not already in one, adds the sketch to
  the Body via `body.addObject(sketch)`, creates `PartDesign::Revolution`, adds
  it to the Body, sets `Profile = sketch`, sets `ReferenceAxis = (body.Origin,
  [axis])`, sets `Angle = angle_val`, recomputes, and validates (non-null valid
  solid with at least one solid). `body.addObject()` must be called explicitly
  to advance `Body.Tip`. FreeCAD 1.1.4 limitation: `ReferenceAxis` set to origin
  axes via Python API produces NULL shape ("Reference axis is invalid") — the
  property is accepted without error but the computed `Axis` is `Vector(0,0,0)`.
  Error-handling (invalid axis, angle, missing sketch) works on real FreeCAD;
  successful revolution creation is blocked by this API limitation.

- **D20 — Canonical product roadmap (2026-11-10):** Maintain `docs/ROADMAP.md`
  as the single forward product plan. Preserve historical M0-M11 and continue
  with sequential M12+ milestones. Rationale: one discoverable plan without
  conflicting milestone namespaces or duplicated roadmap copies.
- **D21 — Native Assembly verification gate (2026-11-10):** Do not define an MCP
  or GUI Assembly abstraction until installed FreeCAD 1.1.4 proves hierarchy,
  component/link insertion, representative joints, solver/update behavior,
  recompute and save/reopen. The module/type names alone are insufficient; the
  console probe showed `Assembly::Joint` is not a directly creatable document
  object. Rationale: avoid freezing an unsupported or legacy API into product
  contracts.
- **D22 — Shared GUI/MCP operation boundary (2026-11-10):** GUI Workbench remains
  a first-class adapter and dispatches through the shared explicit operation
  handler; do not duplicate CAD logic in widgets. Rationale: identical safety,
  validation and operation behavior across interfaces.
- **D23 — Engineering solver isolation (2026-11-10):** Keep FEM/CFD/thermal/
  structural/fluid calculations behind optional versioned adapters; neither
  couple them to the CAD core nor treat CAD shape validity as engineering
  certification. Rationale: separate deployment, solver dependencies and
  engineering evidence from geometry operations.
- **D24 — Capability evidence tiers (2026-11-10):** Capability claims must state
  runtime version and distinguish importability, type/property availability,
  recompute, valid geometry, save/reopen and parametric editability. Mocks do
  not establish native support. Rationale: prevent API names and documentation
  from being mistaken for verified CAD behavior.

---
