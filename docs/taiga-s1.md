# Taiga-S1 integration (Milestones 5–6)

This note records what was verified about **Taiga-S1**, how it relates to this
project, and the isolated runtime integrated in Milestone 6.

**Decision:** adopt Taiga-S1 behind an optional, import-safe adapter and a
one-shot subprocess runtime for availability reporting and next-action scoring.
**Do not** adopt its persistent worker, sockets, or command execution. A full
Method B execution path remains deferred until the missing PartDesign layer is
designed and verified against real FreeCAD.

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

model = from_pretrained("path/to/local/taiga-s1")
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

## Upstream runtime versus this integration

Upstream Taiga-S1 ships a larger runtime:

- it locates a FreeCAD Python interpreter through `FREECAD_PYTHON` and
  `FREECAD_LIB`;
- it starts a FreeCAD worker process (`python -m freecad_s1.runtime.worker`) and
  exchanges line-delimited JSON over stdin/stdout, prefixed with `@@S1 `;
- it snapshots the live document tree, selection, sketch state, and shape;
- it enumerates the currently valid actions;
- it executes commands and fills their task dialogs from the goal;
- for GUI demos, it also uses a local socket server inside FreeCAD.

This project does **not** adopt that runtime. Instead it uses only the verified
library API above, executed in a single isolated child process per request. The
FreeCAD bridge and Taiga runtime remain separate; neither is a persistent
service and neither uses sockets.

## Implemented architecture (Milestone 6)

```
MCP tool (taiga_status / taiga_score_actions)
  -> TaigaBackend (integrations/taiga/adapter.py)
  -> TaigaRuntime (integrations/taiga/runtime.py)
       |  writes request.json, sets FREECAD_MCP_TAIGA_REQUEST / _RESPONSE
       v
     configured Taiga Python + integrations/taiga/runner.py
       |  lazily imports freecad_s1 and torch, writes response.json
       v
     TaigaRuntime -> validated result -> typed MCP result
```

Key properties:

- **One request / one response per invocation.** Temporary files carry both
  envelopes; there are no sockets, HTTP servers, or persistent workers.
- **Explicit interpreter only.** The parent process never falls back to
  `sys.executable`, the project interpreter, or FreeCAD's Python.
- **Parent import isolation.** Importing the MCP server, its tools, or
  `integrations.taiga` never imports `freecad_s1` or `torch`. Those imports live
  only inside `runner.py`, which executes in the child.
- **Strict local-only models.** Before `from_pretrained`, the runner requires a
  directory containing `config.json` (with a `config` object) and
  `model.safetensors`. A Hugging Face repository id is rejected; nothing is
  downloaded.
- **Bounded.** Each operation has a timeout (default 120 s,
  `FREECAD_MCP_TAIGA_TIMEOUT_SECONDS`); the child is killed on expiry.
- **Structured results.** Timeouts, crashes, malformed responses, structured
  child errors, and invalid input all map to `UnavailableReason` values instead
  of raw tracebacks.
- **Scores only.** `taiga_score_actions` ranks supplied candidate actions. It
  never executes a command and never fabricates a plan.

### `integrations/base.py`

- `IntegrationStatus` — available/unavailable state, optional version, and a
  structured `UnavailableReason`.
- `PlanStep` — an explicit, declarative operation plus parameters. It is data,
  not an executed command.
- `PlanningResult` — either a generated, non-empty plan or a structured
  unavailable result; an empty "successful" plan is rejected.
- `PlanningBackend` — a small abstract contract with `status()` and `plan()`.

`UnavailableReason` includes `NOT_INSTALLED`, `MISSING_DEPENDENCY`,
`INCOMPATIBLE`, `INVALID_REQUEST`, `RUNTIME_CONTEXT_REQUIRED`, plus the M6
additions `TIMEOUT` and `RUNTIME_ERROR`.

The module imports only the standard library. It never imports FreeCAD, Torch,
or Taiga.

### `integrations/taiga/runner.py`

Standalone child script with two operations:

- `status` — checks importability of `freecad_s1`, the verified API modules, and
  `torch`/`safetensors`; reports distribution and Python versions; validates a
  configured local model **without loading weights**.
- `score_actions` — validates state, goal, actions, model directory, and device,
  then calls `from_pretrained`, `Policy(model, device=...)`, and
  `Policy.score(state, goal, actions)`. The returned mapping must contain
  exactly the requested actions with finite probabilities in `[0, 1]`.

The runner writes one JSON envelope to a file and sends diagnostics only to
stderr. Its error types are `NotInstalled`, `MissingDependency`, `Incompatible`,
`InvalidRequest`, and `RuntimeError`.

### `integrations/taiga/runtime.py`

Parent-side client. It validates the explicit interpreter and runner paths,
serializes one request, executes `subprocess.run([...])` with the configured
timeout, reads the response, and decodes the envelope. It raises typed errors
for missing configuration, missing files, timeouts, non-zero exits, missing or
invalid responses, malformed envelopes, and structured child failures. Helper
functions map those errors to `UnavailableReason`.

### `integrations/taiga/adapter.py`

`TaigaBackend` preserves the M5 public contract:

1. **`status()` / `status_report()`** — use the isolated runtime. The report adds
   the configured interpreter, Python/Torch versions, and local-model validation
   state. Status never loads weights.
2. **`score_actions(...)`** — validates input in the parent, then delegates to
   the child. Returns typed `ActionScore` values or a structured unavailable
   result. It does **not** execute any command.
3. **`plan(...)`** — returns `UnavailableReason.RUNTIME_CONTEXT_REQUIRED` when
   the runtime is available. It never fabricates a plan or claims that one was
   generated.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `FREECAD_MCP_TAIGA_PYTHON` | unset | Explicit Python interpreter containing `freecad_s1` and its dependencies. No fallback. |
| `FREECAD_MCP_TAIGA_TIMEOUT_SECONDS` | `120` | Per-operation timeout in seconds; must be positive. |
| `FREECAD_MCP_TAIGA_MODEL` | unset | Local model directory containing `config.json` and `model.safetensors`. Required for scoring. |
| `FREECAD_MCP_TAIGA_DEVICE` | `cpu` | Device string passed to Taiga's model loader. |

Taiga and Torch are **not** project dependencies, and `uv sync` does not install
them. Set `FREECAD_MCP_TAIGA_PYTHON` to a separate environment where they are
installed. Model weights must be obtained separately and pointed at with a
local directory; this project never downloads them.

## MCP tools

- **`taiga_status`** — reports runtime availability, installed versions,
  configured interpreter/model, and local-model validation. It never loads
  weights or touches the network. When Taiga is absent it returns
  `available=false` with an actionable reason.
- **`taiga_score_actions`** — accepts JSON `state`, JSON `goal`, and a non-empty
  list of distinct action ids, then returns validated action/probability pairs.
  It requires a configured local model and never executes any action.

Both tools remain usable when Taiga is not installed. Method A is unaffected.

## What is out of scope

The following remain **not implemented**:

- a PartDesign command layer in `freecad_bridge/runner.py`;
- Taiga's worker/RPC client or GUI socket server;
- live FreeCAD state capture in a form Taiga accepts;
- enumerating valid actions from the bridge;
- applying model-produced commands and recomputing;
- end-to-end Method B builds and Method A/Method B comparison;
- converting scores into a fabricated multi-step plan.

M7 (comparison framework) must not assume these exist. A later milestone must
first define the PartDesign execution boundary and verify it against real
FreeCAD.

## Milestone 7 skeleton (`integrations/taiga/workflow.py`)

`run_iterative_modeling` scores actions through `TaigaBackend`, selects the
highest-probability action, and terminates on `Done` before anything is
executed. It is a **guarded skeleton**, not a working Method B pipeline:

- not registered as an MCP tool, so no client can reach it;
- live state comes only from a caller-supplied `state_provider`; without one
  the run returns `runtime_context_required` instead of inventing a state;
- execution comes only from a caller-supplied `executor`; without one the
  selected action is reported and not executed;
- no Taiga action id is translated into a FreeCAD operation (in particular,
  `PartDesign_Pad` is *not* treated as `create_box`);
- fresh state is captured after each successful operation through the same
  provider;
- `success` is `True` only when Taiga selects `Done`; hitting
  `max_iterations` is reported as not-success.

It has never been run against real Taiga-S1 weights or FreeCAD. Its tests use
injected fakes and prove loop mechanics only.

## Verification in this repository

The default test suite needs no Taiga, Torch, model weights, or network:

- `tests/test_taiga_runner.py` executes the real runner against a fake
  `freecad_s1` package, covering status, local model validation, successful
  scoring, malformed upstream results, and missing dependencies.
- `tests/test_taiga_runtime.py` uses fake runner scripts to cover timeouts,
  non-zero exits, missing responses, invalid JSON, malformed envelopes, and
  structured child errors.
- `tests/test_taiga_adapter.py` covers the public contract, score validation,
  error mapping, `plan()` deferral, and fresh-interpreter import isolation with
  `torch` and `freecad_s1` blocked.
- `tests/test_taiga_workflow.py` covers the M7 loop with an injected runtime,
  state provider, and executor: `Done` termination without execution, fresh
  state capture, partial/malformed score envelopes, timeout mapping, and
  iteration caps. It does not exercise real Taiga or FreeCAD.
- `tests/test_server.py` checks tool registration and structured
  unavailable behaviour.
- `tests/test_config.py` covers the Taiga settings and invalid values.

`tests/test_taiga_integration.py` is an **optional real-runtime** test. It skips
unless all of the following hold:

- `FREECAD_MCP_TAIGA_PYTHON` points at an existing interpreter;
- `FREECAD_MCP_TAIGA_MODEL` points at an existing local model directory;
- for the scoring test only, `FREECAD_MCP_TAIGA_TEST_STATE`,
  `FREECAD_MCP_TAIGA_TEST_GOAL`, and `FREECAD_MCP_TAIGA_TEST_ACTIONS` provide
  schema-valid JSON.

It never downloads or installs anything. No test claims real compatibility
unless that opt-in test actually runs and passes.
