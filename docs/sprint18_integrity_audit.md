# Sprint 18 Integrity Audit

- Scope: searched `src/`, `evals/`, `tests/`, and benchmark claim files for explicit handling keyed to the current failing `tier2_frontier` claim IDs or theorem names.
- Finding: no explicit claim-ID or theorem-name branching was present for the failing frontier claims.
- Finding: planner `schema_invalid` repair in `src/planner/planner.py` is generic. It normalizes raw provider JSON and falls back through theorem-stub alignment; it does not branch on claim content.
- Integrity risk removed: the prover previously carried a hardcoded preamble-family direct-closure map in `src/prover/prover.py` with proof bodies such as `exact BellmanOperator.monotone hβ hvw` and `exact policyImproves_refl criterion policy`.
- Sprint 18 change: those hardcoded prover shortcuts were removed. Direct closure is now metadata-driven only: preamble metadata hints, proven lemmas, theorem-context hypothesis reuse, and compile-validated fallback tactics.
- Artifact cleanup: untracked `lean_workspace/LeanEcon/local_gate_*` files were generated leftovers from prior benchmark runs, not active fixtures. They were deleted; `.gitignore` already excludes them.
- Integrity posture after cleanup: remaining prover shortcuts are general compile-validated heuristics grounded in preamble metadata or theorem context, not benchmark-claim special cases.
