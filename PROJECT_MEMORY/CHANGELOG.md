# Changelog

## Current task

- Sketch line editing (`sketch_edit_line`) fixed against the verified FreeCAD
  1.1.4 API: no `setGeometry`/`setLine`; use mutate-and-reassign of the
  `Geometry` property. Real FreeCAD integration tests pass. Changes are in the
  working tree, not committed.
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
  (`396c45a`).
- M7: Taiga build execution (`taiga_build`) — done
  (`1949a5b` feat: add real Taiga FreeCAD build execution).
- M8: documentation and release preparation — planned.

## Recent commits

- `1949a5b` feat: add real Taiga FreeCAD build execution (HEAD).
- `df225fd` feat: add Taiga goal validation and build runtime.
- `b2b090a` feat: add cross-platform pathing and bridge transport.
- `a078b6e` chore: ignore local Kilo configuration.
- `54461ac` docs: add Taiga planning skill and update project memory.
- `396c45a` feat: add Taiga-S1 integration foundation.
- `ae62d24` Merge branch `feat/freecad-mcp`.
- `3f98a94` feat: add boolean cut modeling tool.

Record meaningful milestones here, not every minor edit.

---
