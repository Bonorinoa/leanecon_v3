# Sprint 27 Plan: Technical Foundation

**Theme:** Deterministic, efficient, dependency-injected infrastructure.  
**Primary outcome:** LeanEcon becomes cheaper, safer, and easier to develop against.

## Goal

Sprint 27 should address system, infrastructure, API, and performance issues before any new agentic capability is added.

The core goal is:

> Make the existing pipeline deterministic and enforceable.

## Workstreams

## Sprint 27A Core Status

Sprint 27A implements the core foundation slice:

- `StateConfig.allowed_tools` is enforced through one prompt/execution boundary.
  Prompt-visible tool specs are filtered by the current state, and `_execute_tool`
  rejects disallowed tools before budget accounting.
- `StateConfig.max_tool_calls` is mapped onto the existing `BudgetTracker` by
  counting tool calls since the current state was entered. States with `None`
  keep legacy behavior; restricted states fail closed once their state cap is
  spent.
- `StateConfig.allow_decompose` is part of both provider decomposition gating and
  mathlib-native helper-lemma extraction. Caller-level `allow_decomposition`
  must also be true.
- Lean LSP is an explicit optional capability. The default client is still lazy,
  but it exposes status without spawning `uvx`; `NullLeanLSPClient` supports a
  disabled mode via `LEANECON_LEAN_LSP_MODE=disabled`.
- `evals.local_gate --limit 0` defaults to a temp output directory when no
  `--output-dir` is supplied. CI still passes an explicit temp directory.

## Sprint 27B Technical Foundation Status

Sprint 27B keeps the technical-foundation scope narrow and avoids new proving
capability:

- `src/prover/execution.py` now centralizes terminal `ProverResult` assembly in
  a small helper. The proof loop, tactic policy, and final compile validation
  behavior are unchanged.
- API proof jobs use the existing `JOB_MAX_CONCURRENT` setting as an explicit
  in-process concurrency gate. Additional proof requests remain accepted as
  `queued` jobs and enter `running_prover` only when a slot is available.
- `JobStore` TTL cleanup now runs before job creation, job counts, and metrics
  snapshots, so expired jobs do not remain visible until a direct read.
- Retrieval embedding selection is explicit. Set
  `LEANECON_EMBEDDING_BACKEND=hashing` for deterministic, dependency-free test
  or development runs. Semantic embedding startup failures fall back to hashing
  and emit structured `retrieval.embedding_semantic_fallback` logs with the
  fallback reason.

### 1. Make StateConfig Normative

Turn `StateConfig` from descriptive metadata into execution policy.

Deliverables:

- central state-aware tool allowlist enforcement,
- central state-aware tool-spec builder for prompts,
- execution budget enforcement for `max_tool_calls`,
- decomposition gating that honors `allow_decompose`,
- tests proving disallowed tools cannot execute even if a model requests them.

Target result:

- prompt-visible rules and actual execution rules cannot drift.

### 2. Reduce Prover Execution Risk

Refactor `src/prover/execution.py` incrementally, without rewriting behavior.

Suggested extraction order:

1. result/failure assembly,
2. target iteration,
3. direct closure attempts,
4. mathlib-native harness loop,
5. final compile validation.

Target result:

- smaller modules with tests around boundaries,
- no behavior regression,
- easier future changes in Sprint 28.

### 3. Make LSP Dependency Explicit

The Lean LSP client should be an explicit optional capability.

Deliverables:

- null LSP client for unavailable/disabled mode,
- no global default subprocess startup in unit paths,
- health/readiness reports distinguish unavailable, disabled, and ready,
- tests do not require `uvx`.

Target result:

- local development and CI are deterministic.

### 4. Separate CI, Smoke, And Live Benchmark Modes

Make each gate explicit.

Recommended gates:

- **CI:** unit tests, deterministic local-gate scaffold, no live providers.
- **Local smoke:** Lean build, API import, health endpoint, zero/live-model-free proof samples.
- **Live benchmark:** explicit credentials and output directory required.
- **Release benchmark:** frozen claim set, explicit output directory, no accidental baseline mutation.

Target result:

- running a verification command never unexpectedly rewrites release artifacts.

### 5. Improve Cold Start And Retrieval Observability

Deliverables:

- deterministic hashing embedder by default for tests/dev,
- semantic embedder opt-in via env,
- retrieval initialization timing in metrics,
- explicit log/audit event when semantic embeddings fall back to hashing.

Target result:

- performance costs are visible and controllable.

### 6. API And Job Store Hardening

Deliverables:

- bounded job queue behavior documented and tested,
- timeout propagation across planner/formalizer/prover,
- cancellation or stale-job cleanup path,
- no blocking live-provider calls in unbounded API paths,
- health endpoint reflects actual local deployment readiness.

Target result:

- the API is suitable for Sprint 29 local deployment work.

## Sprint 27 Non-Goals

- Do not expand the economics preamble.
- Do not tune prompts for new frontier claims.
- Do not change release benchmark targets.
- Do not redeploy.

## Sprint 27 Exit Gate

- `pytest` passes.
- Fast local Lean gate passes: `cd lean_workspace && lake env lean LeanEcon.lean`.
- Full release Lean gate remains: `cd lean_workspace && lake build LeanEcon` or
  `lake build`, with enough time for full package replay.
- deterministic CI command passes without credentials, network, or `uvx`.
- state-config enforcement has integration tests.
- LSP disabled/unavailable mode is tested.
- ordinary test/smoke commands do not mutate checked-in benchmark baselines.
- docs describe CI, smoke, live benchmark, and release benchmark separately.

## Gate Definitions

- **CI:** Python unit tests plus
  `python -m evals.local_gate --claim-set tier0_smoke --limit 0 --output-dir /tmp/leanecon-local-gate-ci`.
  No live providers, credentials, networked LSP startup, or `uvx` are required by
  the scaffold. Use `LEANECON_EMBEDDING_BACKEND=hashing` when the environment
  must avoid any semantic-embedding model import or download attempt.
- **Fast local Lean gate:** `cd lean_workspace && lake env lean LeanEcon.lean`.
  This checks the root LeanEcon module against the existing cache and is the
  recommended developer gate before broad Python verification.
- **Full release Lean gate:** `cd lean_workspace && lake build LeanEcon` or
  `lake build`. This can replay a large cached Mathlib/LeanEcon graph and may be
  silent for extended periods; use it for release/CI validation, not every local
  edit loop.
- **Live benchmark:** explicit credentials and `--output-dir` required by
  convention. Do not use checked-in baselines as scratch output.
- **Release benchmark:** frozen claim set, explicit output directory, intentional
  history/baseline update only.

## Sprint 26 Lake-Build Caveat

On the current workspace, `lake env lean LeanEcon.lean` completed successfully in
roughly 13 seconds. A bounded `lake build LeanEcon` attempt produced no output
for 60 seconds and was interrupted. Given the 12G `.lake` cache and package-wide
target, this appears to be command/target breadth and cache replay rather than a
Lean root-module failure. Sprint 27A therefore recommends the direct root check
as the fast local gate and keeps full `lake build` as the release gate.

## Codex Goal Prompt Draft

```text
/goal Complete Sprint 27 technical foundation for LeanEcon v3.

Focus only on system, infrastructure, API, CI, and performance improvements. Do not add economics preamble content or new agentic proving capability.

Primary objectives:
1. Make Prover StateConfig normative: enforce allowed_tools, max_tool_calls, and allow_decompose centrally.
2. Add a state-aware tool-spec builder so prompt-visible tools and execution allowlists cannot drift.
3. Make Lean LSP an explicit optional dependency with a null/unavailable client path; no unit test should accidentally spawn uvx or lean-lsp-mcp.
4. Separate deterministic CI/local smoke behavior from live benchmark/provider behavior.
5. Improve retrieval cold-start observability and make semantic embeddings opt-in for tests/dev where appropriate.
6. Add focused tests and update docs.

Constraints:
- Preserve current public APIs unless a small change is necessary and documented.
- Avoid broad prover rewrites; extract only safe boundaries with tests.
- Do not mutate benchmark baseline files except intentionally documented fixtures.
- Keep pytest and lake build green.

Before final response:
- Run focused tests for state machine, prover, LSP, local gate, and retrieval.
- Run full pytest if feasible.
- Report any tests not run.
```
