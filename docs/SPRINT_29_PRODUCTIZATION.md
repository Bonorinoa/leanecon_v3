# Sprint 29 Plan: Productization And Benchmark Freeze

**Theme:** Freeze release expectations and prepare local deployment.  
**Primary outcome:** LeanEcon v3 can be demonstrated from a clean checkout with honest pass-rate reporting.

## Goal

Sprint 29 should transform the improved development system into a productizable alpha.

The core goal is:

> Define the release surface and make it reproducible.

## Workstreams

### 1. Freeze The Undergraduate Release Benchmark

Create a release benchmark claim set focused on advanced undergraduate mathematical economics.

Suggested categories:

- preferences and utility representation wrappers,
- constrained optimization and maximum certificates,
- compactness/existence wrappers,
- monotone convergence bridge claims,
- contraction/fixed-point/value-function claims,
- best response and simple Nash certificate claims,
- Walras law and simple equilibrium certificate claims,
- elementary dynamic programming claims.

Target result:

- a stable release benchmark that should reach 95%+ on release-reliable claims.

### 2. Create Demo-Quality Examples

Pick 20-30 representative examples with complete artifacts:

- raw claim,
- scope classification,
- planner output,
- formalization,
- verified Lean proof,
- human-readable explanation,
- trace summary.

Target result:

- the system can be shown without relying on cherry-picked hidden runs.

### 3. Local Deployment Path

Before Railway, make local deployment boring.

Deliverables:

- Docker/local run instructions,
- health and metrics smoke script,
- sample API request script,
- documented env variables,
- deterministic demo mode if no live provider credentials are present.

Target result:

- a clean checkout can run a credible local alpha.

### 4. Benchmark Artifact Discipline

Separate:

- historical baselines,
- development runs,
- release baselines,
- frontier queues.

Target result:

- benchmark artifacts are intentional and interpretable.

### 5. Documentation Pruning

Update public docs so they do not overclaim.

Docs should clearly state:

- what LeanEcon v3 solves reliably,
- what it attempts experimentally,
- what it currently does not support,
- how to run local demo,
- how to run release benchmark,
- how to interpret frontier data.

## Sprint 29 Non-Goals

- Do not add large new theorem surfaces unless required by release examples.
- Do not deploy before local release gates pass.
- Do not use frontier claims to dilute release pass rates.

## Sprint 29 Exit Gate

- release benchmark claim set exists and is documented,
- release benchmark separates reliable/frontier/out-of-scope metrics,
- local deployment works from clean checkout,
- demo examples are reproducible,
- docs match actual commands,
- no hidden provider assumptions in deterministic demo mode.

## Sprint 29 Implementation Status

Sprint 29 freezes the alpha benchmark surface without broadening the economics
preamble or adding new agentic capabilities.

### Frozen Alpha Benchmark Surface

The alpha release-reliable denominator is:

- `tier1_core_preamble_definable`

The following standard claim sets remain useful but are excluded from
`release_reliable_metrics`:

- `tier2_frontier_mathlib_native`
- `tier2_frontier_preamble_definable`

`tier0_smoke` remains a smoke/scaffold gate. It is not the release benchmark
denominator. Historical and utility sets under archive/regression paths remain
noncanonical unless a future sprint explicitly promotes them.

`release_reliable` claims must be preamble-definable, backed by the frozen Sprint
29 preamble surface, and carry an authoritative theorem stub in the benchmark
manifest. Sprint 29 does not use an unwired template-availability flag for the
release denominator.
Preamble-backed claims outside that surface are `supported_attempt`. Mathlib
native claims and missing-surface claims are `frontier_collect` unless they name
a broad theorem family currently marked `out_of_scope`.

### Honest Capability Claims

LeanEcon v3 can currently claim reliable alpha behavior only for narrow,
preamble-backed undergraduate economics wrappers represented in
`tier1_core_preamble_definable`, including:

- constrained maximum feasibility/value certificates,
- Bellman monotonicity and value-function fixed-point wrappers,
- contraction/fixed-point wrappers,
- continuity/convexity preference wrappers,
- Kuhn-Tucker slackness certificates,
- simple Nash witness existence,
- policy-improvement reflexivity,
- topological/measure primitive wrappers.

LeanEcon v3 attempts, but does not claim release reliability for, frontier
preamble-backed paraphrases and mathlib-native synthesis claims. Those claims are
kept in reports and frontier queues so Sprint 30+ can inspect gaps without
diluting the alpha reliability denominator.

LeanEcon v3 does not currently claim broad Arrow-Debreu, Kakutani, welfare
theorem, Roy's identity, envelope theorem, or comparative-statics support.

### Artifact Discipline

Local-gate summaries now expose:

- `artifact_schema_version`,
- `claim_scope_counts`,
- `metrics_by_scope`,
- `release_reliable_metrics`,
- `frontier_metrics`,
- `progress_log_path`,
- `frontier_queue_path`.

Frontier queue JSONL records include `schema_version`, claim identity, raw claim,
scope reason, claim type, selected preamble entries, required primitives, theorem
shape recommendation, assumption audit, Lean statement/parse status when
available, proof result, failure class, recommended next action, and failure
reason.

`--limit 0` scaffold mode defaults to:

```bash
./.venv/bin/python -m evals.local_gate --claim-set tier0_smoke --limit 0
```

which writes under the system temp directory, not checked-in benchmark baselines.
Use `--output-dir <path>` for any intentional local artifact location.

### Local Alpha Path

From a clean checkout, the deterministic product path is:

```bash
LEANECON_EMBEDDING_BACKEND=hashing ./.venv/bin/python -m pytest tests/test_claim_scope.py tests/test_local_gate.py tests/test_metrics_aggregator.py tests/test_aggregate_benchmarks.py
cd lean_workspace && lake env lean LeanEcon.lean
./.venv/bin/python -m evals.local_gate --benchmark-mode --claim-set tier1_core_preamble_definable --limit 0 --output-dir /tmp/leanecon-sprint29-scaffold
./.venv/bin/python -m evals.aggregate_benchmarks --output-dir benchmark_baselines/v3_alpha/benchmark_mode
```

For an actual benchmark run, replace `--limit 0` with a selected nonzero sample
or a full run and use an explicit scratch output directory. Do not write into
`benchmark_baselines/v3_alpha/benchmark_mode` unless intentionally refreshing a
checked-in baseline.

API demo readiness remains local-only for Sprint 29. Hosted redeploy and Railway
changes are deferred to Sprint 30.

## Codex Goal Prompt Draft

```text
/goal Complete Sprint 29 productization and benchmark freeze for LeanEcon v3.

Assume Sprints 27 and 28 are complete. Focus on release benchmark definition, reproducible local deployment, demo artifacts, and documentation honesty.

Primary objectives:
1. Create/freeze an advanced-undergraduate release benchmark claim set with explicit scope tiers.
2. Create reproducible demo examples with raw claim, scope classification, planner/formalizer/prover artifacts, and trace summary.
3. Harden local deployment instructions and scripts before hosted deployment.
4. Separate historical, development, release, and frontier benchmark artifacts.
5. Update docs to state reliable capabilities, frontier capabilities, and out-of-scope limitations.

Constraints:
- Do not redeploy yet.
- Do not count frontier claims against release-reliable pass rate.
- Avoid large preamble expansion unless directly required by frozen release examples.

Before final response:
- Run deterministic test suite.
- Run release benchmark or a documented representative subset if full run is too expensive.
- Run local deployment smoke checks if feasible.
- Report any tests or deployment checks not run.
```
