# Current State

## Latest verified state

- HEAD: `38416c4` — shared application backend layer (branch `feat/freecad-mcp`).
- Python 3.11 development pin and direct FreeCAD transport fixes are uncommitted.
  The bridge uses `-c` and `--pass`; native Windows integration needs verification.
- Phase 4 M1 introduces shared application contracts and direct/Taiga adapters;
  MCP schemas and the existing headless execution runtimes remain in place.
  Active-document execution and the GUI are not implemented.
- Full test suite verified locally: `uv run pytest -q` → **335 passed, 9 skipped**
  (~31 s). Skips are FreeCAD-console-missing and opt-in real-Taiga integration
  tests; no FreeCAD / Taiga runtime was available in this Linux environment.
- Real Windows integration test (`tests/test_taiga_integration.py`):
  **2 passed** against FreeCAD 1.1.4 at `E:\Softwares\FreeCAD\bin\freecadcmd.exe`
  with Taiga source at `G:\Cources\py\Projects\taiga-upstream-copy`.
- CLI argument transport uses FreeCAD's `--pass` mechanism (positional args
  after `--pass`) because `freecadcmd.exe` strips custom env vars and
  intercepts `--response` as its internal `--response-file` option.

## Implemented

- Milestones 0–7 are code-complete:
  - MCP server skeleton + `server_info` / `freecad_status` diagnostics.
  - FreeCAD bridge via one-shot child process (`freecad_bridge/`).
  - Direct parametric modeling tools: create box/cylinder, set_parameter,
    recompute, list/inspect, validate, save, export STEP, `boolean_cut`, tank,
    tubular coil.
  - Optional Taiga-S1 integration: isolated one-shot subprocess runtime with
    `taiga_status` and `taiga_score_actions`.
  - **Taiga build execution (`taiga_build`):** real isolated FreeCAD child
    process runs feasibility + Expert-driven execution loop, atomic FCStd save,
    metadata side-car, and in-child post-save validation (PartDesign-aware
    `Body.Tip.Shape` inspection).

## Exposed MCP tools

`server_info`, `freecad_status`, `create_document`, `create_box`,
`create_cylinder`, `set_parameter`, `recompute`, `list_objects`,
`inspect_object`, `validate_document`, `save_document`, `export_step`,
`boolean_cut`, `tank`, `tubular_coil`, `taiga_status`, `taiga_score_actions`,
`taiga_build`.

## New files (Phase 1–3)

- `src/freecad_mcp/pathing.py` — shared path translation (WSL2↔Windows via
  `wslpath`, no shell interpolation).
- `src/freecad_mcp/freecad_bridge/taiga_runner.py` — FreeCAD-side Taiga build
  runner (child process only; imports FreeCAD/freecad_s1 lazily).
- `src/freecad_mcp/integrations/taiga/child.py` — parent-side `TaigaChildRunner`
  (argv-based `--pass` transport, diagnostics, error classification).
- `src/freecad_mcp/tools/taiga.py` — `taiga_build` MCP tool registration plus
  `TaigaBuildParams`/`TaigaBuildResult` contracts.

## New config settings

- `FREECAD_MCP_TAIGA_SOURCE` — Taiga-S1 source repository root (child imports
  `freecad_s1` from here). `None` → Taiga build unavailable.
- `FREECAD_MCP_TAIGA_BUILD_TIMEOUT_SECONDS` — per-build timeout (default 120s).

## Outstanding / known limitations

- Milestone 8 (documentation/release preparation) not started.
- FreeCAD required from Milestone 2 on; developed against FreeCAD 1.1.x on a
  Windows host; system Python cannot import FreeCAD; bridge uses the FreeCAD
  console interpreter.
- `subprocess.run` timeout does not kill the child on expiry (Python stdlib
  limitation); orphaned lock files handled by workspace stale-lock detection.
- Real FreeCAD integration not runnable in this Linux environment; gated behind
  `FREECAD_MCP_FREECAD_EXECUTABLE` + `FREECAD_MCP_TAIGA_SOURCE`.

## Task-specific notes

- Shared agent skills and persistent project memory were added under
  `.agents/skills/` and `PROJECT_MEMORY/`; root `AGENTS.md` routes agents to them.
- `gen_traceback.py` is an unrelated untracked file; do not modify or stage it.

## Taiga-S1 integration (verified 2026-11-03)

### Runtime compatibility (VERIFIED)
- **Upstream:** `/mnt/g/tmp_kilo_exp/taiga_src`, pinned commit
  `84fdd19c487658a59805531fa306fa40e8f72555`
- **FreeCAD:** 1.1.4 on Windows at `E:\Softwares\FreeCAD`, embedded Python 3.11.14
- **Launch from WSL2:** Possible when `FREECAD_PYTHON` and `FREECAD_LIB` set to
  Windows paths; `freecad_s1` must be on PYTHONPATH for FreeCAD Python

### Expert-driven execution (VERIFIED)
- Goal parsing works without external deps (`freecad_s1.schema.Goal.from_json`)
- Expert-driven Worker execution works without PyTorch, safetensors, or trained
  model weights
- Worker executes Goals via Expert actions; learned Policy is optional

### Build lifecycle (VERIFIED on Windows)
- Feasibility session + separate execution session; `build_target` returns
  detached target shape valid after feasibility session closes
- `Std_Undo` = failure in feasibility, recovery in execution
- Empty Expert result (`[]`) = unrecoverable failure; `Done` = completion
- Step budget: `2 * expert_plan_length + 6`
- IoU threshold: `>= 0.99` for success
- Detached target does not leak into execution session
- Atomic FCStd save (temp `.fcstd` + `os.replace`) while document open
- Metadata side-car separate from FCStd; `artifact_written=true,
  metadata_written=false` when metadata fails
- Lock held through feasibility, execution, save, metadata, post-save validation;
  released in `finally` on every path
- In-child post-save validation reopens saved artifact via `FreeCAD.openDocument`
- PartDesign-aware validation inspects `Body.Tip.Shape` only (not sketches,
  origin objects, wires)

### Architecture (VERIFIED decisions)
- **Method A:** OpenCode/LLM → FreeCAD MCP → direct FreeCAD modeling tools
- **Method B:** OpenCode/LLM + Taiga Task Planner Skill → Goal JSON → FreeCAD
  MCP → Taiga Worker + Expert → FreeCAD
- `freecad_bridge/runner.py` remains Method A only; no Taiga execution logic
- Parent process never imports `FreeCAD` or `freecad_s1`; child imports them
  lazily
- Request/response via argv (`--pass` positional args), not env vars
- FreeCAD child alone owns the document lock

### Current status (VERIFIED)
- `taiga_build` **implemented and committed** (`1949a5b`).
- Next: Milestone 8 (documentation/release preparation).

### Phase 0 verification (2026-11-03) — read-only experiments
All run against real FreeCAD 1.1.4 embedded Python and Taiga upstream at
`/mnt/g/tmp_kilo_exp/taiga_src`. No repo source modified during experiments.

- **E1 path/envelope I/O — PASS:** Windows `G:\`, WSL `/tmp`, and UNC
  `\\wsl.localhost\Debian\tmp\` all readable/writable. Paths with spaces +
  non-ASCII OK. `freecadcmd.exe` strips custom env vars. Workaround: argv
  transport via `--pass`.
- **E2 build_target detached lifecycle + IoU — PASS:** detached Compound
  (1 solid, vol 9147.61) valid after session close; Expert reaches `Done`;
  IoU = 1.0 >= 0.99.
- **E3 atomic save/metadata/locking — PASS:** atomic_save OK; meta read/write
  OK; locking acquire/reacquire/reclaim-stale OK.
- **E4 post-save validation + Method A/B isolation — PASS:** inspect
  `Body.Tip.Shape` instead of every object; Method A runner independent of
  Taiga.

---
