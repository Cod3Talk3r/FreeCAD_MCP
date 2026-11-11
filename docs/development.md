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

Mocked bridge tests prove our contracts and delegation only; they do not
establish native FreeCAD API behavior. See
[`PROJECT_MEMORY/TESTING.md`](../PROJECT_MEMORY/TESTING.md) and the canonical
[product roadmap](../.kilo/ROADMAP.md) for evidence tiers and milestone criteria.

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

Capability research must use the actual installed FreeCAD version. Keep probes
read-only with respect to user/project documents; use disposable documents and
temporary paths. Record the exact version, module/type/property availability,
recompute outcome, shape validity, save/reopen and parametric-edit outcome as
separate evidence. A module import or object constructor is not proof that a
feature works. Do not install/download workbenches or models as part of a probe.

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
after its acceptance criteria are met. Historical M0-M11 labels are preserved;
the forward roadmap continues at M12. Consult the canonical roadmap before
proposing or implementing CAD work.

- [x] M0 — Environment and project initialization
- [x] M1 — Minimal MCP server (`server.py`, `config.py`, one diagnostic tool, tests)
- [x] M2 — FreeCAD bridge (connection + version diagnostics)
- [x] M3 — Direct modeling tools (box, cylinder, inspect, modify, validate, STEP export)
- [x] M4 — Tank and tubular coil
- [x] M5 — Taiga-S1 investigation and optional adapter contract
- [x] M6 — Taiga-S1 isolated runtime integration
- [x] M7 — Taiga build execution (isolated Expert-driven build); general Method A
  vs Method B comparison remains future work
- [ ] M8 — Product roadmap/documentation milestone; release preparation remains
  separate

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
| `FREECAD_MCP_TAIGA_PYTHON` | unset | Explicit Python interpreter containing Taiga-S1 and its dependencies; no fallback. |
| `FREECAD_MCP_TAIGA_TIMEOUT_SECONDS` | `120` | Per-operation timeout for the isolated Taiga runtime, in seconds. |
| `FREECAD_MCP_TAIGA_MODEL` | unset | Local Taiga model directory; required for scoring. Never downloaded implicitly. |
| `FREECAD_MCP_TAIGA_DEVICE` | `cpu` | Device passed to Taiga's model loader. |

Invalid values fail fast at startup (for example, an unknown log level or a
non-positive timeout).

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

The native GUI Workbench is in `Mod/freecad_mcp_gui/`. Verify GUI-only
selection/view behavior in FreeCAD GUI; do not assume the console can import
`FreeCADGui` or GUI modules. GUI model mutations should dispatch through the
shared operation handler rather than implementing CAD algorithms in widgets.

## Optional Taiga-S1 runtime (M6)

The verified investigation and its limitations are recorded in
[`taiga-s1.md`](taiga-s1.md). Taiga-S1 is real and MIT-licensed, but it needs
Torch, a live FreeCAD state, and a command execution loop. This project ships an
import-safe adapter and an isolated one-shot subprocess runtime for status and
next-action scoring only.

Taiga and Torch are **not** project dependencies, and `uv sync` does not install
them or any model weights. Create a separate environment for them and point the
server at it explicitly:

```bash
# Example only; do not add these to the project's dependency files.
# python -m venv C:\taiga-env
# C:\taiga-env\Scripts\python -m pip install taiga-s1

$env:FREECAD_MCP_TAIGA_PYTHON = "C:\taiga-env\Scripts\python.exe"
$env:FREECAD_MCP_TAIGA_MODEL = "C:\models\taiga-s1"
$env:FREECAD_MCP_TAIGA_DEVICE = "cpu"
$env:FREECAD_MCP_TAIGA_TIMEOUT_SECONDS = "120"
uv run freecad-mcp
```

The project never falls back to its own interpreter or FreeCAD's Python. A blank
or missing `FREECAD_MCP_TAIGA_PYTHON` makes `taiga_status` report
`not_installed` while the server keeps running.

The adapter API is unchanged:

```python
from freecad_mcp.integrations.taiga import TaigaBackend

backend = TaigaBackend(
    python="C:/taiga-env/Scripts/python.exe",
    model="C:/models/taiga-s1",
)
report = backend.status_report()   # never loads weights
status = backend.status()
plan = backend.plan("a plate")     # RUNTIME_CONTEXT_REQUIRED; never invents steps
result = backend.score_actions(state, goal, actions)  # scores only; no execution
```

Each `status_report()` or `score_actions()` call launches the configured
interpreter with `integrations/taiga/runner.py` and exchanges one JSON request
and response through a temporary directory. No sockets, HTTP servers, or
persistent workers are used. The parent never imports `freecad_s1` or `torch`.

Scoring requires a local model directory containing `config.json` (with a
`config` object) and `model.safetensors`. Repository ids are rejected, so no
implicit Hugging Face download can occur.

The two MCP tools are:

- `taiga_status` — runtime availability, versions, configured interpreter/model,
  and local-model validation. Never loads weights.
- `taiga_score_actions` — validated action/probability pairs for supplied JSON
  state, goal, and distinct action ids. Never executes an action.

Both return structured unavailable results when Taiga is not configured.

### Taiga tests

The normal suite needs no Taiga, Torch, weights, or network:

```bash
uv run pytest tests/test_taiga_runner.py tests/test_taiga_runtime.py \
  tests/test_taiga_adapter.py tests/test_taiga_workflow.py \
  tests/test_config.py tests/test_server.py -q
```

The runner and runtime tests use fake scripts and a fake `freecad_s1` package.
Adapter tests inject a fake runtime, and an import-isolation test runs a fresh
interpreter with `torch` and `freecad_s1` blocked.

`tests/test_taiga_integration.py` is the optional real-runtime test. It skips
unless `FREECAD_MCP_TAIGA_PYTHON` and `FREECAD_MCP_TAIGA_MODEL` point at an
existing interpreter and local model directory. To exercise real scoring as
well, provide schema-valid JSON in `FREECAD_MCP_TAIGA_TEST_STATE`,
`FREECAD_MCP_TAIGA_TEST_GOAL`, and `FREECAD_MCP_TAIGA_TEST_ACTIONS`. It never
downloads or installs anything.

Do not add Taiga's worker/RPC or GUI socket runtime without an explicit
milestone decision. A PartDesign command layer, live state capture, action
enumeration, and action execution do not exist in the bridge yet.

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

## Milestone 7 status: guarded loop skeleton only

`src/freecad_mcp/integrations/taiga/workflow.py` provides
`run_iterative_modeling(goal_description, *, model, python, state_provider,
          executor, max_iterations, ...)`. Read the following carefully — this
is **not** a working Method B pipeline.

What it actually does:

- **Scores actions.** Scoring goes through the existing
  `TaigaBackend.score_actions`, so the validated local-model loading, timeout
  handling, and `UnavailableReason` mapping are shared with the MCP tools
  rather than re-implemented.
- **Selects the highest-probability action** from Taiga's reply.
- **Terminates on `Done`.** `Done` is handled *before* the executor is
  consulted, so it never executes a modeling operation.
- **Refuses to invent anything.** Without a caller-supplied `state_provider`
  the run stops with `runtime_context_required`; without a caller-supplied
  `executor` the selected action is not executed and the run stops the same
  way. There is no built-in state capture and no built-in execution path.
- **Captures fresh state after each successful operation**, through the
  caller's `state_provider`, and feeds it to the next scoring step.
- **Never translates action ids into FreeCAD operations.** There is no
  `PartDesign_Pad → create_box` style mapping, because no such equivalence has
  been verified. Choosing the concrete operation is the executor's job.

What it explicitly does **not** do:

- it is not registered as an MCP tool, so no client can reach it;
- it does not capture live FreeCAD state (no `State` snapshot exists);
- it does not execute any FreeCAD operation (no PartDesign command layer);
- it does not validate resulting geometry (that is the bridge's `validate`
  operation, which this loop never calls);
- it has not been run against real Taiga-S1, Torch, weights, or FreeCAD.

Consequently `WorkflowResult.success` is `True` only when Taiga selects
`Done`. Reaching `max_iterations` is reported as not-success, and a run with
injected fakes proves loop mechanics only — never real modeling.

Mock-only coverage lives in `tests/test_taiga_workflow.py` (state/executor
boundaries, `Done` termination, fresh-state capture, malformed and partial
score envelopes, timeout mapping, iteration caps). No Taiga, Torch, weights,
network, or FreeCAD is needed:

```bash
uv run pytest tests/test_taiga_workflow.py -q
```

Before a fair Method A/Method B comparison can be built, verified live state
capture, candidate action enumeration, typed execution and feedback are
required (see [`taiga-s1.md`](taiga-s1.md) and the canonical
[product roadmap](../.kilo/ROADMAP.md)). The isolated `taiga_build` path is an
implemented build workflow, not proof of a general interactive controller.

### Planned comparison framework (not implemented)

- **Method A:** the LLM selects MCP modeling tools directly
  (`create_box`, `tank`, `tubular_coil`, ...).
- **Method B:** the LLM supplies a structured goal, and an iterative MCP–Taiga
  loop selects and executes one action at a time.

Neither method has been run end to end. Method B additionally depends on the
PartDesign execution boundary and live state capture that do not exist, so the
two cannot be compared fairly today. **Method A is currently the only path that
actually models anything.**

Metrics a future benchmark should record:
- Successful completion (goal achieved).
- Geometric correctness (valid shapes, non-zero volume).
- Number of executed operations.
- Failed operations (timeouts, invalid actions, runtime errors).
- Elapsed time per workflow.

Do not add Taiga's worker/RPC or GUI socket runtime without an explicit
milestone decision. A PartDesign command layer, live state capture, action
enumeration, and action execution remain unimplemented and are the prerequisite
for a Method B comparison.
