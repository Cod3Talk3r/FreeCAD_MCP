# Changelog

## Current task

- **M18 Phase 1 — Model Tree & Lifecycle GUI implemented and hardened** (working tree, not committed):
  - Backend: `get_document_hierarchy` operation in `runner.py` + MCP tool in `modeling.py` with `HierarchyNode`/`DocumentHierarchy`/`GetDocumentHierarchyParams` schemas exposing ownership hierarchy (Body.Group, Assembly.Group, JointGroup.Group) separately from InList/OutList dependencies.
  - GUI: Hierarchical model tree in `Mod/freecad_mcp_gui/panel.py` with dependency inspector, bidirectional 3D view ↔ tree selection sync via `FreeCADGui.Selection.addObserver`/`removeObserver`, context menu (Delete, Reorder Body features, Set Tip), tree-state preservation across refreshes; 35 `_objects_list.clear()` calls replaced with `_on_refresh_tree()`.
  - Hardening 1: SelectionObserver cleanup on dock destruction (`self._dock.destroyed.connect`).
  - Hardening 2: Reorder action eligibility limited to Body features only.
  - Hardening 3: 6 focused backend tests for `get_document_hierarchy` added to `test_runner.py`.
  - Automated tests: 6 new hierarchy tests + 534 test_runner + 33 body/tip/dependency + 13 M16 E2E + 14 sketch_hole = 594 focused tests passed; pyright clean (expected PySide6/FreeCAD missing-import errors).
  - **Not yet executed**: Real FreeCAD GUI manual smoke-test (panel open/close/destroy, tree refresh/state, selection sync both directions, context menu actions, assembly hierarchy, dependency inspector separation).
  - **GUI development paused**: After M18 Phase 1, GUI work is intentionally paused. Future GUI development will resume after core MCP roadmap milestones (M19-M20) with an Agent Collaboration Environment direction.
- M17 Native Assembly foundation implemented (research gate had PASSED on
  FreeCAD 1.1.4): six allowlisted bridge operations (`assembly_create`,
  `assembly_add_component`, `assembly_create_joint`, `assembly_ground_component`,
  `assembly_solve`, `assembly_inspect`) with MCP tools, Pydantic schemas, and
  shared-layer validation. Only Fixed/Revolute joints; GroundedJoint kept
  distinct; original linked objects verified unmodified; save/reopen
  persistence verified. 8 mock tests + 6 native 1.1.4 integration tests pass.
  Working tree only; not committed.
- Product architecture research and canonical forward roadmap documented in
  `docs/ROADMAP.md`, including FreeCAD 1.1.4 capability evidence, explicit
  Assembly research gate, shared GUI/MCP boundary, solver isolation, and M12+
  acceptance-driven milestones. Documentation only; no CAD operation code added.
- Sketch ellipse editing (`sketch_edit_ellipse`) orientation fixed against the
  verified FreeCAD 1.1.4 API: no `MajorDirection`/`MinorDirection`; use in-place
  `Part.Ellipse.rotate(Placement(...))` with an absolute angle delta. Focused
  unit and real FreeCAD integration tests pass. Changes are in the working
  tree, not committed.
- Sketch line editing (`sketch_edit_line`) fixed against the verified FreeCAD
  1.1.4 API: no `setGeometry`/`setLine`; use mutate-and-reassign of the
  `Geometry` property. Real FreeCAD integration tests pass. Changes are in the
  working tree, not committed.
- Added shared agent skills (`.agents/skills/token-saver`,
  `.agents/skills/project-memory`) and persistent project memory
  (`PROJECT_MEMORY/`), plus root `AGENTS.md` routing agents to them.
- Added native `PartDesign::Body` creation via MCP (`create_body` operation +
  `create_body` tool) to close the Body -> Sketch -> Pad workflow gap; `_op_sketch_pad`
  now also reuses an explicitly named existing Body. Focused unit and real FreeCAD
  1.1.4 integration tests pass. Changes are in the working tree, not committed.

## Milestones

- M0–M1: project init, MCP server skeleton, diagnostics — done.
- M2: FreeCAD bridge (connection/version diagnostics) — done.
- M3: direct modeling tools (incl. `boolean_cut`) — done.
- M4: tank and tubular coil geometry — done.
- M5: Taiga-S1 investigation and adapter contract — done.
- M6: isolated Taiga-S1 runtime (`taiga_status`, `taiga_score_actions`) — done
  (`396c45a`).
- M7: Taiga build execution (`taiga_build`) — done
  (`1949a5b` feat: add real Taiga FreeCAD build execution).
- M8: documentation and release preparation — planned.

## Recent commits

- `1949a5b` feat: add real Taiga FreeCAD build execution (HEAD).
- `df225fd` feat: add Taiga goal validation and build runtime.
- `b2b090a` feat: add cross-platform pathing and bridge transport.
- `a078b6e` chore: ignore local Kilo configuration.
- `54461ac` docs: add Taiga planning skill and update project memory.
- `396c45a` feat: add Taiga-S1 integration foundation.
- `ae62d24` Merge branch `feat/freecad-mcp`.
- `3f98a94` feat: add boolean cut modeling tool.

Record meaningful milestones here, not every minor edit.

---
