# Validation (VERIFIED/INFERRED)

Source: `freecad_s1/schema.py`, `freecad_s1/goals.py`, `freecad_s1/actions.py`, `freecad_s1/rollout.py`

## Syntactic vs executable
- **Syntactic validity**: Parseable, known kinds, required params present, basic ordering OK. **Checkable without FreeCAD.**
- **Executable validity**: Can run successfully from current state. **Requires live FreeCAD state** (workbench, body, selection, geometry). **Never guaranteed by passing preflight.**

## No reusable semantic validator (VERIFIED)
No standalone `validate_goal()` exists. Validation distributed across `Goal.from_json()`, `RECIPES`, `enumerate_actions()`, expert/rollout.

## Preflight checklist
- [ ] `features` non-empty list
- [ ] First feature is supported base kind
- [ ] All kinds in `GOAL_KINDS`/`RECIPES`
- [ ] Required params present per kind (see feature-catalog.md)
- [ ] If pattern/mirror present → immediately after target feature
- [ ] Pattern counts are integer-valued
- [ ] `mirror` has no params
- [ ] Scale computed from base (or reasonable)
- [ ] Params numeric
- [ ] No unsupported features

## Runtime checks (require FreeCAD/state) - INFERRED
- Face/edge existence for dressups
- Body exists, correct workbench
- Selection validity, Tip exists
- Geometry feasibility

## Rules
- **Ask, don't guess** essential dimensions.
- **Explain limitations** for unsupported/ambiguous.
- **Never claim executable** just because structurally valid.
- **Don't treat held-out as runtime errors** unless verified.
