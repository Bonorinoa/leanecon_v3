# Lean Econ v3

Lean Econ v3 is a clean-slate thin harness for research-grade economic-theory formalization and verification in Lean 4.

The preserved moat is `lean_workspace/` plus the REPL/guardrail/observability spine ported from v2. The prover is claim-type-aware: preamble-definable claims use LeanEcon metadata and direct closure (zero tool calls), while mathlib-native claims enter `mathlib_native_mode` and use `lean-lsp-mcp` goal, diagnostic, code-action, hover, LeanSearch, and Loogle tooling.

Mathlib-native goals run through a **harness-owned hybrid retrieval pipeline** (Sprints 21–23): a local RAG over a ~1480-entry seed of Mathlib `Topology / Order / Analysis / FixedPoints` premises (semantic embedding via `sentence-transformers/all-MiniLM-L6-v2`, with a hashing fallback) plus first-class `lean_leansearch` results enriched with full type signatures and docstrings via cached `lean_file_outline` / `lean_hover_info`. Stalled turns trigger a refined second-pass query; hallucinated identifiers (`unknown identifier`) trigger a rescue retrieval keyed on the parsed concept tokens. Every retrieval, every failure, and every recovery attempt is observable in the per-run JSONL trace (Sprint 24).

Repository evidence is authoritative. Historical benchmark JSON under `benchmark_baselines/v3_alpha/` is preserved for audit history and should not be treated as public release truth.

The current hosted benchmark-default stack is:
- Planner: `mistral-structured` with `mistral-large-2512`
- Formalizer: `leanstral` via Mistral
- Prover: `leanstral` via Mistral, with bounded Lean REPL + `lean-lsp-mcp` tool use

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
- `docs/LeanEcon Engineering Log.md`: sprint-by-sprint implementation history through Session 24 (hybrid retrieval, synthesis lift, observable failure paths).
- `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`: minimum deployment bar before any Railway readiness claim.
- `skills/*.md`: runtime process knowledge for HILBERT, APOLLO, guardrails, and the preamble model.
- `evals/claim_sets/`: canonical benchmark inputs plus the v3 alpha PhD-qual additions. Historical mixed sets live under `evals/claim_sets/archive/`, and regression-only utilities live under `evals/claim_sets/regressions/`.
- `evals/benchmark_manifest.json`: claim-set bucket mix, theorem-stub usage, and integrity status.

## Benchmark Note

`python -m evals.local_gate` now defaults to `tier0_smoke`, `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. Use `--benchmark-mode` to force the full Planner -> Formalizer -> Prover path with benchmark guardrails, periodic heartbeat lines during long claims, explicit `verified_via` tagging, and claim-type-aware trace metadata.

For integrity-sensitive work, prefer the canonical split sets `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. The old mixed sets are archived under `evals/claim_sets/archive/` and are no longer part of the canonical benchmark surface.

The benchmark flywheel records average tool calls, LSP tool calls, native search attempts, `mathlib_native_mode` usage, direct-close rate, decomposition depth, latency, cost, failure classes, claim-type mix, plus retrieval-side metrics (`retrieval_hit_rate@5`, `leansearch_hit_rate@5`, `enriched_leansearch_hit_rate`, `second_retrieval_rate`, `hybrid_retrieval_latency_ms`) in `benchmark_baselines/v3_alpha/benchmark_history.jsonl`.

**Current state (post-Sprint-24)**: 86.5% benchmark-mode pass rate (32/37) on the canonical claim sets; `tier1_core_preamble_definable` at 24/24 with 0 tool calls; `tier2_frontier_mathlib_native` stuck at **1/3 across Sprints 20–24** despite a fully-instrumented hybrid retrieval pipeline. The remaining failures isolate a **synthesis bottleneck**: the model receives correct, enriched, well-organised premises and still cannot assemble the proofs. The Sprint 25 work plan attacks this from the prover side (model selection + prompting strategy) rather than from the harness side.

## Deployment Path

Deployment readiness is currently defined by a clean `lake build`, focused Python tests, benchmark-mode local-gate, `/health`, `/metrics`, configured Mistral/Leanstral credentials, SQLite job storage, and runtime access to `lean-lsp-mcp`. Railway deployment should follow `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md` and the updated architecture doc.
