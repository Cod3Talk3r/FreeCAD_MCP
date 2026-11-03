---
name: taiga-task-planner
description: Translate natural-language CAD requests into structured Taiga-S1 Goal JSON. The Agent plans; Taiga-S1 selects actions and executes. Outputs Goal JSON compatible with Goal.from_json() or asks for missing info.
---

# Taiga Task Planner

## Purpose
Convert user intent into a valid Taiga-S1 Goal (ordered feature intents). Taiga-S1 decides low-level FreeCAD actions; this Skill does **not** emit action sequences.

## When to use
Use when the user wants to build a model with Taiga-S1 and needs a structured Goal. Do **not** use to generate FreeCAD commands or arbitrary Python.

## Workflow
1. **Parse** - Understand base shape and requested features (bosses, holes/cuts, patterns, dressups).
2. **Identify base** - Pick exactly one supported base feature first.
3. **Extract params** - Get dimensions/positions/counts. **Ask** if essential values missing.
4. **Plan order** - Base → features in order. Place patterns/mirrors **immediately after** the feature they pattern/mirror (Tip). Dressups after relevant geometry.
5. **Compute scale** - From base using upstream logic (see references/goal-schema.md).
6. **Validate** - Run structural/semantic preflight (references/validation.md). Classify as VERIFIED/INFERRED/UNVERIFIED.
7. **Output or ask** - If complete and unambiguous → Goal JSON. Else ask concise questions. Never guess essential dims.

## Hard rules
- First feature must be a supported base feature.
- Patterns/mirrors use Tip → place immediately after target feature.
- No explicit feature references or low-level action sequences.
- Pattern counts must be integer-valued (even if stored as numbers).
- `mirror` has no parameters (verified).
- Omit `target` for ordinary execution goals. Omit `level` unless verified reason.
- Do not fabricate target ShapeInfo.
- Do not claim executable just because structurally valid.
- Do not treat held-out/curriculum restrictions as confirmed runtime errors.
- If mixed supported/unsupported → explain limitation and ask how to proceed.

## Quick reference
- Feature catalog: `references/feature-catalog.md`
- Goal schema: `references/goal-schema.md`
- Planning rules: `references/planning-rules.md`
- Validation: `references/validation.md`
- Examples: `references/examples.md`

## Output format
Return Goal JSON compatible with `Goal.from_json()`:
```json
{
  "features": [{"kind": "...", "params": {...}}],
  "scale": <number>
}
```
