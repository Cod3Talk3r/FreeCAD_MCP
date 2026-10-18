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
- [ ] M1 — Minimal MCP server (`server.py`, `config.py`, one diagnostic tool, tests)
- [ ] M2 — FreeCAD bridge (connection + version diagnostics)
- [ ] M3 — Direct modeling tools (box, cylinder, inspect, modify, validate, STEP export)
- [ ] M4 — Tank and tubular coil
- [ ] M5 — Taiga-S1 investigation and adapter
- [ ] M6 — Comparison framework
- [ ] M7 — Documentation and release preparation

## Planned next step (M1)

Implement the smallest MCP server using the official `mcp` Python SDK: verify the
installed SDK version and its real API, add `config.py`, register one harmless
diagnostic tool with a typed schema, add tests for the FreeCAD-independent logic,
and document how to launch the server and wire it into OpenCode's MCP
configuration (using the current, verified config format).
