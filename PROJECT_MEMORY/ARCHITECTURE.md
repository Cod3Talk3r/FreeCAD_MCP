# Architecture

Layered design: MCP server (`server.py` + `tools/`) → `freecad_bridge/` →
FreeCAD; optional `integrations/` behind a dependency-free contract.

## Layers and module responsibilities

- `src/freecad_mcp/server.py` — MCP server construction and stdio entry point;
  never imports FreeCAD.
- `src/freecad_mcp/config.py` — environment-based (`FREECAD_MCP_*`) settings with
  safe defaults; fails fast on invalid values.
- `src/freecad_mcp/tools/` — MCP tool definitions and typed input validation
  (diagnostics, freecad, modeling, taiga groups; `register_all`).
- `src/freecad_mcp/freecad_bridge/` — communication with the FreeCAD process
  (bridge, discovery, errors, runner, workspace).
- `src/freecad_mcp/integrations/base.py` — dependency-free optional backend
  contract; `integrations/taiga/` — adapter, isolated runtime, child runner,
  guarded workflow.

## Key invariants

1. Package imports without FreeCAD installed.
2. Explicit validated operations only; no arbitrary Python execution in FreeCAD.
3. Local-only, bounded, structured-error bridge.
4. Optional integration isolation (no parent import of Taiga/Torch).
5. Evidence over assumption; upstream APIs verified, never invented.

## Bridge design

One-shot child process: MCP tool → `FreeCadBridge.run(op, params)` writes
`FREECAD_MCP_REQUEST`/`FREECAD_MCP_RESPONSE` JSON envelopes via a temp directory →
`freecadcmd` child runs fixed `runner.OPERATIONS` → parsed result returned.

## Deferred / not implemented

- Long-lived FreeCAD process; `validation/` module (referenced in docs, absent);
- Live state capture, action execution, PartDesign layer; Method B execution;
  Method A vs Method B comparison framework (M7).
