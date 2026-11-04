# Architecture

Layered design: MCP server (`server.py` + `tools/`) → shared `app/` contracts
and backend adapters → existing `freecad_bridge/` or Taiga build runtime →
FreeCAD. GUI and in-process execution are not implemented.

## Layers and module responsibilities

- `src/freecad_mcp/server.py` — MCP server construction and stdio entry point;
  never imports FreeCAD.
- `src/freecad_mcp/config.py` — environment-based (`FREECAD_MCP_*`) settings with
  safe defaults; fails fast on invalid values.
- `src/freecad_mcp/tools/` — MCP tool definitions and typed input validation
  (diagnostics, freecad, modeling, taiga groups; `register_all`).
- `src/freecad_mcp/app/` — MCP-independent application contracts and direct
  and Taiga build adapters; **document & model inspection contracts and direct
  inspection adapter**; execution remains delegated to existing runtimes.
- `src/freecad_mcp/freecad_bridge/` — communication with the FreeCAD process
  (bridge, discovery, errors, runner, workspace, taiga_runner).
- `src/freecad_mcp/freecad_bridge/taiga_runner.py` — FreeCAD-side Taiga build
  runner (child process only; imports `FreeCAD`/`freecad_s1` lazily; never
  imported by the MCP server).
- `src/freecad_mcp/integrations/base.py` — dependency-free optional backend
  contract; `integrations/taiga/` — adapter, isolated runtime, child runner,
  guarded workflow.
- `src/freecad_mcp/integrations/taiga/child.py` — parent-side `TaigaChildRunner`
  (isolated FreeCAD subprocess transport via `--pass` argv; no env-var
  dependency).
- `src/freecad_mcp/pathing.py` — shared WSL2↔Windows path translation
  (`wslpath` as argument list; no shell interpolation).

## Key invariants

1. Package imports without FreeCAD installed.
2. Explicit validated operations only; no arbitrary Python execution in FreeCAD.
3. Local-only, bounded, structured-error bridge.
4. Optional integration isolation (no parent import of Taiga/Torch).
5. Evidence over assumption; upstream APIs verified, never invented.
6. `freecad_bridge/runner.py` remains Method A only — no Taiga execution logic.
7. FreeCAD child alone owns the document lock; parent never touches it.

## Bridge design

One-shot child process: MCP tool → `FreeCadBridge.run(op, params)` writes
`FREECAD_MCP_REQUEST`/`FREECAD_MCP_RESPONSE` JSON envelopes via a temp directory →
`freecadcmd` child runs fixed `runner.OPERATIONS` → parsed result returned.

## Taiga build transport (Method B)

`taiga_build` tool → `BuildRuntime` → `TaigaChildRunner` → `freecadcmd.exe -c
"runpy.run_path(...)" --pass <request.json> <response.json>`. Positional args
after `--pass` avoid FreeCAD's `--response-file` interception of `--response`
and `freecadcmd.exe`'s stripping of custom environment variables.

## Deferred / not implemented

- Long-lived FreeCAD process; `validation/` module (referenced in docs, absent);
- Live state capture, action execution, PartDesign layer; Method A vs Method B
  comparison framework (M7).
- Persistent FCStd artifact path; torch/model scoring; real Taiga weights.

---
