# Project

`freecad-mcp` — a Model Context Protocol (MCP) server that lets AI coding
agents create, inspect, validate, and modify parametric 3D models in a real
FreeCAD instance over MCP (stdio).

## Purpose and scope

- **Method A (primary):** the agent drives FreeCAD directly through explicit,
  validated MCP modeling tools over a local one-shot bridge. This path must keep
  working without the optional integration.
- **Method B (optional):** an agent-assisted path backed by the optional
  Taiga-S1 integration (status + next-action scoring; no execution yet).

## Out of scope / deferred by design

- No arbitrary Python execution inside FreeCAD.
- No autonomous planning/execution loops yet (planned Milestone 7).
- No live state capture, action enumeration/execution, or PartDesign layer
  without a verified execution boundary.

## Key references

- `README.md`, `docs/architecture.md`, `docs/development.md`, `docs/taiga-s1.md`.
- Code: `src/freecad_mcp/{server,config}.py`, `src/freecad_mcp/tools/`,
  `src/freecad_mcp/freecad_bridge/`, `src/freecad_mcp/integrations/`.
