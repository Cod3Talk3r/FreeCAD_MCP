# FreeCAD MCP

**Control FreeCAD parametric 3D modeling through AI agents and the Model Context Protocol (MCP).**

FreeCAD MCP is an open-source project that connects AI coding agents to [FreeCAD](https://www.freecad.org/) through the [Model Context Protocol](https://modelcontextprotocol.io/).

It provides a controlled interface for creating, inspecting, validating, and modifying parametric 3D models using FreeCAD's native modeling capabilities.

The project is designed for engineering-oriented CAD workflows, with an emphasis on reliable operations, persistent documents, reusable modeling features, and integration with external AI agents.

Instead of executing arbitrary AI-generated Python code inside FreeCAD, the project uses an explicit operation interface and a dedicated FreeCAD process to execute supported CAD operations.

## Features

### Parametric modeling

The current modeling capabilities include:

- **Part primitives:** Boxes, cylinders, tanks, and tubular coils.
- **Placement editing:** Position objects along the X, Y, and Z axes.
- **Boolean operations:** Cut, Fuse, and Common.
- **Sketcher geometry:** Lines, rectangles, circles, triangles, ellipses, regular polygons, and custom polygons.
- **Sketch editing:** Inspect, add, modify, and delete supported sketch geometry.
- **PartDesign features:**
  - Pad and Pocket
  - Revolution and Groove
  - Fillet and Chamfer
  - Linear and Polar Patterns
  - Mirrored
  - Thickness

These operations use native FreeCAD objects and features rather than approximating the modeling process with unrelated geometry.

### Document and object management

The project provides operations for:

- Creating and managing FreeCAD documents.
- Listing and inspecting document objects.
- Editing supported object parameters and placements.
- Recomputing and validating models.
- Saving and exporting models.
- Deleting objects through the controlled operation interface.

Document persistence, validation, and safe execution are central parts of the architecture.

### Native FreeCAD GUI Workbench

FreeCAD MCP also provides a native FreeCAD GUI Workbench with a dockable panel.

The GUI provides access to supported modeling and document operations without requiring an external AI agent.

Both the MCP interface and GUI Workbench use the same shared operation layer, avoiding separate implementations of CAD logic.

See the development documentation for setup and usage details.

## Architecture

The project supports two independent execution strategies.

### Method A — Direct MCP (primary)

An external AI agent communicates directly with the FreeCAD MCP server.

The server exposes typed tools and routes supported operations through the shared application and FreeCAD bridge layers.

This is the primary execution strategy and must remain fully usable without Taiga-S1.

### Method B — Taiga-assisted CAD automation (optional)

[Taiga-S1](https://github.com/shhivv/biome-s1) is integrated as an optional component behind an isolated integration boundary.

The existing `taiga_build` workflow supports an Expert-driven build process in a separate child runtime.

A general interactive controller that continuously observes CAD state, selects actions, executes them, and evaluates the resulting state remains future work.

The two strategies are intended to be independently usable and will be evaluated separately as the Taiga integration develops.

### Execution and safety

The architecture follows several principles:

- The MCP parent process does not import FreeCAD.
- CAD operations execute in a dedicated FreeCAD child process.
- Supported operations are explicitly registered and allowlisted.
- Arbitrary generated Python execution inside FreeCAD is not exposed.
- MCP and GUI interfaces reuse shared operation logic.
- Taiga and its machine-learning dependencies remain optional and isolated.

For more information, see [Architecture](docs/architecture.md).

## Project Status

Development is incremental and organized into milestones.

Historical milestones M0–M11 are retained for project history. M12 is the latest completed milestone.

The project roadmap contains the detailed implementation status, verified FreeCAD capabilities, outstanding gaps, architectural direction, dependencies, and acceptance criteria.

See the **[Project Roadmap](docs/ROADMAP.md)** for the canonical and up-to-date project plan.

## Requirements

- Python 3.10 or later.
- [uv](https://docs.astral.sh/uv/) for Python dependency and environment management.
- Git.
- [FreeCAD](https://www.freecad.org/) 1.1.x for CAD operations and GUI integration.

FreeCAD is not required for FreeCAD-independent MCP server tests, schema validation, or other tests that do not execute native CAD operations.

The project is developed and verified primarily against FreeCAD 1.1.4. Other versions may require additional compatibility testing.

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Cod3Talk3r/FreeCAD_MCP
cd FreeCAD_MCP
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure FreeCAD

Make sure FreeCAD is installed and its executable can be discovered by the project.

If automatic discovery is not suitable for your environment, configure the FreeCAD executable using the project's supported environment settings.

See [Development Guide](docs/development.md) for platform-specific setup and configuration.

### 4. Start the MCP server

```bash
uv run freecad-mcp
```

The server communicates over **stdio**, so it should normally be launched by an MCP-compatible client rather than used as a standalone interactive terminal application.

Configure your preferred MCP client to launch the command from the project environment.

The exact client configuration depends on the agent and operating system.

For additional setup instructions, see:

- [Development Guide](docs/development.md)
- [Architecture](docs/architecture.md)

## FreeCAD GUI Workbench

The project includes a native FreeCAD Workbench and dockable panel under `Mod/freecad_mcp_gui/`.

The GUI provides direct access to supported operations, including modeling, inspection, editing, recomputation, validation, and object deletion.

It uses the same shared operation dispatcher as the MCP server.

See the development documentation for installation and usage instructions.

## Optional Taiga-S1 Integration

Taiga-S1 is optional and disabled by default.

Taiga, PyTorch, and model weights are not required dependencies of the main project.

To enable the optional integration, configure a separate environment containing Taiga-S1 and set the following environment variables:

| Variable | Description |
|---|---|
| `FREECAD_MCP_TAIGA_PYTHON` | Explicit Python interpreter containing Taiga-S1 |
| `FREECAD_MCP_TAIGA_MODEL` | Local model directory containing `config.json` and `model.safetensors` |
| `FREECAD_MCP_TAIGA_TIMEOUT_SECONDS` | Runtime timeout in seconds; defaults to `120` |
| `FREECAD_MCP_TAIGA_DEVICE` | Inference device; defaults to `cpu` |

The integration does not automatically download models or fall back to another Python interpreter.

The current tools include:

- `taiga_status` — checks whether the optional runtime is available without loading model weights.
- `taiga_score_actions` — scores supplied action identifiers without executing them.

The existing `taiga_build` workflow provides an isolated Expert-driven build path.

Method A remains unaffected when Taiga-S1 is not installed or configured.

See [Taiga-S1 Integration](docs/taiga-s1.md) for details.

## Repository Layout

```text
FreeCAD_MCP/
├── src/
│   └── freecad_mcp/
│       ├── server.py
│       ├── config.py
│       ├── tools/
│       ├── freecad_bridge/
│       ├── app/
│       └── integrations/
│           └── taiga/
├── Mod/
│   └── freecad_mcp_gui/
├── tests/
├── docs/
│   ├── ROADMAP.md
│   ├── architecture.md
│   ├── development.md
│   └── taiga-s1.md
├── PROJECT_MEMORY/
├── pyproject.toml
├── uv.lock
├── AGENTS.md
└── README.md
```

The main directories are:

- `src/freecad_mcp/` — MCP server, tools, shared application logic, FreeCAD bridge, and optional integrations.
- `Mod/freecad_mcp_gui/` — native FreeCAD GUI Workbench and dockable panel.
- `tests/` — unit, schema, mocked bridge, and FreeCAD-dependent integration tests.
- `docs/` — user-facing and technical documentation, including the canonical roadmap.
- `PROJECT_MEMORY/` — project knowledge, architectural decisions, testing evidence, and development history.

## Development

For development setup, environment configuration, testing, and contribution workflow, see:

- [Development Guide](docs/development.md)
- [Architecture](docs/architecture.md)
- [Project Roadmap](docs/ROADMAP.md)
- [Taiga-S1 Integration](docs/taiga-s1.md)

The project uses focused tests during incremental development. Native FreeCAD integration tests should be run against the actual supported FreeCAD installation when validating CAD behavior.

## AI-Assisted Development

AI-assisted tools such as OpenCode, Kilo, and ChatGPT have been used for code exploration, implementation, debugging, testing, and architectural discussions.

AI-generated suggestions and code are reviewed and tested as part of the development process. The project maintainer remains responsible for the code, technical decisions, and releases.

## License

Copyright (C) 2026 Mohammad Reza Asadi.

This project is licensed under the **GNU General Public License v3.0 only (GPL-3.0-only)**.

You are free to use, study, modify, and redistribute this software under the terms of the GPL-3.0 license, subject to its conditions.

See the [LICENSE](LICENSE) file for the complete license text.

This is an independent open-source project and is not affiliated with, endorsed by, or sponsored by the FreeCAD project.
