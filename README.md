# freecad-mcp

Drive [FreeCAD](https://www.freecad.org/) parametric 3D modeling from an AI coding
agent (for example [OpenCode](https://opencode.ai/)) through the
[Model Context Protocol](https://modelcontextprotocol.io/) (MCP).

The agent talks to a local MCP server, which talks to a real FreeCAD instance
through a small, explicit bridge. The goal is to let an agent **create, inspect,
validate, and modify** parametric models — not to run arbitrary generated Python
inside FreeCAD.

## Two execution strategies

This project supports two independent approaches, with direct MCP remaining
usable without the optional Taiga integration:

- **Method A — Direct MCP (primary).** The agent uses this project's MCP tools to
  drive FreeCAD directly. This path must work on its own and never depends on the
  optional integration.
- **Method B — Taiga-assisted CAD automation (optional).** Taiga-S1 runs behind
  an isolated integration boundary. The existing `taiga_build` executes an
  Expert-driven build in a child process; general live state/action iteration
  and fair Method A/B comparison remain future work.

The native FreeCAD GUI Workbench and dockable panel are also a permanent product
interface. They reuse the shared operation dispatcher rather than maintaining a
separate CAD implementation.

## Status

Incremental development. Historical implementation milestones and current status:

| Milestone | Description | Status |
| --------- | ----------- | ------ |
| 0 | Environment inspection and project initialization | Done |
| 1 | Minimal MCP server with a diagnostic tool | Done |
| 2 | FreeCAD bridge (connection + version diagnostics) | Done |
| 3 | Direct modeling tools (box, cylinder, validate, export STEP) | Done |
| 4 | Configurable tank and tubular coil | Done |
| 5 | Taiga-S1 investigation and optional adapter contract | Done |
| 6 | Taiga-S1 isolated runtime integration | Done |
| 7 | Guarded Taiga workflow skeleton and build path | Implemented with limits |
| 8 | Product architecture and roadmap documentation | In progress |

The current operation surface also includes Boolean Cut/Fuse/Common, document
and object inspection, placement editing, Sketcher geometry creation/editing,
PartDesign Pad/Pocket/Groove/Revolution/Fillet/Chamfer/pattern/mirror/thickness,
and the native FreeCAD GUI panel. See the canonical
[`product roadmap`](docs/ROADMAP.md) for verified capability evidence, current
gaps, target architecture, milestone dependencies, and acceptance criteria.

The Taiga integration reports availability, scores candidate actions, and
provides an isolated Expert-driven `taiga_build` workflow. It does not yet
provide a general live interactive controller that captures state, enumerates
actions, executes each selected action and feeds back fresh state.

Taiga and PyTorch are imported only in explicitly configured child runtimes;
model paths are local and no model is downloaded implicitly. When Taiga is
absent, Method A remains unaffected. See [`docs/taiga-s1.md`](docs/taiga-s1.md).

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
principles. The detailed future plan is maintained only in
[`docs/ROADMAP.md`](docs/ROADMAP.md).

## Optional Taiga-S1 runtime

The Taiga tools are optional and off by default. Taiga, PyTorch, and model
weights are not project dependencies. To enable scoring in a separate
environment, configure:

- `FREECAD_MCP_TAIGA_PYTHON` — explicit interpreter containing Taiga-S1;
- `FREECAD_MCP_TAIGA_MODEL` — local directory with `config.json` and
  `model.safetensors`;
- `FREECAD_MCP_TAIGA_TIMEOUT_SECONDS` — default `120`;
- `FREECAD_MCP_TAIGA_DEVICE` — default `cpu`.

The server never falls back to another interpreter and never downloads a model.
`taiga_status` checks availability without loading weights; `taiga_score_actions`
scores supplied action ids without executing them. Both return structured
unavailable results when Taiga is not configured. See
[`docs/taiga-s1.md`](docs/taiga-s1.md).

## Repository layout

```bash
src/freecad_mcp/        # the Python package
    __init__.py         # version
    server.py           # MCP server construction + stdio entry point
    config.py           # environment-based settings
    tools/              # MCP tool definitions (diagnostics, FreeCAD, modeling, Taiga)
    freecad_bridge/     # local child-process bridge to FreeCAD
        runner.py       # runs *inside* FreeCAD; fixed, explicit operations
    integrations/       # optional backend contract + Taiga-S1 adapter/runtime
        taiga/runner.py # standalone Taiga child runner; lazily imports Taiga/Torch
docs/                   # architecture and development notes
tests/                  # FreeCAD-independent tests + FreeCAD-gated integration tests
```

The optional `integrations/` package exists as of Milestone 5; `validation/`
will be added when the milestone that needs it lands.

## AI-Assisted Development

OpenCode and ChatGPT were used as AI-assisted development tools throughout this project.

OpenCode assisted with code exploration, implementation, debugging, and testing, while ChatGPT was used for technical discussions, architectural decisions, troubleshooting, documentation, and development guidance.

AI-generated suggestions and code were reviewed and tested as part of the development workflow. The project maintainer remains responsible for the project's code, technical decisions, and releases.

## License

Copyright (C) 2026 Mohammad Reza Asadi

This project is licensed under the **GNU General Public License v3.0 only (GPL-3.0-only)**.

You are free to use, study, modify, and redistribute this software under the terms of the license. If you distribute this software or modified versions of it, you must comply with the applicable requirements of the GPL-3.0 license.

See the [LICENSE](LICENSE) file for the full license text.

This project is an independent open-source project and is not affiliated with, endorsed by, or sponsored by the FreeCAD project.
