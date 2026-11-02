# Testing

## Commands

- Full suite: `uv run pytest` (or `uv run pytest -q`).
- FreeCAD-gated integration tests: `uv run pytest tests/test_freecad_integration.py -v`
  — skips when no FreeCAD console is found; set `FREECAD_MCP_FREECAD_EXECUTABLE` to override.
- Taiga unit tests (no Taiga/Torch/weights/network needed):
  `uv run pytest tests/test_taiga_runner.py tests/test_taiga_runtime.py tests/test_taiga_adapter.py tests/test_taiga_workflow.py tests/test_config.py tests/test_server.py -q`
- Guarded-loop skeleton tests: `uv run pytest tests/test_taiga_workflow.py -q`.

## Verified results

- 2026-11-02 (this environment, Linux): `uv run pytest` → **255 passed, 9 skipped**
  in ~24 s. Skips: 7 FreeCAD integration tests (console not found) and 2 real-Taiga
  integration tests (env not configured).

## Limitations

- No real FreeCAD instance or real Taiga-S1 / Torch / weights runs have been
  verified yet; FreeCAD-dependent and real-Taiga tests skip instead of failing
  when the runtime is absent.

- Never record unverified results as successful.
