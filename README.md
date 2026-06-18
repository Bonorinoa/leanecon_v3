# Lean Econ v3

Lean Econ v3 is a clean-slate thin harness for research-grade economic-theory formalization and verification in Lean 4.

The preserved moat is `lean_workspace/` plus the REPL/guardrail/observability spine ported from v2. The prover is claim-type-aware: preamble-definable claims use LeanEcon metadata and direct closure (zero tool calls), while mathlib-native claims enter `mathlib_native_mode` and use `lean-lsp-mcp` goal, diagnostic, code-action, hover, LeanSearch, and Loogle tooling.

Mathlib-native goals run through a **harness-owned hybrid retrieval pipeline**: local Mathlib premise retrieval, first-class `lean-lsp-mcp` search/inspection, enriched premise metadata, deterministic fallback candidates, and observable degradation when external LeanSearch is unavailable. Every retrieval, failure, recovery attempt, compile candidate, and terminal status is available in per-run JSONL traces.

Repository evidence is authoritative. Historical benchmark JSON under `benchmark_baselines/v3_alpha/` is preserved for audit history and should not be treated as public release truth.

The current hosted benchmark-default stack is:
- Planner: `mistral-structured` with `mistral-large-2512`
- Formalizer: `leanstral` via Mistral
- Prover: `leanstral` via Mistral, with bounded Lean REPL + `lean-lsp-mcp` tool use

Public MVP posture: **Tier 1 reliable, Tier 2 public beta**. `tier1_core_preamble_definable` is the reliability base. `tier2_frontier_preamble_definable`, `tier2_frontier_mathlib_native`, and regression/frontier batches are beta/diagnostic surfaces with bounded budgets, failure classes, and traces; they are not release-reliability denominators.

## Quick Start

```bash
python -m pip install -e ".[dev]"
cd lean_workspace && lake build
cd ..
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

For mathlib-native proving, make sure `lean-lsp-mcp` is available either in `.venv/bin/lean-lsp-mcp` or through `uvx lean-lsp-mcp`. The client runs it over stdio with the Lean workspace path.

## Repo Highlights

- `docs/CHARTER_v3.md`: mission, assets, liabilities, and alpha success metrics.
- `docs/ARCHITECTURE_v3.md`: authoritative v3 runtime architecture.
- `docs/LeanEcon Engineering Log.md`: sprint-by-sprint implementation history and audit ledger.
- `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`: minimum deployment bar before any Railway readiness claim.
- `docs/DECISION_SPRINT_31_PROVIDER_STRATEGY.md`: provider policy and release guardrail.
- `docs/FRONTIER_FLYWHEEL.md`: Tier 2 beta failure taxonomy and data flywheel.
- `docs/PROVER_STATE_MACHINE.md`: mathlib-native prover execution policy.
- `skills/*.md`: runtime process knowledge for HILBERT, APOLLO, guardrails, and the preamble model.
- `evals/claim_sets/`: canonical benchmark inputs plus the v3 alpha PhD-qual additions. Historical mixed sets live under `evals/claim_sets/archive/`, and regression-only utilities live under `evals/claim_sets/regressions/`.
- `evals/benchmark_manifest.json`: claim-set bucket mix, theorem-stub usage, and integrity status.

## Benchmark Note

`python -m evals.local_gate` now defaults to `tier0_smoke`, `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. Use `--benchmark-mode` to force the full Planner -> Formalizer -> Prover path with benchmark guardrails, periodic heartbeat lines during long claims, explicit `verified_via` tagging, and claim-type-aware trace metadata.

For integrity-sensitive work, prefer the canonical split sets `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. The old mixed sets are archived under `evals/claim_sets/archive/` and are no longer part of the canonical benchmark surface.

The benchmark flywheel records average tool calls, LSP tool calls, native search attempts, `mathlib_native_mode` usage, direct-close rate, decomposition depth, latency, cost, failure classes, claim-type mix, retrieval-side metrics (`retrieval_hit_rate@5`, `leansearch_hit_rate@5`, `enriched_leansearch_hit_rate`, `second_retrieval_rate`, `hybrid_retrieval_latency_ms`), and synthesis-side metrics (`synthesis_efficiency`, `premise_match_rate@3`, `avg_decomposition_depth_mathlib`) in `benchmark_baselines/v3_alpha/benchmark_history.jsonl`.

Current checked-in benchmark history is audit evidence, not a live-provider readiness claim. The latest engineering log records major LSP/MCP, LeanSearch degradation, Lake hygiene, and preamble-template routing fixes that moved focused Tier 2 regression slices to green. Before any public hosted claim, rerun the release denominator and hosted smoke with explicit provider approval.

## Deployment Path

Deployment readiness is currently defined by deterministic Python/Lean gates, release-image readiness, `/health`, `/metrics`, configured Mistral/Leanstral credentials, SQLite job storage, runtime access to `lean-lsp-mcp`, and a separate live hosted smoke. Railway deployment should follow `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`; GHCR Lean base image publishing and live provider smoke remain operational deployment steps.
