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
| 1 | Minimal MCP server with a diagnostic tool | Done |
| 2 | FreeCAD bridge (connection + version diagnostics) | Done |
| 3 | Direct modeling tools (box, cylinder, validate, export STEP) | Done |
| 4 | Configurable tank and tubular coil | Done |
| 5 | Taiga-S1 investigation and optional adapter contract | Done |
| 6 | Comparison framework (Method A vs Method B) | Planned |
| 7 | Documentation and release preparation | Planned |

The MCP server, its diagnostic tool, and the local FreeCAD bridge (connection and
version diagnostics) are implemented and tested. Milestone 3 adds direct
modeling tools (create_box, create_cylinder, set_parameter, recompute,
list_objects, inspect_object, validate_document, save_document, export_step),
and Milestone 4 adds tank and tubular coil geometry creation using Part::Box,
Part::Cylinder, Part::Helix and Part::Pipe primitives through the persistent
parametric modeling system.

Milestone 5 records the verified Taiga-S1 investigation and adds an optional,
import-safe adapter contract. The adapter can report availability and score
candidate commands against a supplied state, but it does not generate plans or
execute model-produced commands; Taiga-driven geometry remains deferred. See
[`docs/taiga-s1.md`](docs/taiga-s1.md).

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
uv run freecad-mcp # run the MCP server over stdio
```

See [`docs/development.md`](docs/development.md) for environment setup, FreeCAD
discovery, and the development workflow, and
[`docs/architecture.md`](docs/architecture.md) for the module layout and design
principles.

## Repository layout

```
src/freecad_mcp/        # the Python package
    __init__.py         # version
    server.py           # MCP server construction + stdio entry point
    config.py           # environment-based settings
    tools/              # MCP tool definitions (diagnostics + freecad_status)
    freecad_bridge/     # local child-process bridge to FreeCAD
        runner.py       # runs *inside* FreeCAD; fixed, explicit operations
    integrations/       # optional backend contract + Taiga-S1 adapter
docs/                   # architecture and development notes
tests/                  # FreeCAD-independent tests + FreeCAD-gated integration tests
```

The optional `integrations/` package exists as of Milestone 5; `validation/`
will be added when the milestone that needs it lands.

## License

No license has been selected yet. This must be decided before any public release.
