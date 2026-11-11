# FreeCAD_MCP Product Roadmap

Status legend for the capability matrix: **Verified in 1.1.4** means checked
against the installed runtime with the evidence level stated in the row.
**Available but not sufficiently investigated** means an API/module is present
but behavior has not been established. **External workbench or dependency**
means capability relies on a separate installation. **Requires integration**
means the project needs an adapter or implementation. **Unsupported or
unavailable** means evidence rules out the capability in the stated context.
**Unknown** means evidence is insufficient to classify it. Matrix rows use only
these labels. Implementation status in the completed inventory and roadmap
milestones uses separate explicit language. Geometry validity is not engineering
approval.

This is the canonical long-term product and architecture roadmap. Historical
project milestones use M0-M11; new roadmap milestones continue sequentially at
M12. The repository-level release/documentation milestone labels in older notes
are historical and are not a second forward roadmap.

## Vision and scope

Build an extensible CAD automation platform for agents to construct, inspect,
modify, validate, and manage complex parametric models in FreeCAD 1.1.4. The
product supports two independent workflows: direct agent-to-MCP operation
(Method A) and optional Taiga-S1-assisted iterative action selection (Method B).
The GUI Workbench and dockable panel are permanent first-class interfaces.

The platform separates five kinds of outcomes:

1. **Geometric/parametric CAD:** valid editable solids and feature histories.
2. **Assembly:** parts, links, coordinate systems, joints, and relationships.
3. **Engineering calculations:** explicit design constraints, sizing, and
   calculations with traceable assumptions.
4. **Simulation/analysis:** FEM, structural, thermal, CFD, fluid, and heat
   transfer solving through modular adapters.
5. **Manufacturing/export:** manufacturing-aware checks and exchange/export.

No CAD model, shape-validity result, or successful simulation by itself is a
certification that an engineering design is safe or manufacturable. Domain review,
applicable codes, boundary conditions, material data, solver verification, and
manufacturing review remain separate responsibilities.

## Verified current architecture

```text
Method A: External agent -> MCP stdio server/tools -> shared app contracts /
          direct adapter -> one-shot FreeCAD child -> allowlisted operations
          -> structured result and workspace artifact

Method B today: external agent -> optional Taiga integration/build request
                -> isolated child workflow -> FreeCAD child -> saved artifact

GUI today: FreeCAD Workbench + dockable panel -> runner.handle_request()
           (same explicit operation registry as the bridge)
```

- The MCP parent does not import FreeCAD. CAD operations execute in the installed
  FreeCAD console child using JSON requests and responses. The operation registry
  is explicit; generated arbitrary Python is not an agent capability.
- `src/freecad_mcp/app/` contains MCP-independent contracts and direct,
  inspection, and Taiga build adapters. Existing validation remains primarily in
  the FreeCAD runner/operation handlers; there is no general validation package
  despite older illustrative references to `validation/`.
- `runner.OPERATIONS` is shared with GUI dispatch through
  `freecad_bridge.runner.handle_request`. GUI handlers package user input; they
  must not implement parallel CAD algorithms.
- Workspace locking and atomic artifact persistence are established for the
  relevant build flow. Keep concurrency, failure envelopes, rollback, and
  ownership explicit as operation coverage grows.
- Taiga runtime and model scoring are optional and isolated. No implicit model
  download is permitted. Taiga absence must not affect Method A.

## Completed implementation inventory

| Capability | Project status | Evidence and boundary |
| --- | --- | --- |
| MCP stdio server, diagnostics, typed tools | Verified / implemented | `src/freecad_mcp/server.py`, `tools/`, server and stdio tests |
| FreeCAD detection, version and child-process bridge | Verified / implemented | `freecad_bridge/bridge.py`, `discovery.py`, `runner.py`; parent stays FreeCAD-free |
| Workspace document, create box/cylinder, parameter/placement edits, recompute, list/inspect/validate, save, STEP export | Verified / implemented | Registered operations and focused integration tests; do not recast as missing |
| Tank and tubular coil workflows | Verified / implemented | Existing `tank`, `tubular_coil`; component-template generalization remains proposed |
| Boolean Cut, Fuse, Common | Verified / implemented | `_op_boolean_cut`, `_op_boolean_fuse`, `_op_boolean_common`; improve multi-solid/topology diagnostics, not basic boolean availability |
| Sketch creation and primitive geometry (rectangle, circle, triangle, ellipse, regular/custom polygon), inspect, edit and delete | Verified / implemented | `runner.OPERATIONS`; real 1.1.4 line/ellipse edits preserve geometry identity per project memory |
| PartDesign Pad, Pocket, Groove, Revolution, Fillet, Chamfer, Linear/Polar Pattern, Mirrored, Thickness | Verified / implemented | Source, MCP schemas and GUI controls. Real FreeCAD evidence exists for core paths and persistence; see historical decisions/tests for property-specific constraints |
| Shared app layer and model inspection contracts | Verified / implemented | `app/contracts.py`, `app/direct.py`, `app/inspection.py`; delegates to existing bridge operations |
| FreeCAD MCP GUI Workbench and dockable panel | Verified / implemented (early) | `Mod/freecad_mcp_gui`; broad controls call shared `handle_request`, but tree/selection/assembly/scale UX are missing |
| Optional Taiga status/scoring and `taiga_build` | Verified / implemented with limits | Isolated child, Expert-driven build lifecycle, atomic save and post-save validation. Not a general live interactive controller |
| Taiga live state/action enumeration/general operation execution and fair Method A/B benchmark | Missing | Guarded `workflow.py` is a skeleton; no general state/action/execution boundary or comparison harness |
| Sketcher geometric/dimensional constraints and solver editing | Missing | Existing sketches are geometry workflows; fully constrained parametric sketch lifecycle is not established |
| Native Assembly MCP automation | Missing / research-gated | Runtime module/types present; minimal console probe below could not create a native joint through `Document.addObject` |
| FEM/CFD/thermal/piping-calculation integrations and engineering validation | Missing / external integration | No solver integration demonstrated by the project |

## FreeCAD 1.1.4 capability matrix

Probes were run against `FreeCAD 1.1.4 Revision 20260928`, using the installed
Windows `freecadcmd.exe` from WSL. Probes used disposable temporary documents;
they did not open or alter project/user CAD documents. Source code, tests, and
runtime evidence are separately identified. Importable modules do not prove
feature behavior, and object construction does not prove a valid recompute.

| Capability family | FreeCAD 1.1.4 status | Evidence/API/property references | Current project exposure | Future ownership/tests/unknowns |
| --- | --- | --- | --- | --- |
| Part primitives and document objects | Verified in 1.1.4 | `Part` imports; disposable `Part::Box` and `Part::Cylinder` recompute to valid single-solid shapes | Box/cylinder operations implemented | Shared operation; MCP and GUI; verify dimensions, placement, edit and save/reopen per operation |
| Placement and transforms | Verified in 1.1.4 | Existing runner `_op_set_placement`; FreeCAD `Placement.Base`, `Placement.Rotation`; probe of general link/attachment semantics incomplete | Base XYZ placement operation implemented; preserves rotation per project memory | Shared operation; test local/global frames, attached objects, links, rotation and persistence before broadening |
| Shape/solid validity and topology inspection | Verified in 1.1.4 | `Shape.isValid()`, `Shape.Solids`, volume and topology used by runner; box/cylinder/booleans tested | `inspect_object`, `validate_document` | Shared inspection contracts; expand diagnostics for compounds, multiple solids, tolerances and topology references |
| Boolean Cut/Fuse/Common | Verified in 1.1.4 | `TopoShape.cut`, `.fuse`, `.common`; disposable shapes returned valid results; integration tests exist | All three implemented through shared operation registry | Improve workflows, compound/multi-solid policy, split operations and robust reference handling; not a missing basic boolean feature |
| Sketch geometry and edits | Verified in 1.1.4 | `Sketcher::SketchObject`, `Geometry`, `addGeometry`, `addConstraint`, `FullyConstrained`; source-proven line/ellipse mutation behavior | Primitive creation/edit/delete/inspection implemented | Expand supported curves, closure/profile analysis, geometry ID/reference safety, error reporting and persistence tests |
| Sketch constraints/solver | Available but not sufficiently investigated | `addConstraint` exists; one horizontal constraint probe left a one-line sketch under-constrained (`FullyConstrained=False`); broader solver APIs/status not probed | No general constraint tool/model | Sketcher layer; inspect `solve`, `Constraints`, `DoF`, redundancy/conflict APIs before schema; real under/fully constrained and save/reopen tests |
| Sketch support/attachment/external geometry/multiple profiles | Available but not sufficiently investigated | Sketch object introspection only; no behavioral probe of attachment/support or external geometry | Not exposed as a complete workflow | Shared typed references; investigate support planes, attachment offsets, external geometry, profile selection and multiple-wire failure cases |
| PartDesign Body and Pad | Verified in 1.1.4 | `PartDesign::Body`, `body.addObject`, `PartDesign::Pad.Profile`, `.Length`, `Body.Tip`; valid solid after recompute; reopened and edited Length 5->7 mm successfully | Pad implemented; Pocket tested against actual FreeCAD; body/tip rules are important | Shared operation; MCP/GUI; body membership, tip, valid solid, edit and save/reopen required |
| PartDesign Pocket | Verified in 1.1.4 | Existing real integration tests cover normal/reversed cut, solid volume/depth, persistence and body tip | Implemented | Preserve direction expectations and detached sketch/body rules in regression tests |
| PartDesign Revolution | Verified in 1.1.4 | `PROJECT_MEMORY/DECISIONS.md` D19 and `tests/test_freecad_integration.py` record sketch-edge LinkSub axes working in verified orientations, origin-axis limitations, and XY sketch normal-axis constraints | Implemented with explicit supported-axis checks | Expand only with a real FreeCAD probe; do not infer that every axis/profile combination works |
| PartDesign Groove | Verified in 1.1.4 | `tests/test_freecad_integration.py::test_real_freecad_groove_*` covers valid X/Y axes and partial angle, invalid axis/angle/missing sketch, save/reopen, parametric edit and rollback. Reproduce from WSL with `FREECAD_MCP_FREECAD_EXECUTABLE=/mnt/e/Softwares/FreeCAD/bin/freecadcmd.exe uv run pytest tests/test_freecad_integration.py -k groove -q -rs`. The tests explicitly record that FreeCAD 1.1.4 does not persist the computed Groove shape, Axis or Profile link through save/reopen; live creation geometry is checked before reopen. | Implemented | Preserve the documented reopen limitation; investigate its cause and safe recovery/recompute path before relying on Groove persistence in long workflows |
| PartDesign Fillet/Chamfer | Verified in 1.1.4 | `PROJECT_MEMORY/TESTING.md` and `tests/test_freecad_integration.py` record selective LinkSub edges, recompute, reopen and rollback checks | Implemented | Topological naming fragility remains; selection-aware stable references and failure cases |
| Linear/Polar Pattern, Mirrored, Thickness | Verified in 1.1.4 | `PROJECT_MEMORY/DECISIONS.md` D15-D18 and focused real integration tests document native properties, recompute/reopen/edit behavior | Implemented | Preserve exact property rules; broader non-principal axes and reference stability are unknown |
| Additive/Subtractive Pipe and Additive/Subtractive Loft | Available but not sufficiently investigated | Installed `PartDesign` module imports; native type/property/valid-shape probe not completed | Not exposed | Group paired features into one coherent M15 milestone; verify profile/spine/wire ordering and solid result in FreeCAD before implementation |
| PartDesign Hole, Draft and other native features | Available but not sufficiently investigated | PartDesign/Draft modules import; type-specific property enumeration and valid feature probes incomplete | Not exposed as general operations | Catalog object types/properties in disposable probe, then real model tests; avoid relying on GUI labels alone |
| Part advanced curves, surfaces, shells, sweeps, lofts, refinement/repair | Available but not sufficiently investigated | `Part` imports; broad BRep/TopoShape operations exist, but no complete support matrix or failure/repair proof | Basic primitives/booleans and tubular coil only | Keep Part and PartDesign semantics distinct; probe representative operations and valid topology before exposing tools |
| Native Assembly module and workbench resources | Verified in 1.1.4 | `Assembly` imports. Installed `Mod/Assembly/CommandCreateAssembly.py` and `JointObject.py` expose the native assembly and proxy design; disposable console probe confirmed `Assembly::Joint` is not directly creatable with `Document.addObject` | No MCP assembly tools; no GUI assembly manager in this product | Hard gate: before abstraction prove native hierarchy, component/link, representative joint, solve/update, recompute, save/reopen and failure inspection. Unknown whether supported GUI/proxy construction can be safely automated |
| `App::Link`, links and external references | Available but not sufficiently investigated | Installed Assembly sources use `App::Link`; no end-to-end project-level linked-part replacement/reference test | Not a general workflow | Probe linked placement, link transform, recompute, replacement, external file path and reopen. Keep links separate from copied Part::Feature geometry |
| Import/export | Available but not sufficiently investigated | STEP export is implemented and integration tested. `Import` and `TechDraw` modules imported in console; `ImportGui` raised `ImportError: Cannot load Gui module`. Broader formats remain uninvestigated. | STEP export implemented | Enumerate installed formats and headless/GUI split; round-trip geometry and metadata; formats may require external libraries |
| Document save/reopen and parametric edit | Verified in 1.1.4 | Disposable PartDesign Pad saved to temporary FCStd, reopened, Length edited, recomputed valid with new volume 700 | Workspace persistence and Taiga atomic build save implemented | Test all feature/link classes and rollback under failed recompute; document integrity and versioning remain roadmap work |
| Headless vs GUI | Verified in 1.1.4 | Console imports core modules; `ImportGui` fails without GUI; Assembly joint creation shown as proxy/GUI-oriented in installed source | MCP uses console; GUI workbench runs inside FreeCAD GUI | Separate headless operation contracts from GUI-only selection/dialog features; no unsafe access to arbitrary active documents |
| FEM / CFD / thermal / structural analysis | External workbench or dependency | `Fem` module imports; import is not proof of solver, mesh, material, boundary-condition or result correctness. No solver integration in project | None | Adapter packages should sit beside CAD core; specify solver versions, input mapping, result traceability and independent validation |
| Fluid/piping and heat-transfer calculations | Unknown | No current project calculation engine verified | None | Separate calculation/service adapters with unit-aware inputs, assumptions, standards scope, tests and report provenance |
| Manufacturing/export readiness | Unknown | STEP export exists, which is neutral geometry exchange rather than manufacturing validation | STEP export only | Add format-specific exports, units/tolerances, mesh/toolpath adapters and downstream acceptance tests; never label CAD validity as manufacturability |

## Target architecture

### Shared CAD operation layer

Keep the existing process boundary and evolve incrementally:

```text
MCP adapter ─┐
             ├─> typed app/backend contract -> validated operation request
GUI adapter ─┘                                  -> FreeCAD execution boundary
                                                -> allowlisted handler
                                                -> recompute/validation
                                                -> structured result + state delta
```

- The shared operation contract owns operation identifiers, input validation,
  deterministic parameters, result/error schemas, capability discovery, and
  stable object references. FreeCAD-native work remains in the child runner.
- MCP and GUI are adapters over the same contracts and registered operations.
  GUI-specific selection data is translated to explicit object/subelement
  references before dispatch; it must not bypass the allowlist.
- Keep parent process free of FreeCAD imports. Keep Taiga and engineering
  solvers behind optional interfaces and child/service boundaries.
- Extend workspace ownership, locks and atomic save/rollback consistently. Use
  structured operation errors and fresh, bounded model-state results.
- Do not introduce a persistent daemon or rewrite the bridge without measured
  requirements (e.g. latency/state persistence) and a separately approved design.

### Method A — Direct Agent to FreeCAD MCP

```text
User intent -> External AI agent -> MCP tool discovery/schema
            -> typed explicit CAD action -> shared app operation boundary
            -> isolated FreeCAD child -> recompute + validation + persistence
            -> structured result/state/artifact -> agent inspection/next action
```

Method A must remain fully useful without Taiga. Tools should have explicit
schemas, safe bounds, documented units/reference semantics, deterministic
results, model-state inspection, and actionable structured errors. The agent
selects operations, but never receives arbitrary Python execution. Every mutation
should return the affected objects, model revision/state summary, validity,
warnings and artifact information needed for the next step.

### Method B — Taiga-assisted CAD automation

```text
User request -> External AI agent (intent translator / clarifier)
             -> structured goal + relevant context -> optional Taiga-S1
             -> state-aware action selection / continue-retry-finish decision
             -> typed MCP execution boundary -> FreeCAD child
             -> fresh CAD state + validation feedback -> Taiga next decision
             -> agent explanation to user
```

Responsibilities:

- **External agent:** understands natural language; resolves essential
  ambiguity; emits structured goal/context; translates a selected Taiga action to
  the defined MCP execution interface when needed; explains progress/results.
- **Taiga-S1:** receives goal and actual structured state; selects an action from
  valid candidates; manages iterative continue/retry/finish decisions and uses
  real execution feedback. Do not have the agent hard-code the whole action plan
  in place of Taiga's decision role.
- **MCP:** owns deterministic operation schemas, allowlist, validation, execution,
  structured responses and FreeCAD process boundary.
- **FreeCAD child:** sole FreeCAD document executor for that request, recomputes,
  reports fresh inspectable state, and persists/validates according to the
  approved workflow.

Taiga remains optional at runtime; core imports do not load Taiga/Torch; local
interpreter and model paths are explicit; downloads never occur implicitly.
Current `taiga_build` is an implemented isolated Expert-driven build workflow
with atomic save and post-save validation, not proof of a general live
state/action/controller integration. The guarded loop still needs a verified
state provider, candidate-action enumerator, mapping from Taiga identifiers to
typed operation schemas, feedback/error policy and real integration tests.

## MCP / shared layer / GUI exposure map

| Capability family | Shared operation layer | MCP exposure | Native GUI Workbench exposure |
| --- | --- | --- | --- |
| Primitives, placement, inspection, validation | Typed primitive, placement and state-inspection operations | Existing box/cylinder/list/inspect/validate; discover schemas and add other primitives only after evidence | Existing box/cylinder/placement/list controls; improve object tree/selection and validation display |
| Boolean and Part shape operations | Explicit input links, operation type, recompute and topology result | Cut/Fuse/Common complete; later compounds/split/repair after real tests | Existing boolean controls; add selection-aware objects and multi-solid result feedback |
| Sketcher | Geometry, constraint, solver and attachment operations with stable indices/references | Existing geometry editing/inspection; future constraint/support/profile schemas | Existing sketch forms; future constraint status, graphical selection and contextual edits via same operation |
| PartDesign | Body ownership, feature links, Body.Tip and native properties | Existing Pad/Pocket/Groove/Revolution/Fillet/Chamfer/pattern/mirror/thickness; add verified feature families | Existing controls for implemented feature families; selection/property UI built atop common schemas |
| Assembly | No operation/schema until native Assembly gate passes | Future hierarchy/component/joint/solve/inspect tools through tested adapter | Native Assembly visualization/selection can be GUI-specific, but document mutations must use shared operation boundary |
| Document/model lifecycle | Document identity, dependency graph, revision, rollback and snapshots | Existing subset of create/list/save/recompute; add safe inspect/edit/delete/order features | Complete tree/dependency inspector, parameter panel, recover/validate feedback |
| Engineering calculations and solvers | Separate optional typed adapter contract; no CAD-core solver coupling | Tools expose explicit inputs/results/provenance and external runtime status | GUI presents setup/results; shared CAD layer handles only CAD artifacts and references |
| Import/export/manufacturing | Typed format/export operation, units/options and result verification | Existing STEP; future verified formats and exchange | GUI file workflows and export settings through same typed operation |
| Agent workflows | State snapshots, action capabilities, goal/progress and errors | Method A direct tools; future workflow/status surfaces; optional Method B tools | Human review/control, current model state and progress; no duplicate planning engine |

The existing GUI does not equal feature parity merely because some operation
buttons are present. Parity should be measured per capability family and include
inspection, errors, validation and workflows, not a mechanically identical list
of controls.

## Engineering design scenarios

These are decomposed capability targets, not assertions of present product scope.

### Indirect water bath heater / CGS assembly

1. Model vessel/enclosure as reusable parametric parts with wall thickness,
   openings/nozzles and mounting interfaces.
2. Build heating coil and piping from constrained profiles, sweep/pipe features,
   bend/connection rules and segment-level metadata.
3. Build burner, heat-transfer and service components as independent components
   with service clearances and replaceable interfaces.
4. Create supports, brackets and connectors, then assemble with explicit frames,
   joints and inspectable access envelopes.
5. Add design tables and unit-aware engineering calculations for duty, heat
   transfer, flow, pressure drop, vessel sizing and code checks as separate
   services; trace inputs, assumptions and versions.
6. Export analysis-ready geometry and manufacturing exchange only after explicit
   defeaturing, mesh/solver checks, drawings and manufacturing review.

Foundations not yet demonstrated: constrained sketches, pipe/loft operations,
stable references, robust attachment/placement frames, native assembly automation,
clearance/connection state, material/configuration metadata, engineering
calculations, thermal/fluid solvers and manufacturing workflows.

### Jet engine

1. Create parameterized inlet/casing sections and reusable compressor/turbine
   blade/stator components; do not represent a stage as an unstructured solid.
2. Model shaft/bearing interfaces and combustion chamber/nozzle parts with
   parameterized revolved/lofted/swept geometry.
3. Compose compressor stages, shafts, bearings, combustor, turbine stages and
   exhaust into subassemblies with explicit coaxial frames, revolute/rigid joints,
   clearances and replaceable components.
4. Track material, temperature, operating condition and configuration metadata.
5. Keep aerodynamic, combustion, rotor dynamics, thermal, structural, fatigue and
   manufacturing assessments in versioned solver/calculation adapters.

Foundations not yet demonstrated: robust loft/pipe and surface workflows,
constraints/design tables, large assembly performance, stable coaxial references,
native joint solving automation, material/condition data, and independently
validated simulation coupling. A visually plausible assembly is not a validated
engine.

## Dependency-aware roadmap

Complexity: **Small**, **Medium**, **Large**, or **Research-heavy**; no calendar
estimates are implied. Each milestone is delivered through the same sequence:
research/API verification, shared contract, FreeCAD implementation, MCP adapter,
GUI exposure, focused unit/backend tests, real FreeCAD 1.1.4 tests and
documentation. Features already implemented are not repeated as new work.

### M12 — Capability evidence and tool discoverability

- **Complexity:** Medium.
- **Status:** Complete (bounded scope).
- **Scope:** documented reproducible capability evidence, clarified schemas/descriptions for already registered operations, improved tool/capability discoverability. No new CAD operations, no broad state-management architecture, no duplicate CAD abstraction.
- **Dependencies:** current typed tool and runner registry; installed 1.1.4 probe environment.
- **FreeCAD APIs:** `Version`, module/workbench importability, `DocumentObject` properties, `Shape` validity, `Sketcher` solver fields, `PartDesign` type creation and Assembly resources.
- **Shared/MCP:** documented an evidence format (`PROJECT_MEMORY/EVIDENCE.md`) and improved current operation descriptions/discovery for schemas, units, bounds and prerequisites. No new shared state contracts added.
- **GUI:** existing shared dispatch kept; no GUI redesign in M12.
- **Taiga:** no required M12 changes; later integration consumes operation schemas only after its own state/action boundary is designed.
- **Tests/acceptance (all passing):**
  - `tests/test_m12_capability_discovery.py` — operation/tool registration parity, unknown-operation rejection, tool descriptions, input schema validity.
  - `tests/test_modeling_tool_params.py` — schema coverage extended from 22 to 41 `*Params` models (all registered operations).
  - Probe logs distinguish import/property/recompute/validity/reopen/edit evidence (see `PROJECT_MEMORY/EVIDENCE.md`).
  - Negative operations are rejected; tests do not mistake mocks for native evidence.
- **Risks:** native feature API may be GUI-only or differ by object context; capability tables can become stale and must include FreeCAD version.

### M13 — Document and model lifecycle

- **Complexity:** Large.
- **Scope:** complete tree/object/dependency inspection; object metadata and
  stable IDs; safe edit/delete/reorder and explicit Body.Tip management; revision
  snapshots, recompute reporting, persistence/reopen validation, recovery and
  rollback. Include bounded/context-efficient state summaries for agents.
- **Dependencies:** M12 state and operation contracts; existing workspace,
  lock, save/reopen, `list_objects`, `inspect_object`, `recompute` paths.
- **FreeCAD APIs:** `Document.Objects`, `InList`/`OutList`, `Group`, `Body.Tip`,
  `removeObject`, `recompute`, `saveAs`, `openDocument`, object properties and
  links (verify exact semantics per object type).
- **Shared/MCP:** lifecycle operations and concise dependency/state responses;
  preserve single document ownership, locking and atomic persistence.
- **GUI (essential lifecycle UI):** provide the model tree and dependency view
  needed for safe lifecycle work, plus basic edit/delete/reorder actions,
  recovery, recompute status and validation messages through the shared layer.
  Defer advanced selection workflows and large-model optimization to M18.
- **Taiga:** establishes fresh state snapshots for later action selection.
- **Tests/acceptance:** focused unit/schema tests; real create-edit-recompute-
  save-close-reopen-edit tests; Body.Tip and dependencies are correct; injected
  failure preserves prior artifact and returns a structured error; GUI manual
  test confirms it addresses only its configured workspace document.
- **Risks:** deleting/reordering dependency-linked features can invalidate
  models; active-document ambiguity in GUI; identity stability across reopen.

### M14 — Sketcher constraints and profile foundation

- **Complexity:** Large.
- **Scope:** add geometric/dimensional constraint inspection and editing, solver
  status/DoF, fully/under-constrained reporting, redundant/conflicting constraint
  handling, support/attachment and external geometry, profile closure/selection,
  and multi-profile workflows. Preserve geometry identity and indices where
  referenced.
- **Dependencies:** M12 operation/result contracts; M13 dependencies, references
  and rollback.
- **FreeCAD APIs:** `Sketcher::SketchObject.addConstraint`, `delConstraint`,
  `Constraints`, `Geometry`, `FullyConstrained`, `solve`, and verified DoF/solver
  status APIs; `AttachmentSupport`/`Support` and external geometry APIs require
  per-feature probes.
- **Shared/MCP:** structured constraints by type, geometry index/subelement,
  value/unit and solver result; reject unsafe edits with explicit constraint
  dependency information.
- **GUI:** constraint list/solver state, selection-aware creation/editing and
  profile diagnostics using the same shared calls.
- **Taiga:** supports valid sketch actions and better structured state.
- **Tests/acceptance:** under-, fully-, redundant/conflicting, dimensional,
  attached and external-geometry cases; valid downstream Pad/Pocket; edit and
  save/reopen preserving intended geometry/constraints; real 1.1.4 GUI review of
  selection and solver feedback.
- **Risks:** solver APIs and failure messages; topology and geometry index
  stability; generated profiles may be closed but not suitable for a solid.

### M15 — Part and PartDesign feature families

- **Complexity:** Large / Research-heavy per feature family.
- **Scope:** native Additive Pipe + Subtractive Pipe as one family, Additive Loft
  + Subtractive Loft as another, then Hole and selected advanced Part operations
  (sweep/revolution/loft/shell/refinement/repair) based on verified need. Do not
  reimplement existing booleans, Pad, Pocket, Groove, Revolution, Fillet,
  Chamfer, patterns, Mirrored or Thickness.
- **Dependencies:** M12 capability evidence, M13 body/dependency lifecycle, M14
  profile/solver foundation for sketch-driven features.
- **FreeCAD APIs:** native `PartDesign::*Pipe`, `*Loft`, `Hole` object types and
  properties; Part `makePipeShell`, loft/sweep/shape refinement methods only
  after actual 1.1.4 probes validate parameters and outcomes.
- **Shared/MCP:** explicit profiles, spine/sections, solid mode, orientation,
  fuse/cut policy and errors; typed operation schemas.
- **GUI:** feature-family panels using selection-derived references passed to the
  shared operation layer.
- **Taiga:** enumerate only actions with valid schemas and prerequisites.
- **Tests/acceptance:** real recompute and valid expected solid; body membership
  and Body.Tip; invalid profile/spine/section failures; parameter edit and
  save/reopen; rollback and GUI/MCP result parity.
- **Risks:** native property variation, self-intersections, invalid profiles,
  fragile topological references and computational cost.

### M16 — Topology references and shape robustness

- **Complexity:** Large / Research-heavy.
- **Scope:** robust subelement resolution and provenance, geometry repair and
  refinement workflows, compound/multi-solid policies, shape splitting and
  classification, downstream dependency impact and operation recovery.
- **Dependencies:** M13 lifecycle and M15 feature variety; selectively parallel
  with M14 only for reference research.
- **FreeCAD APIs:** `Shape.check`, `isValid`, `fix`/`removeSplitter` and
  subelement access are candidate APIs, not blanket guarantees; test each on
  representative invalid/valid geometry. Avoid claiming universal stable
  topological naming.
- **Shared/MCP:** references include owner object, element kind/name and
  verification context; stale references fail closed with repair guidance.
- **GUI:** highlight/select affected edges/faces, show validation and repair
  previews via shared operations.
- **Taiga:** return reference validity and operation feasibility as state.
- **Tests/acceptance:** real reference survives intended edits/reopen or fails
  clearly; repair never silently changes intended shape; boolean/multi-solid
  results reported accurately.
- **Risks:** geometric kernels and topology labels can change after recompute;
  automatic repair may change design intent.

### M17 — Native Assembly foundation (hard research gate)

- **Complexity:** Research-heavy, then Large if the gate passes.
- **Scope:** first prove FreeCAD 1.1.4 native assembly hierarchy, component/link
  insertion, coordinate frames, representative rigid and moving joints, solver/
  update behavior, recompute, save/reopen and failure inspection. Only after the
  gate passes design hierarchy, subassembly, replacement and validation APIs.
- **Dependencies:** M12 probes and M13 object/link/lifecycle support; M16
  references for joint connectors.
- **FreeCAD APIs:** observed module `Assembly`, `Assembly::AssemblyObject`,
  `Assembly::JointGroup`, `App::Link`; installed `JointObject.py` declares Fixed,
  Revolute, Cylindrical, Slider, Ball, Distance, Parallel, Perpendicular, Angle,
  RackPinion, Screw, Gears and Belt. These names do not prove scripted creation
  or solving: `Assembly::Joint` is not directly creatable by `Document.addObject`
  in the console probe.
- **Shared/MCP:** no Assembly schema before end-to-end runtime evidence. If the
  gate passes, typed hierarchy/component/joint/solve/inspect operations through
  the normal allowlisted child. If not, keep this milestone research-only.
- **GUI:** native Assembly workbench may provide interaction/visualization;
  FreeCAD MCP GUI should expose parity over the shared layer, not clone native
  joint logic or bypass document ownership.
- **Taiga:** assembly actions only after stable state/action schemas and safe
  solver feedback.
- **Tests/acceptance:** actual native hierarchy and link placements recompute;
  representative joint solve/update behavior is measurable; saved assembly
  reopens with links/joints and correct placements; invalid joint/reference
  yields structured failure. GUI and headless results explicitly distinguished.
- **Risks:** GUI-created Python proxies, GUI-bound solver, workbench lifecycle,
  licensing/version drift, single-root constraints and native solver semantics.
  Hard gate: no abstraction ships if core behaviors remain unverified.

### M18 — Advanced GUI usability and workflow parity

- **Complexity:** Large.
- **Scope:** build on M13's essential lifecycle UI with graphical and
  selection-aware workflows, refined parameter editing, broader operation-family
  parity, assembly visualization if M17 passes, batched/context-aware inspection,
  and large-model performance. Do not defer the basic tree/dependency/edit/delete/
  reorder/recovery interface from M13 to this milestone.
- **Dependencies:** M12 shared operation metadata; M13 lifecycle; M14/M15
  capabilities; M17 only for assembly UI.
- **FreeCAD APIs:** FreeCADGui selection/view APIs and Qt controls are GUI-only;
  probe GUI version/API and ensure selections resolve to stable document/object/
  subelement references.
- **Shared/MCP:** shared layer remains authoritative for document mutation;
  MCP schemas stay available regardless of GUI state.
- **GUI:** dockable panel as a first-class product interface; no CAD algorithms
  embedded in widgets. Maintain selection ownership and workspace document
  boundaries.
- **Taiga:** optional progress and action explanations can be shown but not
  required for direct GUI use.
- **Tests/acceptance:** headless panel import/syntax tests, GUI manual matrix for
  selection, controls, error state, reopen and big documents; MCP and GUI invoke
  same operation/result contract.
- **Risks:** Qt/FreeCADGui API differences, UI blocking during child calls,
  scaling and active-document confusion.

### M19 — Method A agent workflows

- **Complexity:** Large.
- **Scope:** capability/tool discovery, concise structured model state, multi-step
  execution/progress, inspect-after-action, explicit validation/recovery loops,
  stable artifact/revision references and benchmark fixtures.
- **Dependencies:** M12 schemas, M13 state/lifecycle, M14-M16 model operations.
- **FreeCAD APIs:** reuse only verified operation APIs; no new GUI-only CAD
  dependency in the MCP parent.
- **Shared/MCP:** deterministic operation and workflow orchestration contracts;
  bounded calls and safe operation allowlists.
- **GUI:** workflow progress, state diffs, validation and manual override share
  execution results.
- **Taiga:** not required for Method A; later comparison baseline.
- **Tests/acceptance:** MCP client end-to-end sequences, failure recovery, state
  freshness, reproducibility and valid persisted artifacts; report geometric
  success separately from engineering correctness.
- **Risks:** token/context cost, long tasks, unclear goal acceptance criteria and
  versioned operation schemas.

### M20 — General Taiga-S1 decision/execution boundary

- **Complexity:** Large / Research-heavy.
- **Scope:** real CAD state adapter, state normalization, valid action
  enumeration, action-to-typed-operation mapping, iterative Taiga selection,
  continue/retry/finish policy, fresh execution feedback, timeout/rollback,
  progress and termination. The external agent translates intent; Taiga owns
  next-action decisions; MCP remains executor.
- **Dependencies:** M12 state/schema contract, M13 lifecycle, M19 stable Method A
  executor; relevant M14-M17 operations where appropriate. Taiga model/API
  compatibility research is an explicit gate.
- **FreeCAD APIs:** no direct Taiga imports into core; use existing isolated
  runtime/child patterns and verified Taiga schema/Policy APIs.
- **Shared/MCP:** explicit goal, state, action candidate and execution result
  contracts; Taiga optional and configured locally only. Never map an action id
  to an unrelated primitive merely to make a loop execute.
- **GUI:** optional goal/progress/decision view through a separate adapter;
  direct GUI and Method A remain usable without Taiga.
- **Tests/acceptance:** fake loop/unit tests plus opt-in real FreeCAD/Taiga tests
  with explicit local weights; every action is a valid typed operation; state is
  refreshed after execution; failure and stop conditions persist no false
  success; no network downloads; compare Method A/B on same goals and criteria.
- **Risks:** action vocabulary mismatch, model confidence/calibration, goal
  parameterization, model license/runtime size and nondeterminism.

### M21 — Reusable engineering components and design tables

- **Complexity:** Large.
- **Scope:** reusable parametric component templates, connection/interface
  contracts, design tables/configurations, standard-part libraries and material
  metadata. Build heater and jet-engine assemblies as compositions of parts and
  subassemblies, not single operations.
- **Dependencies:** M13 lifecycle, M14 sketches, M15 features, M17 Assembly gate
  for mechanically constrained assemblies; M16 stable references.
- **FreeCAD APIs:** document objects, expressions/spreadsheets, links and custom
  properties must be probed for persistence/version behavior.
- **Shared/MCP:** typed library lookup/instantiate/configure operations with
  provenance; no silent external library downloads.
- **GUI:** browse/configure/review components, interfaces, material metadata and
  clearances through shared APIs.
- **Taiga:** select among vetted component workflows/actions; no bypass of
  constraints.
- **Tests/acceptance:** configured instances remain parametric and independent;
  interface checks and save/reopen pass; library source/version is traceable.
- **Risks:** licensing, units/material provenance, configuration explosion and
  false assumptions about standards compliance.

### M22 — Solver and manufacturing adapters

- **Complexity:** Research-heavy.
- **Scope:** isolated adapters for FEM/structural, thermal, CFD/fluid,
  piping/heat-transfer calculations and manufacturing exchange. Define CAD
  preparation, solver input/output, mesh/results provenance and export checks.
- **Dependencies:** M12 integration contracts, M13 artifact lifecycle, M16 shape
  quality, M21 materials/configuration; solver-specific research before choosing
  concrete integrations.
- **FreeCAD APIs/tools:** FEM module imports in 1.1.4, but solver executable,
  supported solvers, package availability, mesh/material/boundary requirements,
  headless execution and results remain unverified. CFD may require external
  tools/workbenches. STEP export is implemented; manufacturing formats need
  separate format-level probes.
- **Shared/MCP:** optional external service contracts with explicit versions,
  paths, inputs, assumptions, logs, results and cancellation; solver failure
  cannot corrupt source CAD artifact.
- **GUI:** setup/result review, visualization and explicit warnings about
  assumptions; core CAD remains usable with no solver installed.
- **Taiga:** may select a vetted analysis workflow but cannot certify results.
- **Tests/acceptance:** analytical benchmark problems, mesh/solver convergence
  criteria appropriate to each solver, provenance and round-trip tests; separate
  geometry-valid, solver-completed and engineering-reviewed statuses.
- **Risks:** licensing/deployment, solver convergence, material/code compliance,
  units, environmental dependencies and safety-critical overclaim.

## Testing and acceptance strategy

Mocks test our contracts; only the installed runtime proves native FreeCAD
behavior. Keep tests labeled and report skips precisely.

| Test layer | Purpose and acceptance evidence |
| --- | --- |
| Unit tests | Pure schemas, pathing, validation, geometry-independent logic, typed errors and state transforms |
| Schema/tool registration | MCP names, descriptions, required/optional values, bounds/units and structured response contracts match registered operations |
| Mocked bridge/backend tests | Delegation, envelope/error mapping, allowlist and rollback orchestration; explicitly not native API proof |
| Real FreeCAD 1.1.4 integration | Launch actual `freecadcmd`; create/recompute native objects; assert non-null valid expected shapes, Body membership, links and measurements |
| Save/reopen | Persist, close, reopen, inspect feature dependencies/references and recompute; no constructor-only success criteria |
| Parametric edit | Change a feature input after reopen; recompute; assert changed expected geometry and valid dependency/tip state |
| Failure/recovery | Invalid geometry/reference, thrown error, timeout where feasible, failed recompute; verify structured error and original artifact preserved |
| GUI manual/integration | Real FreeCAD GUI Workbench startup, selection mapping, dock panel operation, validation/error presentation, model tree and configured document ownership |
| MCP end-to-end | Real MCP stdio client discovers tools and performs inspect/create/edit/validate/save sequences through the child |
| Taiga workflow | Fake state/action/runtime tests for loop mechanics; separately gated real local Taiga/FreeCAD/weights tests; no implicit downloads |
| Method A vs Method B | Same goal fixtures, initial state, operation set and acceptance rules; record completion, geometry, failures, operation count and elapsed time. Do not present small benchmark evidence as engineering validation |

For each milestone, acceptance must cover focused unit/schema/backend tests,
actual FreeCAD tests for changed native behavior, error/rollback behavior, and
save/reopen plus parametric edit whenever the feature is persistent/parametric.
GUI manual coverage is required for GUI interaction. Coding agents run focused
tests directly related to changed code; the maintainer runs the full suite unless
explicitly requested otherwise. A missing executable/runtime is a skipped test,
not a pass.

## Research backlog and risks

- Complete a clean headless Assembly probe using the installed Python proxy and,
  where necessary, a disposable GUI session. Determine how the installed native
  workbench creates joints, solves, persists and restores proxies. Keep M17
  blocked until this is proven.
- Inspect actual FreeCAD 1.1.4 properties/enums and representative successful
  recomputes for Pipe/Loft/Hole/other PartDesign features; record failure
  conditions and save/reopen behavior.
- Probe Sketcher solver status/DoF and support/attachment/external geometry
  behavior, including geometric and dimensional constraint edits.
- Verify feature-specific GUI/headless API split, import/export formats and
  available solver executables/dependencies without downloading or installing.
- Investigate robust topology references and safe replacement/deletion under
  dependency graphs; do not assume edge/face indices are stable.
- Investigate operation timeout cleanup and concurrent workspace/document
  behavior without weakening current locks/rollback guarantees.
- Add trustworthy engineering standards/material/calculation provenance and
  independent engineering review before describing any output as designed,
  validated or safe.

## Recommended next implementation milestone

After the documentation milestone is reviewed, start **M12 — Evidence,
capability contracts, and discoverability** as a tightly scoped implementation
milestone. It unlocks verified feature prioritization and structured state for
M13, Sketcher, Assembly research, and Taiga without prematurely adding CAD
operations. The native Assembly implementation remains gated on its explicit
real-FreeCAD proof.
