# Current State

## Latest verified state

- HEAD: `396c45a` — `feat: add Taiga-S1 integration foundation` (branch
  `feat/freecad-mcp`).
- Full test suite verified locally: `uv run pytest` → **255 passed, 9 skipped**
  (~24 s, 2026-11-02). The skips are FreeCAD-console-missing (7 tests) and
  opt-in real-Taiga integration tests (2 tests); no FreeCAD / Taiga runtime was
  available in this environment.

## Implemented

- Milestones 0–6 are code-complete:
  - MCP server skeleton + `server_info` / `freecad_status` diagnostics.
  - FreeCAD bridge via one-shot child process (`freecad_bridge/`).
  - Direct parametric modeling tools: create box/cylinder, set_parameter,
    recompute, list/inspect, validate, save, export STEP, `boolean_cut`, tank,
    tubular coil.
  - Optional Taiga-S1 integration: isolated one-shot subprocess runtime with
    `taiga_status` and `taiga_score_actions`.
- Exposed MCP tools: `server_info`, `freecad_status`, `create_document`,
  `create_box`, `create_cylinder`, `set_parameter`, `recompute`, `list_objects`,
  `inspect_object`, `validate_document`, `save_document`, `export_step`,
  `boolean_cut`, `tank`, `tubular_coil`, `taiga_status`, `taiga_score_actions`.

## Outstanding / known limitations

- Milestone 7 (Method A vs Method B comparison) is not implemented; only a
  guarded `run_iterative_modeling` skeleton exists (loop mechanics with injected
  fakes only).
- Milestone 8 (documentation/release preparation) not started.
- FreeCAD required from Milestone 2 on; developed against FreeCAD 1.1.x on a
  Windows host; system Python cannot import FreeCAD; bridge uses the FreeCAD
  console interpreter.
- Docs reference `src/freecad_mcp/validation/`, which does not exist yet.
- No end-to-end FreeCAD or real Taiga-S1 / Torch / weights verification yet.

## Task-specific notes

- Shared agent skills and persistent project memory were added under
  `.agents/skills/` and `PROJECT_MEMORY/`; root `AGENTS.md` routes agents to them.
- `gen_traceback.py` is an unrelated untracked file; do not modify or stage it.

## Taiga-S1 integration (verified 2026-11-02)

### Runtime compatibility (VERIFIED)
- **Upstream:** `/tmp/taiga-upstream`, pinned commit `84fdd19c487658a59805531fa306fa40e8f72555`
- **FreeCAD:** 1.1.4 on Windows at `E:\Softwares\FreeCAD`, embedded Python 3.11.14
- **Launch from WSL2:** Possible when `FREECAD_PYTHON` and `FREECAD_LIB` set to Windows paths; `freecad_s1` must be on PYTHONPATH for FreeCAD Python
- **Path note:** Taiga's `fcenv.py` has Linux/macOS candidates only; explicit env vars required for Windows

### Expert-driven execution (VERIFIED)
- Goal parsing works without external deps
- Expert-driven Worker execution works without PyTorch, safetensors, or trained model weights
- Worker executes Goals via Expert actions; learned Policy is optional and not required

### Repeated independent Hole verification (VERIFIED)
Test Goal executed successfully:
```python
Goal(
    features=[
        GoalFeature("base_box", {"w": 40, "d": 30, "h": 8}),
        GoalFeature("hole", {"r": 3, "x": -10, "y": 0}),
        GoalFeature("hole", {"r": 3, "x": 10, "y": 0}),
    ],
    scale=40,
)
```
- Both holes executed (two separate `PartDesign_Pocket` operations)
- Expert reselected `Face+Z` before second Hole
- Geometry: `iou=1.0`, `match=True`
- No execution exceptions
- **Limitation (UNVERIFIED):** Persistent FCStd artifact path/saved status not confirmed

### Architecture (VERIFIED decisions)
- **Method A:** OpenCode/LLM → FreeCAD MCP → direct FreeCAD modeling tools (existing)
- **Method B:** OpenCode/LLM + Taiga Task Planner Skill → Goal JSON → FreeCAD MCP → Taiga Worker + Expert → FreeCAD
- Both share workspace/artifact handling/inspection/validation/reporting; engines differ
- **Method B document lifecycle (PROPOSED):** Taiga Worker creates new document, executes Goal, saves FCStd; MCP opens/inspects that artifact. Avoid concurrent control of same document by Taiga and MCP.

### Current status (VERIFIED)
- `taiga_build` **not implemented**. Next: read-only architecture investigation + implementation plan for `taiga_build` based on current FreeCAD_MCP source.
- Investigation must locate prior test artifact (if exists), inspect Taiga integration and FreeCAD bridge, define `taiga_build` API/validation/error handling/process isolation/artifact lifecycle, preserve A/B experiment, produce file-by-file roadmap.

### Phase 0 verification (2026-11-03) — read-only experiments, no implementation
All run against real FreeCAD 1.1.4 embedded Python (`E:\Softwares\FreeCAD\bin\freecadcmd.exe`, py 3.11.14) and Taiga upstream at `/mnt/g/tmp_kilo_exp/taiga_src`. Scripts outside the repo (`/tmp/kilo/`, `/mnt/g/tmp_kilo_exp/e1_win/`). No repo source modified.

- **E1 path/envelope I/O — PASS (workaround):** Windows `G:\`, WSL `/tmp`, and UNC `\\wsl.localhost\Debian\tmp\` all readable/writable from Windows FreeCAD; paths with spaces + non-ASCII OK. **Blocker:** `freecadcmd.exe` strips custom env vars (`FREECAD_MCP_REQUEST/RESPONSE/PYTHONPATH` null) and auto-opens any file argv. **Workaround (verified):** fixed Windows data path + wrapper script computing paths internally; full `create_document→…→export_step` lifecycle works.
- **E2 build_target detached lifecycle + IoU — PASS:** `build_target` fills `goal.target`, returns detached Compound(1 solid, vol 9147.61) after session close; separate session Expert loop reaches `Done` (23 steps); **IoU = 1.0 ≥ 0.99**. `level`/`scale`/caller-supplied target all work.
- **E3 atomic save/metadata/locking — PASS:** atomic_save success/overwrite/failure-cleanup OK; meta read/write/missing/malformed OK; locking acquire/reacquire/reclaim-stale/malformed OK (live-owner block not demoable without an alive PID on this host). FreeCAD save→reopen→validate→inspect OK.
- **E4 post-save validation + Method A/B isolation — PARTIAL/needs adjustment:** current `_op_validate` false-fails PartDesign docs (Body=Compound, Origin=no Shape, sketches=Wire, planes/edges all invalid → `all_valid=false`). Must inspect `Body.Tip.Shape` instead of every object. Method A runner independent of Taiga: PASS (no Taiga in `runner.py`).

Decisions: (1) `taiga_build` ready to begin only after the E1 env-var workaround and E4 PartDesign-aware validation are addressed; (2) torch/model scoring, real Taiga weights, PartDesign-aware validation, persistent FCStd artifact path, live state capture/action execution, and M7 comparison remain out of scope until separately verified.
