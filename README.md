# freecad-mcp

Drive [FreeCAD](https://www.freecad.org/) parametric 3D modeling from an AI coding
agent (for example [OpenCode](https://opencode.ai/)) through the
[Model Context Protocol](https://modelcontextprotocol.io/) (MCP).

The agent talks to a local MCP server, which talks to a real FreeCAD instance
through a small, explicit bridge. The goal is to let an agent **create, inspect,
validate, and modify** parametric models — not to run arbitrary generated Python
inside FreeCAD.

## Two execution strategies

This project is structured so that two independent approaches can be built and
fairly compared:

- **Method A — Direct MCP (primary).** The agent uses this project's MCP tools to
  drive FreeCAD directly. This path must work on its own and never depends on the
  optional integration.
- **Method B — Taiga-assisted MCP (optional).** An isolated adapter for
  *Taiga-S1*, wired in behind a clearly defined interface. Taiga's real
  capabilities and interfaces must be verified before any adapter code exists,
  and the project must keep working when Taiga is absent, disabled, or
  incompatible.

## Status

Incremental development. Current progress:

| Milestone | Description | Status |
| --------- | ----------- | ------ |
| 0 | Environment inspection and project initialization | Done |
| 1 | Minimal MCP server with a diagnostic tool | Planned |
| 2 | FreeCAD bridge (connection + version diagnostics) | Planned |
| 3 | Direct modeling tools (box, cylinder, validate, export STEP) | Planned |
| 4 | Configurable tank and tubular coil | Planned |
| 5 | Taiga-S1 investigation and optional adapter | Planned |
| 6 | Comparison framework (Method A vs Method B) | Planned |
| 7 | Documentation and release preparation | Planned |

The MCP server and tooling are **not implemented yet**; Milestone 0 only
establishes a reproducible project skeleton.

## Requirements

- Python 3.10+ (development uses a uv-managed CPython 3.14)
- [uv](https://docs.astral.sh/uv/) for dependency management
- Git
- FreeCAD (developed against FreeCAD 1.1.x) — required only for the bridge and
  modeling milestones, not for the MCP server skeleton

## Quick start (development)

```bash
# Install uv if missing: https://docs.astral.sh/uv/getting-started/installation/
uv sync            # create the environment and install (locked) dependencies
uv run pytest      # run the test suite
```

See [`docs/development.md`](docs/development.md) for environment setup, FreeCAD
discovery, and the development workflow, and
[`docs/architecture.md`](docs/architecture.md) for the module layout and design
principles.

## Repository layout

```
src/freecad_mcp/        # the Python package
    __init__.py
docs/                   # architecture and development notes
tests/                  # tests that do not require FreeCAD
```

Directories for `server`, `tools`, `freecad_bridge`, `validation`, and
`integrations` are added only as the milestones that need them land.

## License

No license has been selected yet. This must be decided before any public release.
