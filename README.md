# Lean Econ v3

LeanEcon v3 is a Lean 4 verification harness for economic-theory claims. It
takes natural-language economics statements through a Planner -> Formalizer ->
Prover pipeline, then treats the Lean kernel as the only authority for proof
success.

The repository is deliberately split between a thin Python orchestration layer
and a versioned Lean workspace:

- `lean_workspace/`: the LeanEcon preamble, theorem stubs, Lake package, and
  pinned Mathlib dependency.
- `src/`: FastAPI app, planner/formalizer/prover services, guardrails,
  retrieval, budget profiles, observability, and provider adapters.
- `evals/claim_sets/`: canonical release and frontier benchmark inputs.
- `benchmark_baselines/v3_alpha/`: checked-in benchmark artifacts preserved for
  audit history, not as a live hosted readiness claim.

The prover is claim-type-aware. Preamble-definable claims use LeanEcon metadata
and direct closure where possible. Mathlib-native claims enter
`mathlib_native_mode` and use bounded `lean-lsp-mcp` goal inspection,
diagnostics, code actions, LeanSearch/Loogle/local search, deterministic
fallback candidates, and structured degradation when an external search service
fails.

Current public posture: **Tier 1 reliable, Tier 2 beta/diagnostic**.
`tier1_core_preamble_definable` is the release-reliable denominator.
`tier2_frontier_preamble_definable`, `tier2_frontier_mathlib_native`, and
regression/frontier batches are useful for diagnostics and data collection, but
they are not release-reliability denominators.

The current release-profile provider defaults are:

- Planner: `mistral-structured` with `mistral-large-2512`
- Formalizer: `leanstral` via Mistral, `labs-leanstral-2603`
- Prover: `leanstral` via Mistral, `labs-leanstral-2603`, with bounded Lean
  REPL and `lean-lsp-mcp` tool use

## Quick Start

```bash
python -m pip install -e ".[dev]"
cd lean_workspace && lake build Mathlib LeanEcon
cd ..
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

For mathlib-native proving, make sure `lean-lsp-mcp` is available either in
`.venv/bin/lean-lsp-mcp` or through `uvx lean-lsp-mcp`. The client runs it over
stdio with the Lean workspace path. The release denominator is
preamble-definable, so missing LSP tooling should be visible in health/metrics
without being presented as a Tier 1 release failure.

## Repo Highlights

- `docs/CHARTER_v3.md`: historical mission, assets, liabilities, and original alpha targets.
- `docs/ARCHITECTURE_v3.md`: authoritative v3 runtime architecture.
- `docs/LeanEcon Engineering Log.md`: sprint-by-sprint implementation history and audit ledger.
- `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`: minimum deployment bar before any Railway readiness claim.
- `docs/DECISION_SPRINT_31_PROVIDER_STRATEGY.md`: provider policy and release guardrail.
- `docs/FRONTIER_FLYWHEEL.md`: Tier 2 beta failure taxonomy and data flywheel.
- `docs/PROVER_STATE_MACHINE.md`: mathlib-native prover execution policy.
- `skills/*.md`: runtime process knowledge for HILBERT, APOLLO, guardrails, and the preamble model.
- `evals/claim_sets/`: canonical benchmark inputs plus HIL evaluation material. Historical mixed sets live under `evals/claim_sets/archive/`, and regression/diagnostic utilities live under `evals/claim_sets/regressions/`.
- `evals/benchmark_manifest.json`: claim-set bucket mix, theorem-stub usage, and integrity status.

## Benchmark Note

`python -m evals.local_gate` defaults to `tier0_smoke`,
`tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and
`tier2_frontier_preamble_definable`. Use `--benchmark-mode` to force the full
Planner -> Formalizer -> Prover path with benchmark guardrails, periodic
heartbeat lines during long claims, explicit `verified_via` tagging, and
claim-type-aware trace metadata.

For integrity-sensitive work, prefer the canonical split sets `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. The old mixed sets are archived under `evals/claim_sets/archive/` and are no longer part of the canonical benchmark surface.

The benchmark flywheel records average tool calls, LSP tool calls, native search attempts, `mathlib_native_mode` usage, direct-close rate, decomposition depth, latency, cost, failure classes, claim-type mix, retrieval-side metrics (`retrieval_hit_rate@5`, `leansearch_hit_rate@5`, `enriched_leansearch_hit_rate`, `second_retrieval_rate`, `hybrid_retrieval_latency_ms`), and synthesis-side metrics (`synthesis_efficiency`, `premise_match_rate@3`, `avg_decomposition_depth_mathlib`) in `benchmark_baselines/v3_alpha/benchmark_history.jsonl`.

Current checked-in benchmark history is audit evidence, not a live-provider
readiness claim. The latest engineering log records deterministic CI/Lake
stabilization, Docker proof, and approved live-provider diagnostics: Tier 1
release profile at 24/24, Tier 2 preamble focused sample at 6/9, and Tier 2
mathlib-native at 3/3. Before any public hosted claim, rerun the release
denominator and hosted smoke with explicit provider approval.

## Deployment Path

Deployment readiness is defined by deterministic Python/Lean gates,
release-image readiness, `/health`, `/metrics`, configured Mistral/Leanstral
credentials, SQLite job storage, runtime visibility into `lean-lsp-mcp`, and a
separate live hosted smoke. Railway deployment should follow
`docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`; GHCR Lean base image publishing,
deployment-environment image rebuild, and live hosted smoke remain operational
steps.
