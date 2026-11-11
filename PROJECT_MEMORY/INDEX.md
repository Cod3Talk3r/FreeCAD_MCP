# Project Memory Index

Shared, concise project memory for agents working in this repository. Source
code and tests are authoritative; memory only records verified facts.

## Memory files

| File | Read when | Purpose |
| ---- | ---------- | ------- |
| `PROJECT.md` | Orientation is unclear, or before a broad task | Project purpose and scope |
| `CURRENT_STATE.md` | Starting, continuing, or handing off any task | Latest verified state, outstanding work, known limitations |
| `ARCHITECTURE.md` | Task touches modules, bridge, tools, or integrations | Layer and module design, key invariants |
| `DECISIONS.md` | Task involves a design or implementation choice | Why past decisions were made |
| `TESTING.md` | Before or after running / changing tests | Commands, verified results, testing limitations |
| `CHANGELOG.md` | Need historical context or milestone status | Meaningful milestones and commits |
| `docs/ROADMAP.md` | Planning CAD capability work | Canonical forward roadmap, verified FreeCAD 1.1.4 capability evidence, architecture and acceptance criteria |

## Rules

- Read `CURRENT_STATE.md` second, right after this index, before substantial tasks.
- Read only the memory files relevant to the task; do not read all of them.
- Keep information concise and factual; avoid duplicating across files.
- Update memory after meaningful changes, completed milestones, important
  decisions, or changes in test status.
- Do not create new memory files unless clearly useful; extend existing ones.

## Handoff

When handing work between agents (OpenCode ↔ Kilo), follow the handoff
procedure in `.agents/skills/project-memory/SKILL.md` and keep
`CURRENT_STATE.md` current.
