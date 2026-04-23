# Running Benchmarks

The benchmark runner is `evals/local_gate.py`. Canonical benchmark outputs should be written to `benchmark_baselines/v3_alpha/benchmark_mode/`, with one JSON summary and one live `.progress.jsonl` file per claim set, plus a combined `local_gate.json`.

## Canonical Claim Sets

The benchmark surface is now split cleanly:

- `tier0_smoke`
- `tier1_core_preamble_definable`
- `tier2_frontier_mathlib_native`
- `tier2_frontier_preamble_definable`

Non-canonical claim sets are no longer mixed into the top-level benchmark directory:

- historical mixed sets live under `evals/claim_sets/archive/`
- regression-only utilities live under `evals/claim_sets/regressions/`
- `phd_qual_alpha` remains top-level, but is not part of the standard public benchmark sweep

If you explicitly request an archived or regression set by name, the loader still resolves it.

## Manual Runs

Run a single tier:

```bash
PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --benchmark-mode --claim-set tier0_smoke
PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --benchmark-mode --claim-set tier1_core_preamble_definable
PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --benchmark-mode --claim-set tier2_frontier_mathlib_native
PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --benchmark-mode --claim-set tier2_frontier_preamble_definable
```

Run the standard sweep and then print the aggregate markdown table:

```bash
PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --benchmark-mode \
  --output-dir benchmark_baselines/v3_alpha/benchmark_mode \
  --claim-set tier0_smoke \
  --claim-set tier1_core_preamble_definable \
  --claim-set tier2_frontier_mathlib_native \
  --claim-set tier2_frontier_preamble_definable && \
PYTHONPATH=. ./.venv/bin/python -m evals.aggregate_benchmarks \
  --output-dir benchmark_baselines/v3_alpha/benchmark_mode
```

Useful options:

- `--limit N`: run a smaller sample.
- `--sample-seed 17`: keep sampled runs reproducible.
- `--stratified`: spread a limited run across preamble buckets.
- `--allow-unready`: bypass readiness gating and still emit JSON.
- `--output-dir PATH`: keep benchmark-mode artifacts separate from historical summaries.

Console behavior during long claims:

- a start table is printed per claim set
- a `[claim i/n]` line is printed when each claim begins
- a `[heartbeat i/n]` line is printed every 30 seconds while the claim is still active
- claim completion prints a progress-bar summary line
- live JSONL progress is also written to `<claim_set>.progress.jsonl`

## Reading The Output Files

Each `<claim_set>.json` includes:

- `pass_at_1`, `claims_passed`, `claims_failed`, `claims_total`: top-line success metrics.
- `cost_by_stage` and `cost_by_model`: estimated spend rolled up by stage and model.
- `failure_counts`: counts by normalized failure code.
- `results`: per-claim records with `status`, `failure_code`, `timing_breakdown`, and `usage_by_stage`.

`local_gate.json` is the combined rollup across the claim sets from the same run.

Each `<claim_set>.progress.jsonl` includes stage events such as:

- `planner_started` / `planner_completed` / `planner_failed`
- `formalizer_started` / `formalizer_completed` / `formalizer_failed`
- `prover_started`
- `prover_turn`
- `prover_tool`
- `claim_heartbeat`
- `prover_verified` / `prover_failed`

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

One important readiness blocker seen recently is `planner_endpoint_reachable`. In this repo, that mainly matters when the planner backend is `ollama-cloud`. It can mean either:

- the planner backend is configured as `ollama-cloud` while `LEANECON_OLLAMA_HOST` still points to a local daemon like `http://127.0.0.1:11434`, and the local Ollama server is not reachable
- or, with hosted Ollama, the configured API key/model cannot successfully complete the preflight probe against the selected model

The current benchmark-default planner is Mistral. The recommended `.env` posture is:

```env
LEANECON_PLANNER_BACKEND=mistral-structured
LEANECON_PLANNER_PROVIDER=mistral
LEANECON_PLANNER_MODEL=mistral-large-2512
MISTRAL_API_KEY=...
MISTRAL_BASE_URL=https://api.mistral.ai/v1
LEANECON_LIVE_MODEL_TESTS=true
```

The planner pricing registry includes `mistral-large-2512`, so `/health` and benchmark preflight should agree on planner price coverage for the hosted setup shown above.

If you explicitly want to use hosted Ollama for planner experiments instead of Mistral, the `.env` should use:

```env
LEANECON_PLANNER_BACKEND=ollama-cloud
LEANECON_PLANNER_PROVIDER=ollama
LEANECON_OLLAMA_HOST=https://ollama.com
LEANECON_PLANNER_MODEL=gemma4:31b-cloud
OLLAMA_API_KEY=...
LEANECON_LIVE_MODEL_TESTS=true
```

That last flag makes preflight use a real hosted `/api/chat` probe, which catches broken auth or missing model access before a multi-hour benchmark starts. Ollama is still useful as an experimental planner backend, but it is no longer the benchmark default.

## Aggregating The Latest Results

After any run, print a markdown summary of the standard tiers:

```bash
PYTHONPATH=. ./.venv/bin/python -m evals.aggregate_benchmarks \
  --output-dir benchmark_baselines/v3_alpha/benchmark_mode
```

Optional filters:

```bash
PYTHONPATH=. ./.venv/bin/python -m evals.aggregate_benchmarks \
  --claim-set tier1_core_preamble_definable \
  --output-dir benchmark_baselines/v3_alpha/benchmark_mode
PYTHONPATH=. ./.venv/bin/python -m evals.aggregate_benchmarks --output-dir /tmp/benchmarks
```

The aggregation report shows:

- pass@1 by tier and overall
- estimated cost by tier
- average latency by stage
- failure breakdown across the loaded summaries

## Targeted API Checks

Three targeted API checks were run against the local FastAPI server while debugging the planner path:

- `t1_bellman_rhs_monotone_discount`
- `t1_expected_payoff_pure_strategy_11`
- `t2_monotone_sequence_converges`

Observed results:

- The packet-handoff bug was fixed in code: tests now cover hydrated `/plan -> /formalize -> /prove` reuse, and that regression suite is green.
- The hosted Ollama planner path remained too unstable for benchmark use. A clean tier-0 rerun produced planner timeouts before formalization/proving, so the benchmark-default planner path was switched to `mistral-structured`.
- The SSE endpoint stays connected, but meaningful intermediate prover progress over the public API still needs improvement. The benchmark harness progress log is richer than the current API event stream.

This means the benchmark harness is currently more robust than the public API for benchmark-style end-to-end runs. Before treating the API as benchmark-ready, the following should be fixed:

- make `/formalize` accept sanitized planner packets rather than hydrated API job payloads
- ensure `job_events` subscribers receive meaningful stage and prover progress events during active jobs, not just terminal updates
- move long-running proof work off the main event loop so polling and SSE remain responsive during active proving

API nuance: `POST /plan` and `POST /formalize` return HTTP `200` even when the stage itself fails, because the failure is represented in the returned job payload (`status: failed`, `error: ...`). When debugging the live API from the terminal, inspect the JSON body status rather than relying on HTTP status alone.
