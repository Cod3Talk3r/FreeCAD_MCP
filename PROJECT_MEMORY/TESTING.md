# Testing

## Commands

- Full suite: `uv run pytest` (or `uv run pytest -q`).
- FreeCAD-gated integration tests: `uv run pytest tests/test_freecad_integration.py -v`
  — skips when no FreeCAD console is found; set `FREECAD_MCP_FREECAD_EXECUTABLE` to override.
- Taiga unit tests (no Taiga/Torch/weights/network needed):
  `uv run pytest tests/test_taiga_runner.py tests/test_taiga_runtime.py tests/test_taiga_adapter.py tests/test_taiga_workflow.py tests/test_taiga_goal.py tests/test_taiga_build.py tests/test_taiga_build_tool.py tests/test_taiga_child.py tests/test_taiga_diagnostic.py tests/test_config.py tests/test_server.py -q`
- Guarded-loop skeleton tests: `uv run pytest tests/test_taiga_workflow.py -q`.
- Real Taiga/FreeCAD integration (opt-in, Windows only):
  `FREECAD_MCP_FREECAD_EXECUTABLE="E:\Softwares\FreeCAD\bin\freecadcmd.exe" FREECAD_MCP_TAIGA_SOURCE="G:\...\taiga-upstream-copy" uv run pytest tests/test_taiga_integration.py tests/test_taiga_diagnostic.py -q -rs`

## Verified results

- 2026-11-04 (Linux, Python 3.11.18): `uv run pytest -q -rs` -> **366 passed,
  9 skipped**. Windows FreeCAD 1.1.4 launched from WSL passed direct ping,
  version, and a temporary document/tank/reopen diagnostic. Native Windows
  Python and the full real FreeCAD integration suite remain unverified.
- 2026-11-04 (Linux): `uv run --no-sync pytest -q -rs` → **362 passed, 9 skipped**
  after Phase 4 M1 application-layer changes. Real FreeCAD/Taiga integration
  was not configured for this run.
- 2026-11-03 (this environment, Linux): `uv run pytest -q` → **335 passed, 9 skipped**
  in ~31 s. Skips: FreeCAD integration tests (console not found) and real-Taiga
  integration tests (env not configured).
- Real Windows integration (FreeCAD 1.1.4, Taiga upstream
  `84fdd19c`): `tests/test_taiga_integration.py` → **2 passed**.

## Limitations

- FreeCAD-dependent and real-Taiga tests skip when their runtime is not configured.
- Never record unverified results as successful.

---
