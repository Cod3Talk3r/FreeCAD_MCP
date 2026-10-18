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
| `src/freecad_mcp/integrations/base.py` | Minimal interface for optional planning/generation backends. |
| `src/freecad_mcp/integrations/taiga/` | Optional Taiga adapter (only after verifying its interface). |

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
