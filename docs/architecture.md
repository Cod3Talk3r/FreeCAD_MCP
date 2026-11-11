# Architecture

This document describes the intended structure of `freecad-mcp` and the design
principles that constrain it. It is deliberately lightweight: components are
added only when a milestone needs them.

## Goals

- Let an AI agent create, inspect, validate, and modify parametric models in a
  real FreeCAD instance over MCP.
- Keep the direct path (**Method A**) fully functional on its own.
- Keep the optional Taiga integration (**Method B**) isolated behind an adapter,
  so the project never hard-depends on it.

## Layered design

Transport, CAD execution, validation, and optional integrations are separate
concerns and must not leak into each other:

`freecad_mcp.app` is a standard-library application boundary shared by interface
adapters. Its request, result, error, inspection and backend-status contracts do
not depend on MCP, Qt, FreeCAD, Taiga, or an LLM. Direct operations and document
inspection delegate to the existing bridge; Taiga builds delegate to the
isolated build runtime. MCP tools translate at the boundary.

The native FreeCAD Workbench and dockable panel are implemented and call
`freecad_mcp.freecad_bridge.runner.handle_request`, using the same explicit
operation registry as the child bridge. The GUI does not need a network service
and must not create an independent CAD algorithm path. Its workspace document
ownership and locking remain governed by the same shared execution behavior.
GUI-only selection and view state should be translated to explicit operation
references before mutation.

The existing direct and Taiga build runtimes are headless child-process paths;
the GUI is a separate in-FreeCAD adapter. The contracts do not imply that the
headless backend safely edits an arbitrary already-open document. Active-document
ownership must remain explicit. FreeCAD-side operation validation remains
authoritative for native document and geometry behavior. Backend availability
is unknown until probed, and structured operation results/errors should be
preserved across MCP and GUI.

```
         External AI agent                FreeCAD GUI Workbench
                  |                                |
                  | MCP (stdio)                    | shared operation dispatch
                  v                                v
   +-----------------------------------+
   |  server.py                        |  MCP server: registers tools
   |  tools/                           |  tool definitions + input validation
   +-----------------------------------+
                 |
                  |                                |
                  +---------------+----------------+
                                  v
   +-----------------------------------+
   |  freecad_bridge/                  |  talks to the real FreeCAD process
   +-----------------------------------+
                 |
                 v
   +-----------------------------------+
   |  runner.OPERATIONS                |  fixed, validated FreeCAD operations
   +-----------------------------------+

   integrations/base.py  ->  optional Taiga / future solver adapters
```

### Module responsibilities

| Path | Responsibility |
| ---- | -------------- |
| `src/freecad_mcp/server.py` | MCP server initialization and tool registration. |
| `src/freecad_mcp/config.py` | Validated configuration and environment settings. |
| `src/freecad_mcp/tools/` | MCP tool definitions and typed input validation. |
| `src/freecad_mcp/freecad_bridge/` | Communication with the FreeCAD application. |
| `src/freecad_mcp/app/` | MCP-independent operation, backend, and inspection contracts/adapters. |
| `src/freecad_mcp/integrations/base.py` | Dependency-free contract for optional planning backends. |
| `src/freecad_mcp/integrations/taiga/` | Optional isolated Taiga status/scoring/build and guarded workflow. |
| `Mod/freecad_mcp_gui/` | Native FreeCAD Workbench and panel; UI adapts inputs and dispatches shared operations. |

## Design principles

1. **No arbitrary code execution by default.** The MCP surface exposes explicit,
   validated modeling operations with typed parameters. Running LLM-generated
   Python inside FreeCAD is not the default tool design.
2. **Import-safe without FreeCAD.** The package (and the MCP server skeleton)
   must import and run on a machine where FreeCAD is not installed. All FreeCAD
   globals (`FreeCAD`, `Part`, ...) live behind `freecad_bridge`.
3. **Local-only bridge.** Any IPC/listener the bridge introduces binds to
   localhost and never exposes unauthenticated remote code execution.
4. **Optional integration isolation.** `integrations/taiga/` may use Taiga-only
   dependencies, but only inside its child runner; nothing in the parent core
   path may import them. Uninstalling Taiga must not break Method A.
5. **Evidence over assumption.** FreeCAD APIs, the MCP SDK API, the OpenCode MCP
   configuration format, and Taiga-S1's real interface are verified before being
   relied upon — never invented.

## FreeCAD bridge (Milestone 2)

`freecad_bridge/` talks to a real FreeCAD installation through a **one-shot child
process**, chosen because it is local-only and needs no persistent service:

```
MCP tool  ->  FreeCadBridge.run(op, params)
                 |  writes request.json, sets FREECAD_MCP_REQUEST / _RESPONSE
                 v
             freecadcmd.exe  runner.py     (FreeCAD's own Python 3.11)
                 |  dispatches op -> writes response.json
                 v
             FreeCadBridge  ->  parsed result  ->  MCP tool result
```

Key properties:

- **Local only.** A child process on this machine; no sockets, no network, no
  remote code execution surface.
- **Explicit operations.** `runner.py` holds the complete set of allowed
  operations (`OPERATIONS`). The bridge cannot run arbitrary Python inside
  FreeCAD; it sends `{"op": "...", "params": {...}}` only.
- **Bounded.** Every operation has a timeout (default 60 s, `FREECAD_MCP_TIMEOUT_SECONDS`);
  the child is killed on expiry.
- **No FreeCAD imports in the parent.** Only the child (`runner.py`) imports
  `FreeCAD`/`Part`, and it does so *lazily inside handlers*, so its dispatch and
  envelope logic stay unit-testable on the host interpreter.
- **Structured errors.** `FreeCadNotFoundError`, `FreeCadTimeoutError`,
  `FreeCadExecutionError`, `FreeCadResponseError`, `FreeCadOperationError` let the
  MCP layer return a useful result instead of a traceback.

Discovery (`discovery.py`) resolves the console executable in order: explicit
setting → `FREECAD_MCP_FREECAD_EXECUTABLE` → `PATH` → conventional install
locations → (Windows) Uninstall registry entries. An explicit setting is
authoritative: a wrong path reports "not found" rather than silently using a
different FreeCAD.

FreeCAD quirk worth remembering: it runs a script file with `__name__` set to the
*module name*, not `"__main__"`, so the runner keys off the request environment
variable rather than the usual entry guard.

## Document & model inspection (Milestone 4 M2)

`src/freecad_mcp/app/inspection.py` defines MCP-independent inspection
contracts shared by interface adapters:

- **`ShapeInfo`** — geometry and validity of a shape (type, valid, volume,
  dimensions, counts). All fields optional; unset when unavailable.
- **`ObjectInspection`** — a single object: name, type, label, plus
  `has_shape`/`shape_null`/`shape` to distinguish no-Shape, null-Shape,
  invalid-Shape, and valid-Shape objects without misclassifying sketches,
  wires, or origin objects as invalid solids.
- **`DocumentInspection`** — document-level inventory: object list, count,
  `all_valid`, metadata side-car.
- **`InspectionRequest` / `InspectionResult`** — typed request/result pair.
- **`InspectionBackend`** — protocol (`inspect(request)`, `status()`).
- **`DirectInspectionBackend`** — adapter wrapping `FreeCadBridge`; reuses
  existing `list_objects`/`inspect_object` bridge operations, reads the
  metadata side-car directly via `workspace`, and never imports FreeCAD in the
  parent process.

Key properties:

- **Parent stays FreeCAD-free.** Only `freecad_bridge/runner.py` (inside the
  FreeCAD child) imports `FreeCAD`/`Part`.
- **No duplicated inspection logic.** MCP tools remain thin adapters; the
  shared backend owns the inspection contracts.
- **PartDesign-aware.** `Body.Tip.Shape` validation remains in the child
  runner; inspection reports what each object actually exposes.
- **Deferred:** in-process GUI execution, active-document ownership, and
  editing/recovery belong to M3/M4.

Deferred: a long-lived FreeCAD process with an in-memory document is not needed
for version diagnostics. It will be reconsidered if document state must persist
across calls without saving to disk.

## Optional integrations (Taiga and future solvers)

`integrations/base.py` defines a small, dependency-free contract for optional
backends:

- `IntegrationStatus` — available or unavailable, with a structured reason;
- `PlanStep` — an explicit, declarative step (data, never an executed command);
- `PlanningResult` — either a non-empty generated plan or a structured
  unavailable result (an empty "success" is rejected);
- `PlanningBackend` — `status()` plus `plan(goal)`.

The base module imports only the standard library. Importing the core package,
`integrations`, `integrations.taiga`, the tools, or the MCP server must not
import FreeCAD, Torch, or Taiga. The Taiga child runner imports those packages
only inside its own process.

`integrations/taiga/` implements the adapter against the verified upstream API
(see [`taiga-s1.md`](taiga-s1.md)). It reuses the bridge's one-shot child-process
pattern instead of Taiga's persistent worker or sockets:

```
taiga_status / taiga_score_actions
  -> TaigaBackend
  -> TaigaRuntime.run(op, params)
       |  writes request.json, sets FREECAD_MCP_TAIGA_REQUEST / _RESPONSE
       v
     configured Taiga Python  runner.py
       |  lazily imports freecad_s1 / torch -> writes response.json
       v
     TaigaRuntime -> validated envelope -> typed MCP result
```

Key properties:

- **Explicit interpreter.** `FREECAD_MCP_TAIGA_PYTHON` must point at a Python
  environment containing Taiga-S1. There is no fallback to the server's or
  FreeCAD's interpreter.
- **One request, one response.** Each operation uses a temporary directory and
  a fresh child process; no sockets, HTTP, or persistent worker.
- **Strict local models.** Scoring requires a local directory with
  `config.json` and `model.safetensors`; repository ids are rejected and nothing
  is downloaded.
- **Bounded and structured.** A timeout (default 120 s), non-zero exit, missing
  or malformed response, or child error maps to `UnavailableReason`, including
  the M6 additions `TIMEOUT` and `RUNTIME_ERROR`.
- **Status without weights.** `taiga_status` checks importability and local model
  files but never loads weights or contacts the network.
- **Scores, not plans.** `taiga_score_actions` ranks supplied actions and never
  executes a command. `plan()` returns `RUNTIME_CONTEXT_REQUIRED` because a
  multi-step plan needs a live state/execution loop.

The core MCP operation runner remains an explicit allowlist. Taiga's general
interactive live-state/action enumeration and per-action typed execution loop
are not provided by the guarded `workflow.py` skeleton. The separate
`taiga_build` path does execute an Expert-driven build in an isolated FreeCAD
child and validates the saved artifact; it is not interchangeable with a general
interactive controller. See [`taiga-s1.md`](taiga-s1.md) and the canonical
[product roadmap](../.kilo/ROADMAP.md).

Future solver integrations (FEM/structural, thermal, CFD/fluid, piping and heat
transfer) belong behind optional typed adapters with explicit runtime/version,
input, output, provenance and failure contracts. They must not import into or
couple the CAD core to a specific solver. Geometry validity and solver
completion are not engineering certification.

## Extension boundaries and invariants

- Keep MCP and GUI as thin adapters over shared typed operation and inspection
  contracts. The FreeCAD child runner owns native API calls.
- Add operations only to the allowlist with explicit schemas, validation,
  structured errors/results, recompute checks and relevant save/reopen tests.
- Preserve document ownership, workspace locks, atomic persistence and rollback
  for mutation paths. Do not introduce a second GUI lock or silently operate on
  an arbitrary active document.
- Report fresh, bounded model state after operations. Do not assume topology
  indices remain stable after feature edits.
- Native Assembly automation is research-gated: availability of the Assembly
  module/types is not proof of a safe headless joint/solver API. Do not design a
  public MCP/GUI abstraction until real FreeCAD 1.1.4 hierarchy, joint, solve,
  recompute and save/reopen behavior has been demonstrated.
- Preserve backward compatibility of public MCP schemas where practical; use
  explicit schema/version changes when behavior must evolve.

The single canonical forward roadmap, including the verified 1.1.4 capability
matrix and milestone acceptance criteria, is [`.kilo/ROADMAP.md`](../.kilo/ROADMAP.md).

## Environment facts (recorded during Milestone 0)

- Development OS: Windows 11 Enterprise, AMD64.
- `uv` 0.12.5, Git 2.50.1.
- FreeCAD 1.1.4 installed at `E:\Softwares\FreeCAD\`:
  - GUI: `bin\freecad.exe`
  - Console: `bin\freecadcmd.exe`
  - Embedded Python: `bin\python.exe` (CPython 3.11.14, conda-forge).
- The system Python (3.13.x) is **not** expected to import `FreeCAD` or `Part`;
  the bridge must use the FreeCAD-provided interpreter.

Paths above are machine-specific and are therefore documented, not committed as
configuration.
