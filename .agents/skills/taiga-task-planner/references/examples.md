# Examples (VERIFIED/ADAPTED/INFERRED)

Source: `tests/test_expert.py`, `tests/test_ui.py`, `freecad_s1/goals.py` curriculum

## Example 1: Exact upstream example
**Label:** Exact upstream example  
**Source:** `tests/test_expert.py:5`  
**Request:** "Box 20x10x5 with fillet radius 1 on top edges"  
**Goal:**
```json
{
  "features": [
    {"kind": "base_box", "params": {"w": 20.0, "d": 10.0, "h": 5.0}},
    {"kind": "fillet_top", "params": {"r": 1.0}}
  ],
  "scale": 20.0
}
```

## Example 2: Exact upstream feature example
**Label:** Exact upstream example (feature parts)  
**Source:** `tests/test_ui.py:41-42`  
**Request:** "Hole radius 3 at center on top face" / "Pocket 6x5x3 at center"  
**Hole Goal (needs base):**
```json
{
  "features": [
    {"kind": "base_box", "params": {"w": 30.0, "d": 30.0, "h": 10.0}},
    {"kind": "hole", "params": {"r": 3.0, "x": 0.0, "y": 0.0}}
  ],
  "scale": 30.0
}
```

## Example 3: Adapted upstream example
**Label:** Adapted upstream example (from curriculum `_cut_group`)  
**Request:** "Cylindrical base with 6 holes in a circle"  
**Goal:**
```json
{
  "features": [
    {"kind": "base_cyl", "params": {"r": 25.0, "h": 10.0}},
    {"kind": "hole", "params": {"r": 5.0, "x": 15.0, "y": 0.0}},
    {"kind": "polar_pattern", "params": {"n": 6}}
  ],
  "scale": 50.0
}
```
*Note:* hole placed immediately before polar_pattern (Tip). n is integer-valued.

## Example 4: Ask for missing info (correct behavior)
**Label:** INFERRED (correct behavior)  
**Request:** "Make a box with a hole"  
**Behavior:** **Do not generate Goal.** Ask:
- "What are the box dimensions (w, d, h)?"
- "What hole diameter/radius and position?"
- "Should the hole go all the way through (hole) or partway (pocket)?"

## Example 5: Inferred example with boss and fillet
**Label:** Inferred example  
**Request:** "Box 30x30x10 with cylindrical boss radius 5 at (10,0) height 3, fillet top edges radius 0.5"  
**Goal:**
```json
{
  "features": [
    {"kind": "base_box", "params": {"w": 30.0, "d": 30.0, "h": 10.0}},
    {"kind": "boss_cyl", "params": {"r": 5.0, "x": 10.0, "y": 0.0, "h": 3.0}},
    {"kind": "fillet_top", "params": {"r": 0.5}}
  ],
  "scale": 30.0
}
```

**Notes:** All examples classify as Exact/Adapted/Inferred. No action sequences emitted.
