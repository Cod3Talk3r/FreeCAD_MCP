# Changelog

## Current task

- Added shared agent skills (`.agents/skills/token-saver`,
  `.agents/skills/project-memory`) and persistent project memory
  (`PROJECT_MEMORY/`), plus root `AGENTS.md` routing agents to them.

## Milestones

- M0–M1: project init, MCP server skeleton, diagnostics — done.
- M2: FreeCAD bridge (connection/version diagnostics) — done.
- M3: direct modeling tools (incl. `boolean_cut`) — done.
- M4: tank and tubular coil geometry — done.
- M5: Taiga-S1 investigation and adapter contract — done.
- M6: isolated Taiga-S1 runtime (`taiga_status`, `taiga_score_actions`) — done
  (`396c45a`, HEAD).
- M7: Method A vs Method B comparison — planned; only a guarded loop skeleton
  exists.
- M8: documentation and release preparation — planned.

## Recent commits

- `396c45a` feat: add Taiga-S1 integration foundation (HEAD).
- `ae62d24` Merge branch `feat/freecad-mcp`.
- `3f98a94` feat: add boolean cut modeling tool.

Record meaningful milestones here, not every minor edit.
