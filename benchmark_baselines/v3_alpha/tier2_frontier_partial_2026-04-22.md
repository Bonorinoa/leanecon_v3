# tier2_frontier Partial Run - 2026-04-22

This run was intentionally stopped after the prover showed clear topic drift and loop behavior.

Run command actually used:

```bash
./.venv/bin/python -u -m evals.local_gate --claim-set tier2_frontier
```

Notes:
- The user-requested `python3 -m evals.local_gate --claim-set tier2_frontier` could not be used directly because the system `python3` lacked `python-dotenv`.
- The run executed in `live` mode because `--benchmark-mode` was not passed.
- Readiness passed before execution started.
- No `provider_unavailable` failure was observed before interruption.
- Terminal/log output was claim-level only; stage events were not streamed by `evals/local_gate.py`.

Completed claims before termination: 7 of 13

Partial result summary:
- Partial pass@1: 42.9% (3/7)
- Passed: 3
- Failed: 4
- Failure breakdown so far:
  - `max_turns_exhausted`: 2
  - `schema_invalid`: 2

Observed claim outcomes:
- `t2_contraction_mapping_fixed_point`: failed, `max_turns_exhausted`, 814.0s
- `t2_extreme_value_repair`: failed, `schema_invalid`, 5.6s
- `t2_monotone_sequence_converges`: failed, `max_turns_exhausted`, 694.4s
- `t2_pareto_dominance_transitive`: failed, `schema_invalid`, 4.7s
- `t2_utilitarian_swf_pareto_monotone`: verified, `trivial_shortcut`, 47.1s
- `t2_bellman_monotone_value_function`: verified, `trivial_shortcut`, 36.2s
- `t2_expected_payoff_convex_mixture`: verified, `trivial_shortcut`, 27.8s

The raw interrupted console log is preserved alongside this report at:

`benchmark_baselines/v3_alpha/tier2_frontier_partial_2026-04-22.log`
