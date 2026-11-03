# Feature Catalog (VERIFIED)

Source: `freecad_s1/schema.py`, `freecad_s1/goals.py`, `freecad_s1/runtime/params.py` @ `84fdd19c487658a59805531fa306fa40e8f72555`

## Base features
| kind | Required params | Notes |
|------|-----------------|-------|
| `base_box` | `w`, `d`, `h` | Pad on XY plane |
| `base_cyl` | `r`, `h` | Pad on XY plane |
| `base_hex` | `r`, `h` | Pad on XY plane |
| `base_ring` | `ri`, `ro`, `h` | Revolution on XZ plane |

## Additive/cut features (on top face)
| kind | Required params | Notes |
|------|-----------------|-------|
| `boss_cyl` | `r`, `x`, `y`, `h` | Pad on Face+Z |
| `boss_box` | `w`, `d`, `x`, `y`, `h` | Pad on Face+Z |
| `hole` | `r`, `x`, `y` | Pocket ThroughAll on Face+Z |
| `hole_std` | `r`, `x`, `y` | PartDesign_Hole on Face+Z |
| `pocket_rect` | `w`, `d`, `x`, `y`, `depth` | Pocket Length on Face+Z |

## Patterns/mirrors (use Tip selection)
| kind | Required params | Notes |
|------|-----------------|-------|
| `polar_pattern` | `n` (integer-valued) | Place **immediately after** target feature |
| `linear_pattern` | `n` (integer-valued), `length` | Place **immediately after** target feature |
| `mirror` | **none** (VERIFIED) | Place **immediately after** target feature |

## Dressups
| kind | Required params | Notes |
|------|-----------------|-------|
| `fillet_top` | `r` | Edges@Face+Z |
| `fillet_vertical` | `r` | Edges|Z |
| `chamfer_top` | `size` | Edges@Face+Z |
| `shell` | `t` | Face+Z |

## Classification
- **VERIFIED**: All kinds and params above from source.
- **INFERRED**: Specific combinations may be valid beyond curriculum examples.
- **UNVERIFIED**: Features not in this list are unsupported.
