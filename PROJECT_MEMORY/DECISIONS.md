# Decisions

Significant decisions with rationale. Keep concise; source code wins on conflict.

- **D1 — One-shot child process for FreeCAD (M2):** bridge talks to
  `freecadcmd` via JSON request/response envelopes; timeout (default 60 s) and
  typed errors. Rationale: local-only, bounded, no service. Alternative
  (persistent process / sockets) deferred.
- **D2 — Explicit validated tools, no generated-Python execution (M3):** MCP
  surface exposes typed operations; arbitrary Python inside FreeCAD rejected.
  Rationale: safety and verifiability.
- **D3 — Optional dependency-free integration contract (M5):** `integrations/base.py`
  defined before any backend work; core never imports optional deps.
- **D4 — Isolated one-shot Taiga-S1 runtime (M6):** explicit interpreter via
  `FREECAD_MCP_TAIGA_PYTHON`, strict local model dir, status + score only
  (`plan()` → `RUNTIME_CONTEXT_REQUIRED`). Rationale: isolation; upstream
  worker/sockets rejected pending verified PartDesign boundary. Pinned upstream
  revision: `84fdd19c487658a59805531fa306fa40e8f72555`.
- **D5 — Evidence over assumption:** FreeCAD APIs, MCP SDK, OpenCode MCP config
  format, and Taiga-S1 interfaces verified before reliance; nothing invented.

Add new dated decisions here; do not record trivial implementation details.

- **D6 — Taiga Worker execution via Expert (2026-11-02):** Use Expert-driven execution (no PyTorch/weights) for initial integration. Policy is optional. Windows FreeCAD 1.1.4 compatible when configured with `FREECAD_PYTHON`/`FREECAD_LIB` pointing to Windows paths. Rationale: avoids heavy deps, verified working.
- **D7 — Method A/B separation (2026-11-02):** Preserve two distinct approaches sharing infrastructure. Method B uses Goal JSON + Taiga Worker creating/saving its own document, MCP inspects artifact separately (avoid concurrent control). Rationale: verified execution pattern, clear separation of concerns.
