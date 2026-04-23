# Lean Econ v3

Lean Econ v3 is a clean-slate thin harness for research-grade economic-theory formalization and verification in Lean 4.

The preserved moat is `lean_workspace/` plus the REPL/guardrail/observability spine ported from v2. The new harness layers are `src/api`, `src/planner`, `src/formalizer`, `src/prover`, `src/guardrails`, `src/memory`, `src/observability`, `src/tools`, and `src/skills.py`.

Repository evidence is authoritative. Historical benchmark JSON under `benchmark_baselines/v3_alpha/` is preserved for audit history and should not be treated as public release truth.

## Quick Start

```bash
python -m pip install -e ".[dev]"
cd lean_workspace && lake build
cd ..
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

## Repo Highlights

- `docs/CHARTER_v3.md`: mission, assets, liabilities, and alpha success metrics.
- `docs/ARCHITECTURE_v3.md`: authoritative v3 runtime architecture.
- `docs/sprint18_integrity_audit.md`: repo-grounded integrity audit and remediation summary.
- `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`: minimum deployment bar before any Railway readiness claim.
- `docs/MIGRATION_PLAN.md`: living migration/bootstrap record.
- `skills/*.md`: runtime process knowledge for HILBERT, APOLLO, guardrails, and the preamble model.
- `evals/claim_sets/`: canonical benchmark inputs plus the v3 alpha PhD-qual additions.
- `evals/benchmark_manifest.json`: claim-set bucket mix, theorem-stub usage, and integrity status.

## Benchmark Note

`python -m evals.local_gate` now defaults to `tier0_smoke`, `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. Use `--benchmark-mode` to force the full Planner -> Formalizer -> Prover path with benchmark guardrails, periodic heartbeat lines during long claims, and explicit `verified_via` tagging.

For integrity-sensitive work, prefer the canonical split sets `tier1_core_preamble_definable`, `tier2_frontier_mathlib_native`, and `tier2_frontier_preamble_definable`. The old `tier1_core` and `tier2_frontier` files are retained only as historical mixed references.
