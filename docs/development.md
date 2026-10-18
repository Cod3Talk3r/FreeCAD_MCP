# Development

Practical notes for working on `freecad-mcp`.

## Prerequisites

- **uv** — dependency and project manager. Install: <https://docs.astral.sh/uv/getting-started/installation/>
- **Git**
- **FreeCAD** — needed from Milestone 2 onward (developed against FreeCAD 1.1.x).

The Python interpreter for this project is managed by uv and pinned in
`.python-version`. `pyproject.toml` declares `requires-python = ">=3.10"` so the
package stays broadly usable, while local development uses the pinned version.

## Dependency management

Use `uv` only — do not use pip, Poetry, Pipenv, or Conda for this project.

```bash
uv sync                 # create/refresh .venv from uv.lock
uv add <pkg>            # add a runtime dependency
uv add --dev <pkg>      # add a development-only dependency
uv lock                 # refresh uv.lock without installing
uv run <cmd>            # run a command inside the project environment
```

`pyproject.toml` and `uv.lock` are committed. Optional integrations (for example
Taiga) must live in an optional dependency group or a separate package, never in
the default install.

## Tests

```bash
uv run pytest
```

Tests in this repository must run **without FreeCAD** unless explicitly marked.
Tests that need a real FreeCAD instance are separated and clearly labelled, so a
missing FreeCAD install never looks like a code failure.

## FreeCAD discovery (Windows)

FreeCAD ships its own interpreter; the system Python cannot import its modules.
Recorded environment (subject to change per machine):

- Install root: `E:\Softwares\FreeCAD\`
- GUI: `E:\Softwares\FreeCAD\bin\freecad.exe`
- Console (headless): `E:\Softwares\FreeCAD\bin\freecadcmd.exe`
- Embedded Python: `E:\Softwares\FreeCAD\bin\python.exe` (CPython 3.11)

Verify quickly:

```powershell
& "E:\Softwares\FreeCAD\bin\freecadcmd.exe" --version
```

The bridge (Milestone 2) must locate FreeCAD via configuration and/or discovery,
and must never hard-code this machine's absolute path.

## Git workflow

- Work happens on a feature branch (`feat/freecad-mcp`).
- Commit message style: conventional commits, e.g. `chore: initialize project with uv`.
- Before each commit: run the tests, then inspect `git status` and `git diff`.
- One commit per completed milestone.
- Never commit secrets or machine-specific config; never rewrite history or push
  without explicit approval.

## Milestone checklist

Development proceeds milestone by milestone; each milestone is committed only
after its acceptance criteria are met, and the next one starts only after
explicit approval.

- [x] M0 — Environment and project initialization
- [x] M1 — Minimal MCP server (`server.py`, `config.py`, one diagnostic tool, tests)
- [x] M2 — FreeCAD bridge (connection + version diagnostics)
- [ ] M3 — Direct modeling tools (box, cylinder, inspect, modify, validate, STEP export)
- [ ] M4 — Tank and tubular coil
- [ ] M5 — Taiga-S1 investigation and adapter
- [ ] M6 — Comparison framework
- [ ] M7 — Documentation and release preparation

## Running and inspecting the server

The server speaks MCP over **stdio** and is launched as a normal subprocess:

```bash
uv run freecad-mcp          # console script defined in pyproject.toml
uv run python -m freecad_mcp  # equivalent module form
```

Either command blocks, reading MCP messages on stdin and writing responses on
stdout. Anything the server logs goes to stderr, so it never corrupts the
protocol stream.

### Inspecting it

The test suite talks to the real server through the official SDK client
(`tests/test_server.py` in-process, `tests/test_stdio_launch.py` over stdio):

```bash
uv run pytest -q
```

To poke at it by hand, the SDK ships an in-process client:

```python
import asyncio
from mcp import Client, StdioServerParameters

async def main() -> None:
    params = StdioServerParameters(command="uv", args=["run", "freecad-mcp"])
    async with Client(params) as client:  # default mode="auto"
        print([t.name for t in (await client.list_tools()).tools])
        print((await client.call_tool("server_info", {"include_environment": True})).structured_content)

asyncio.run(main())
```

The official **MCP Inspector** is also available, but it is an optional extra and
needs Node.js/`npx`:

```bash
uv run --with "mcp[cli]" mcp dev src/freecad_mcp/server.py:create_server
```

(That extra and Node.js are *not* project dependencies; the checks above need
neither.)

## Configuration

Settings come from environment variables, all prefixed `FREECAD_MCP_`, each with
a safe default. Nothing machine-specific is committed.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FREECAD_MCP_SERVER_NAME` | `freecad-mcp` | Name advertised to MCP clients. |
| `FREECAD_MCP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`. |
| `FREECAD_MCP_FREECAD_EXECUTABLE` | unset (auto-discover) | Override the FreeCAD console executable; an explicit value is authoritative. |
| `FREECAD_MCP_TIMEOUT_SECONDS` | `60` | Per-operation timeout for the FreeCAD bridge, in seconds. |

Invalid values fail fast at startup (for example, an unknown log level).

## FreeCAD bridge

The bridge needs FreeCAD's **console** executable (`freecadcmd`/`FreeCADCmd`), not
the GUI. Discovery checks, in order: an explicit setting →
`FREECAD_MCP_FREECAD_EXECUTABLE` → `PATH` → conventional install locations →
(Windows) Uninstall registry entries.

Verify FreeCAD directly:

```powershell
& "E:\Softwares\FreeCAD\bin\freecadcmd.exe" --version
```

Ask the server about it: the `freecad_status` tool reports `available`, `version`,
embedded `python_version`, and `part_available`. The test suite is the easiest way
to exercise it (it calls the tool through the MCP client):

```bash
uv run pytest -q
```

The suite contains FreeCAD-dependent tests
(`tests/test_freecad_integration.py`). They **skip** when FreeCAD cannot be
discovered, so a machine without FreeCAD never sees a false failure. To run only
those:

```bash
uv run pytest tests/test_freecad_integration.py -v
```

The bridge launches `freecadcmd` with `freecad_bridge/runner.py` and exchanges a
JSON envelope per operation. It is local-only (no sockets), performs only the
fixed operations listed in `runner.OPERATIONS`, and kills the child on timeout.

## Using it from OpenCode

OpenCode V2 configures local MCP servers under `mcp.servers.<name>` with
`type: "local"` (verified against the V2 docs — see
<https://opencode.ai/v2/docs/mcp-servers>). Because the server is defined by this
repository, point OpenCode at it; `uv` starts the process and resolves the
environment for you:

```jsonc title="opencode.jsonc"
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "freecad": {
        "type": "local",
        "command": ["uv", "run", "--directory", "/absolute/path/to/freecad-mcp", "freecad-mcp"]
      }
    }
  }
}
```

If OpenCode runs from this repository (the workspace), the working directory
already resolves and the simpler form works:

```jsonc
{
  "mcp": {
    "servers": {
      "freecad": { "type": "local", "command": ["uv", "run", "freecad-mcp"] }
    }
  }
}
```

Or add it with the CLI (default is project-local; add `--global` for every project):

```bash
opencode mcp add freecad -- uv run freecad-mcp
opencode mcp list
```

Notes:

- OpenCode's default `protocol` is `legacy` (classic `initialize` handshake).
  This server answers `legacy` and also supports the modern `2026-07-28`
  revision, so no `protocol` override is required.
- Pass environment variables with the `environment` field or repeated `--env`
  flags, e.g. `FREECAD_MCP_LOG_LEVEL=DEBUG`. Do not embed secrets or absolute
  machine paths in tracked configuration.

## Planned next step (M3)

Implement the first explicit modeling tools on top of the bridge: create a
document, add a box and a cylinder with validated dimensions, inspect objects
(name/type/dimensions), modify a parameter, recompute, validate geometry, and
export a selection to STEP. Each operation is added to `runner.OPERATIONS` and
exposed as a typed MCP tool; a test creates real geometry and checks the STEP
export. Bounded, validated inputs only — never arbitrary Python in FreeCAD.
