# Planning Rules (VERIFIED/INFERRED)

Source: `freecad_s1/actions.py`, `freecad_s1/goals.py`, `freecad_s1/runtime/params.py`, `tests/test_expert.py`

## Ordering (VERIFIED)
1. **Base first**: First feature must be a supported base feature.
2. **Solid required**: Bosses/cuts/patterns/dressups require an appropriate preceding solid.
3. **Patterns/mirrors use Tip**: Place `polar_pattern`, `linear_pattern`, `mirror` **immediately after** the feature they pattern/mirror.
4. **Dressups**: Can follow features; select by geometry (edges/faces). May appear in various positions.
5. **Ordered intent**: Goal features are in construction order.

## Dependencies (VERIFIED/INFERRED)
- **Patterns/mirrors**: Identify source by position (Tip = last feature). No explicit feature references in Goal.
- **Mirror**: Curriculum shows mirror can follow hole/hole_std/pocket_rect/boss_cyl (some combos held-out in training). **Do not treat held-out as confirmed runtime errors.**
- **Dressups after other features**: INFERRED as possible; depends on geometry.

## Planning guidance
- **Do not generate action sequences** - only Goal features.
- **No explicit references** - Don't add fields to reference previous features.
- **Distinguish**: VERIFIED combinations vs INFERRED vs UNVERIFIED.
- **Held-out restrictions** (e.g., pattern+boss_box, mirrored_hole_std in training) are curriculum restrictions per `goals.py:239-280`; **not confirmed runtime errors**.
