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

Phase 4 introduces `freecad_mcp.app`, a standard-library application boundary
shared by interface adapters. Its request, result, error, and backend status
contracts do not depend on MCP, Qt, FreeCAD, Taiga, or an LLM. Direct operations
delegate to the existing bridge; Taiga builds delegate to the existing build
runtime. The MCP tools retain their public schemas and translate at the boundary.
The existing optional scoring interface remains separate from build execution.

The current execution adapters are headless. In-process FreeCAD execution,
active-document ownership, document inspection, and the GUI workbench remain
future work; the contracts do not imply that either backend can safely operate
on an already-open document. No HTTP service is required.
The direct adapter shares the existing MCP input checks; FreeCAD-side validation
remains authoritative for document and geometry operations. Backend status
reports availability as unknown until probed. Direct execution preserves existing
bridge exceptions, while Taiga build failures return structured result data.

```
        AI agent (e.g. OpenCode)
                 |
                 |  MCP (stdio)
                 v
   +-----------------------------------+
   |  server.py                        |  MCP server: registers tools
   |  tools/                           |  tool definitions + input validation
   +-----------------------------------+
                 |
                 v
   +-----------------------------------+
   |  freecad_bridge/                  |  talks to the real FreeCAD process
   +-----------------------------------+
                 |
                 v
   +-----------------------------------+
   |  validation/                      |  geometry + model correctness checks
   +-----------------------------------+

   integrations/base.py  ->  optional backends (integrations/taiga/)
```

### Module responsibilities

| Path | Responsibility |
| ---- | -------------- |
| `src/freecad_mcp/server.py` | MCP server initialization and tool registration. |
| `src/freecad_mcp/config.py` | Validated configuration and environment settings. |
| `src/freecad_mcp/tools/` | MCP tool definitions and typed input validation. |
| `src/freecad_mcp/freecad_bridge/` | Communication with the FreeCAD application. |
| `src/freecad_mcp/validation/` | Geometry and model correctness checks. |
| `src/freecad_mcp/integrations/base.py` | Dependency-free contract for optional planning backends. |
| `src/freecad_mcp/integrations/taiga/` | Optional Taiga-S1 adapter, isolated subprocess runtime, and child runner (availability and scoring only; no execution). |

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

## Optional integrations (Milestones 5–6)

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

This is not a PartDesign execution layer. The bridge still exposes only its
fixed operations, and Taiga's worker/RPC and GUI socket runtime are not adopted.
Live state capture, action enumeration, action execution, and a Method B build
path remain deferred until a PartDesign boundary is designed and verified
against real FreeCAD.

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
