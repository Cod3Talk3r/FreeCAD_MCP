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
| `src/freecad_mcp/integrations/taiga/` | Optional Taiga-S1 adapter (availability and scoring only; no execution). |

## Design principles

1. **No arbitrary code execution by default.** The MCP surface exposes explicit,
   validated modeling operations with typed parameters. Running LLM-generated
   Python inside FreeCAD is not the default tool design.
2. **Import-safe without FreeCAD.** The package (and the MCP server skeleton)
   must import and run on a machine where FreeCAD is not installed. All FreeCAD
   globals (`FreeCAD`, `Part`, ...) live behind `freecad_bridge`.
3. **Local-only bridge.** Any IPC/listener the bridge introduces binds to
   localhost and never exposes unauthenticated remote code execution.
4. **Optional integration isolation.** `integrations/taiga/` may import Taiga-only
   dependencies; nothing in the core path may. Uninstalling Taiga must not break
   Method A.
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

Deferred: a long-lived FreeCAD process with an in-memory document is not needed
for version diagnostics. It will be reconsidered if document state must persist
across calls without saving to disk.

## Optional integrations (Milestone 5)

`integrations/base.py` defines a small, dependency-free contract for optional
backends:

- `IntegrationStatus` — available or unavailable, with a structured reason;
- `PlanStep` — an explicit, declarative step (data, never an executed command);
- `PlanningResult` — either a non-empty generated plan or a structured
  unavailable result (an empty "success" is rejected);
- `PlanningBackend` — `status()` plus `plan(goal)`.

The base module imports only the standard library. Importing the core package,
`integrations`, or `integrations.taiga` must not import FreeCAD, Torch, or
Taiga; the adapter's optional dependencies are loaded only when it is used.

`integrations/taiga/` implements the adapter against the verified upstream API
(see [`taiga-s1.md`](taiga-s1.md)):

- `status()` reports missing Taiga, missing Torch, unsupported Python, or an
  available installation.
- `score_actions()` calls Taiga's `Policy.score` for a supplied JSON state,
  goal, and valid action list. It returns typed probabilities and never
  executes a command.
- `plan()` returns `RUNTIME_CONTEXT_REQUIRED`. Taiga scores one next command
  from a live state and needs an execution loop to build a multi-step plan, so
  M5 deliberately does not fabricate one.

This is not a PartDesign execution layer. The bridge still exposes only its
fixed operations, and Taiga's worker/RPC and GUI socket runtime are not adopted.
A Method B build path remains deferred until a PartDesign boundary is designed
and verified against real FreeCAD.

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
