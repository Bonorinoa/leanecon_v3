# Running Benchmarks

The benchmark runner is `evals/local_gate.py`. It writes one JSON summary per claim set into `benchmark_baselines/v3_alpha/` and a combined `local_gate.json`.

## Manual Runs

Run a single tier:

```bash
python -m evals.local_gate --benchmark-mode --claim-set tier0_smoke
python -m evals.local_gate --benchmark-mode --claim-set tier1_core
python -m evals.local_gate --benchmark-mode --claim-set tier2_frontier
```

Run the standard sweep:

```bash
python -m evals.local_gate --benchmark-mode \
  --claim-set tier0_smoke \
  --claim-set tier1_core \
  --claim-set tier2_frontier
```

Useful options:

- `--limit N`: run a smaller sample.
- `--sample-seed 17`: keep sampled runs reproducible.
- `--stratified`: spread a limited run across preamble buckets.
- `--allow-unready`: bypass readiness gating and still emit JSON.

## Reading The Output Files

Each `<claim_set>.json` includes:

- `pass_at_1`, `claims_passed`, `claims_failed`, `claims_total`: top-line success metrics.
- `cost_by_stage` and `cost_by_model`: estimated spend rolled up by stage and model.
- `failure_counts`: counts by normalized failure code.
- `results`: per-claim records with `status`, `failure_code`, `timing_breakdown`, and `usage_by_stage`.

`local_gate.json` is the combined rollup across the claim sets from the same run.

## Common Failure Codes

- `schema_invalid`: a stage returned output that failed the expected schema.
- `repl_compile_disagreement`: the prover produced code that disagreed between validation paths.
- `max_turns_exhausted`: the prover used its turn budget without reaching a verified theorem.
- `timeout`: a provider call or tool step timed out.
- `provider_unavailable`: the remote backend could not be reached.
- `auth`: credentials were missing or rejected.
- `lsp_unavailable`: Lean LSP support was unavailable during the run.
- `unknown`: uncategorized exception; inspect the per-claim result for context.

If readiness gating blocks execution, the `failure_counts` field can also contain readiness blocker names such as `planner_provider_configured` or `prover_price_known`.

## Aggregating The Latest Results

After any run, print a markdown summary of the standard tiers:

```bash
python -m evals.aggregate_benchmarks
```

Optional filters:

```bash
python -m evals.aggregate_benchmarks --claim-set tier1_core
python -m evals.aggregate_benchmarks --output-dir /tmp/benchmarks
```

The aggregation report shows:

- pass@1 by tier and overall
- estimated cost by tier
- average latency by stage
- failure breakdown across the loaded summaries
