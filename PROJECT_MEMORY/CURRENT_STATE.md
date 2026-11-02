# Current State

## Latest verified state

- HEAD: `396c45a` — `feat: add Taiga-S1 integration foundation` (branch
  `feat/freecad-mcp`).
- Full test suite verified locally: `uv run pytest` → **255 passed, 9 skipped**
  (~24 s, 2026-11-02). The skips are FreeCAD-console-missing (7 tests) and
  opt-in real-Taiga integration tests (2 tests); no FreeCAD / Taiga runtime was
  available in this environment.

## Implemented

- Milestones 0–6 are code-complete:
  - MCP server skeleton + `server_info` / `freecad_status` diagnostics.
  - FreeCAD bridge via one-shot child process (`freecad_bridge/`).
  - Direct parametric modeling tools: create box/cylinder, set_parameter,
    recompute, list/inspect, validate, save, export STEP, `boolean_cut`, tank,
    tubular coil.
  - Optional Taiga-S1 integration: isolated one-shot subprocess runtime with
    `taiga_status` and `taiga_score_actions`.
- Exposed MCP tools: `server_info`, `freecad_status`, `create_document`,
  `create_box`, `create_cylinder`, `set_parameter`, `recompute`, `list_objects`,
  `inspect_object`, `validate_document`, `save_document`, `export_step`,
  `boolean_cut`, `tank`, `tubular_coil`, `taiga_status`, `taiga_score_actions`.

## Outstanding / known limitations

- Milestone 7 (Method A vs Method B comparison) is not implemented; only a
  guarded `run_iterative_modeling` skeleton exists (loop mechanics with injected
  fakes only).
- Milestone 8 (documentation/release preparation) not started.
- FreeCAD required from Milestone 2 on; developed against FreeCAD 1.1.x on a
  Windows host; system Python cannot import FreeCAD; bridge uses the FreeCAD
  console interpreter.
- Docs reference `src/freecad_mcp/validation/`, which does not exist yet.
- No end-to-end FreeCAD or real Taiga-S1 / Torch / weights verification yet.

## Task-specific notes

- Shared agent skills and persistent project memory were added under
  `.agents/skills/` and `PROJECT_MEMORY/`; root `AGENTS.md` routes agents to them.
- `gen_traceback.py` is an unrelated untracked file; do not modify or stage it.
