"""Benchmark harness for Lean Econ v3.

Entry point: `evals.local_gate` (also exposed as the `leanecon-local-gate`
console script). It runs the full planner → formalizer → prover pipeline
against curated claim sets and produces both human-readable summaries and
machine-readable JSON outputs.

Outputs land in `benchmark_baselines/v3_alpha/<mode>/<claim_set>.json`
alongside `<claim_set>.progress.jsonl` observability traces. Modes:
`benchmark_mode/` (canonical, committed) and `live_pipeline/` (staging).
Claim sets currently include `tier0_smoke`, `tier1_core_preamble_definable`,
`tier2_frontier_preamble_definable`, `tier2_frontier_mathlib_native`, and
the combined `local_gate`.
"""
