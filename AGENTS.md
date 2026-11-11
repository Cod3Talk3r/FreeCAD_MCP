# Project Agent Instructions

## Project

`freecad-mcp` — an MCP server that lets AI agents create, inspect, validate, and modify parametric 3D models in a real FreeCAD instance over MCP (stdio), with an optional Taiga-S1-assisted path.

## Shared Skills

- Use the `token-saver` skill for context-efficient work (exploration, implementation, testing, reporting).
- Use the `project-memory` skill when starting, continuing, or handing off project work.

## Before Starting Work

1. Read `PROJECT_MEMORY/INDEX.md`.
2. Read `PROJECT_MEMORY/CURRENT_STATE.md`.
3. Load relevant memory files only when needed.
4. Inspect Git status before modifying files.
5. Verify important assumptions against source code.

## During Work

- Minimize unnecessary file reads and tool output.
- Avoid unrelated changes.
- Preserve existing user modifications.
- Follow the project's architecture and established decisions.
- Verify changes with appropriate tests.

## Source of Truth

- Source code and tests override potentially outdated project memory.
- Use `.kilo/ROADMAP.md` as the canonical forward product roadmap; update it only
  for verified research or explicitly approved planning changes.

## After Completing Work

1. Verify the implementation.
2. Update relevant project memory files when meaningful information changes.
3. Record important decisions and limitations.
4. Report test results accurately.
5. Provide a concise handoff summary.

## Completion

- Never claim a task is complete without verification.

## Shared Memory Rules

- PROJECT_MEMORY is shared across all agents.
- Never blindly overwrite another agent's changes.
- Never store secrets or credentials.
- Source code and verified Git state take precedence over memory.
- Do not commit or push unless explicitly requested.

## Git Safety

- Do not stage, commit, or push unless explicitly requested.
- Preserve existing user changes.
- Avoid unrelated changes.
