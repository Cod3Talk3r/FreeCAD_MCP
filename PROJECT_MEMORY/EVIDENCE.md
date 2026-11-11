# Evidence Format and Capability Tiers (M12)

Defines the evidence format and tier labels used to record FreeCAD 1.1.4
capability evidence. Follows decision **D24 — Capability evidence tiers**.
Source code and verified test results take precedence over this document.

## Evidence tiers (in increasing strength)

Each claim about a FreeCAD capability must state the highest tier reached:

| Tier | Label | What it proves | Example evidence |
|------|-------|---------------|------------------|
| T1 | Importable | Module/type can be imported in FreeCAD console | `import PartDesign` succeeds |
| T2 | Type/property available | Object type and property names exist and are settable | `PartDesign::Groove` creatable, `Profile`, `ReferenceAxis`, `Angle` properties settable |
| T3 | Recompute | Document recomputes without error after feature creation | `doc.recompute()` returns without exception |
| T4 | Valid geometry | Resulting shape is non-null, valid, expected solid count | `Shape.isValid()`, `len(Shape.Solids) == 1`, volume > 0 |
| T5 | Save/reopen | Feature survives `saveAs` → close → `openDocument` → `recompute` | Shape, links, Body.Tip, Body.Group all persist intact |
| T6 | Parametric edit | Feature input can be changed after reopen and recompute produces expected new geometry | `groove.Angle = 180.0`, recompute, volume changes accordingly |

A claim must not assert a higher tier without evidence at every lower tier.
**Mocks do not establish native support** — mocked FreeCAD APIs prove project
contracts only, never native CAD behavior.

## Evidence record format

Every capability probe log must include:

```text
Capability: <operation name, e.g. sketch_groove>
FreeCAD version: 1.1.4R20260928 (commit 4fd3bf320d9566a27e60069fc8387448aaa3a094)
Executable: /mnt/e/Softwares/FreeCAD/bin/freecadcmd.exe
Platform: Windows (run from WSL2)

T1 Importable: PASS — Part, PartDesign, Sketcher imported successfully
T2 Type/property available: PASS — PartDesign::Groove creatable; Profile, ReferenceAxis, BaseFeature, Angle settable
T3 Recompute: PASS — doc.recompute() completed without error
T4 Valid geometry: PASS — Shape valid, 1 solid, volume 22736.2961 mm^3
T5 Save/reopen: PASS — Shape, BaseFeature, Profile, ReferenceAxis, Axis, Body.Tip, Body.Group all persist through save/close/reopen/recompute
T6 Parametric edit: NOT TESTED — no edit-after-reopen probe performed

Negative cases:
- Invalid axis Z_Axis rejected with clear error
- Invalid angle (>360, <=0, NaN) rejected with clear error
- Missing sketch rejected with clear error
- Failed recompute leaves on-disk document unchanged (rollback)
```

## Evidence versus mocks

| Evidence type | What it establishes | M12 acceptance |
|--------------|-------------------|----------------|
| Mock/unit test | Project contracts, delegation, schema validation, error mapping | Accepted for contract testing only |
| Real FreeCAD integration | Native API behavior, recompute, validity, persistence | Required for capability claims |
| Module import | Type/property availability only | T1/T2 only; not proof of valid geometry |

Tests must be labeled: `test_real_freecad_*` requires `freecadcmd`; `test_*` without the prefix runs without FreeCAD. A missing executable is a **skip**, never a pass.

## Capability table staleness

Capability tables (`docs/ROADMAP.md`, `PROJECT_MEMORY/TESTING.md`) must include the FreeCAD version against which evidence was gathered. Tables without a version are considered stale and must be re-verified before use.

## Related files

- `docs/ROADMAP.md` — M12 scope and FreeCAD 1.1.4 capability matrix
- `PROJECT_MEMORY/DECISIONS.md` — D24 evidence tiers
- `PROJECT_MEMORY/TESTING.md` — test commands and verified results
- `tests/test_m12_capability_discovery.py` — operation/tool registration parity
- `tests/test_modeling_tool_params.py` — schema coverage for all registered operations
