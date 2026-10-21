# Taiga-S1 investigation (Milestone 5)

This note records what was verified about **Taiga-S1**, how it relates to this
project, and the deliberately limited adapter implemented in Milestone 5.

**Decision:** adopt Taiga-S1 behind an optional, import-safe adapter for
availability reporting and next-action scoring. **Do not** adopt its runtime,
sockets, or command execution in this milestone. A full Method B execution
path remains deferred until the missing PartDesign layer is designed.

## Upstream sources

| Item | Verified value |
| ---- | -------------- |
| Repository | <https://github.com/shhivv/taiga-s1> (redirects to `shhivv/biome-s1`) |
| Pinned revision | [`84fdd19c487658a59805531fa306fa40e8f72555`](https://github.com/shhivv/biome-s1/commit/84fdd19c487658a59805531fa306fa40e8f72555) (2026-10-08) |
| License | MIT, Copyright (c) 2026 Shiv Shanmugam |
| Distribution | `taiga-s1` |
| Python package | `freecad_s1` |
| Python requirement | `>=3.11` |
| Runtime dependencies | `torch>=2.3`, `numpy`, `safetensors`, `huggingface_hub` |
| Published weights | <https://huggingface.co/shhivv/taiga-s1> |

The upstream project was renamed to **Biome-S1** when it became a model family
(Taiga-S1 plus Mesa-S1). The `taiga-s1` repository URL still resolves, but the
canonical repository is now `shhivv/biome-s1`. This document pins the revision
above so the findings do not silently drift.

## What Taiga-S1 is

Taiga-S1 is a small (about 1.2M parameters) next-action model for FreeCAD
PartDesign workflows. A planner supplies an ordered goal of features (for
example a plate, a hole, a pattern, a fillet). Taiga-S1 reads a structured
snapshot of the live FreeCAD session and scores the commands that are valid at
that moment. It chooses *which command* to run; numeric values come from the
goal through a separate parameter stage.

The verified Python API is:

```python
from freecad_s1.model.net import from_pretrained
from freecad_s1.rollout import Policy

model = from_pretrained("shhivv/taiga-s1")
policy = Policy(model, device="cpu")
probabilities = policy.score(state, goal, actions)
```

`state` and `goal` are records from `freecad_s1.schema` (`State.from_json` and
`Goal.from_json` accept JSON-compatible dictionaries). `actions` is a list of
command ids such as `PartDesign_Pad`, `Sketcher_CreateCircle`, `Select:Face+Z`,
or `Done`. `Policy.score` returns a mapping of action id to calibrated
probability, highest first.

Crucially, this API scores **one step**. It does not return a multi-step plan.
Producing a plan requires applying the chosen command to FreeCAD, reading the
new state, and repeating.

## Runtime requirements and architecture

Taiga-S1 is not just a model package. The upstream runtime:

- locates a FreeCAD Python interpreter through `FREECAD_PYTHON` and `FREECAD_LIB`;
- starts a FreeCAD worker process (`python -m freecad_s1.runtime.worker`) and
  exchanges line-delimited JSON over stdin/stdout, prefixed with `@@S1 `;
- snapshots the live document tree, selection, sketch state, and shape;
- enumerates the currently valid actions;
- executes commands and fills their task dialogs from the goal;
- for GUI demos, also uses a local socket server inside FreeCAD.

The torch process does not import FreeCAD; FreeCAD runs in a separate worker.

## Compatibility with this project

The current bridge is intentionally a fixed-operation, one-shot child process:

```
freecad_mcp MCP tool
  -> FreeCadBridge.run(op, params)
  -> freecadcmd.exe runner.py
  -> explicit operation from runner.OPERATIONS
```

Its verified operations are document creation, `Part::Box`,
`Part::Cylinder`, parameter updates, recompute, object listing/inspection,
validation, document saving, STEP export, and the M4 tank/coil helpers. It has
**no PartDesign command vocabulary** (`PartDesign_Pad`, `PartDesign_Pocket`,
`PartDesign_Fillet`, sketches, patterns, and so on).

Therefore:

- Taiga-S1 cannot be driven through the existing bridge unchanged.
- Translating Taiga commands into bridge operations would require a new
  PartDesign layer, which is out of scope for Milestone 5.
- Adopting Taiga's worker/RPC or GUI socket runtime would add a second
  execution architecture and a much larger dependency surface. That is also
  out of scope and would conflict with the current local-only, one-shot bridge.
- The default environment must not install Taiga or Torch. Method A works
  without them.
- Because Taiga requires Python >=3.11, the adapter reports an incompatible
  status on Python 3.10. The project's default development environment is
  Python 3.14; Torch wheel availability for that interpreter was not verified
  here, and the adapter never installs Torch itself.

## Implemented adapter (actual capabilities)

`src/freecad_mcp/integrations/` contains a dependency-free contract and an
optional Taiga adapter.

### `integrations/base.py`

- `IntegrationStatus` — available/unavailable state, optional version, and a
  structured `UnavailableReason`.
- `PlanStep` — an explicit, declarative operation plus parameters. It is data,
  not an executed command.
- `PlanningResult` — either a generated, non-empty plan or a structured
  unavailable result; an empty "successful" plan is rejected.
- `PlanningBackend` — a small abstract contract with `status()` and `plan()`.

The module imports only the standard library. It never imports FreeCAD, Torch,
or Taiga.

### `integrations/taiga/adapter.py`

`TaigaBackend` provides:

1. **Availability detection** — imports `freecad_s1` only when `status()` is
   called, checks that Torch is installed with `find_spec` (without loading
   it), and reports the upstream package version when metadata is available.
2. **Structured unavailability** — missing Taiga, missing Torch, unsupported
   Python, incompatible APIs, and invalid requests all return typed results.
   Method A is never broken by an absent optional dependency.
3. **`score_actions(...)`** — the verified `Policy.score` operation. It
   converts the supplied JSON state/goal with `State.from_json` /
   `Goal.from_json`, calls the model, and returns typed `ActionScore` values.
   It does **not** execute any command.
4. **Deferred `plan(...)`** — returns
   `UnavailableReason.RUNTIME_CONTEXT_REQUIRED` with an explanation. It never
   fabricates a plan or claims that one was generated.

Milestone 5 registers no Taiga MCP tool: the adapter is a library component
for tests and future milestones, not a replacement for Method A's tools.

Loading the model through a Hugging Face repository id may download weights on
first use; pass a local directory to `TaigaBackend(model=...)` to avoid that.
`score_actions` only works when the MCP server itself runs in an environment
where `freecad_s1` and Torch are importable. Those packages are not project
dependencies and are not installed by `uv sync`.

## What is deferred

The following are explicitly **not** implemented:

- a PartDesign command layer in `freecad_bridge/runner.py`;
- Taiga's worker/RPC client or GUI socket server;
- snapshotting the live FreeCAD state in a form Taiga accepts;
- enumerating valid actions from the bridge;
- applying model-produced commands and recomputing;
- end-to-end Method B builds and Method A/Method B comparison.

Milestone 6 (comparison framework) should not assume these exist. A later
milestone must first define the PartDesign execution boundary and verify it
against real FreeCAD.

## Verification in this repository

Tests use a fake `freecad_s1` package, so they run without Taiga or Torch:

- the base contract with a fake backend;
- import isolation in a fresh interpreter where `torch` and `freecad_s1` are
  blocked;
- missing Taiga, missing Torch, and unsupported Python statuses;
- deferred planning even when Taiga appears available;
- scoring through the verified API shape, including policy caching and
  structured failures for malformed or incompatible results.

No test claims that a real Taiga model built geometry. Real FreeCAD
integration tests continue to exercise Method A only.
