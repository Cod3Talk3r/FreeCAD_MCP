# Goal Schema (VERIFIED)

Source: `freecad_s1/schema.py` @ `84fdd19c487658a59805531fa306fa40e8f72555`

## Structure
```python
@dataclass
class GoalFeature:
    kind: str
    params: dict[str, float] = field(default_factory=dict)

@dataclass
class Goal:
    features: list[GoalFeature]
    level: int = 1
    scale: float = 1.0
    target: ShapeInfo = field(default_factory=ShapeInfo)
```

- `features` (required): ordered list of feature intents
- `level` (optional): defaults 1; affects complexity/curriculum/eval. **May omit** unless verified reason.
- `scale` (optional): defaults 1.0; normalizes lengths. **Compute from base** when possible.
- `target` (optional): `ShapeInfo`. **Omit for ordinary execution goals** (used for evaluation). Do **not** fabricate.

## Serialization
- `Goal.to_json()` returns `asdict(self)`
- `Goal.from_json(d)` expects `features` list of dicts; optional `level`, `scale`, `target` (bbox as tuple/list)

## Scale computation (VERIFIED)
From `freecad_s1/goals.py:_scale()`:
- `base_box`: `max(w,d,h)`
- `base_ring`: `max(2*ro,h)`
- else (base_cyl/base_hex): `max(2*r,h)`

Compute this from the base feature dimensions.

## Notes
- Params are numeric (floats in dict). Pattern counts are integer-valued conceptually but stored as numbers; cast to int where needed.
- Goal is declarative intent, not action sequences.
