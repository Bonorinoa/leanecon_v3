# Railway Hobby Deployment Checklist

## Minimum Bar Before Any Readiness Claim
1. `PYTHONPATH=. pytest -q -o addopts=''` passes on the deployment branch.
2. `GET /health` reports Lean availability, backend capability metadata, and no missing required secrets.
3. `GET /metrics` returns integrity metrics, benchmark category mix, and backend status without schema errors.
4. `GET /jobs/{job_id}/events` emits ordered SSE events:
   `job.update`, stage start/completion or failure, prover turn/tool events, and terminal status.
5. `POST /jobs/{job_id}/review` is exercised for approve and reject transitions.
6. SQLite-backed jobs survive planner, formalizer, and prover lifecycles plus concurrent polling.
7. Planner, formalizer, prover, and final compile timeouts are exercised explicitly.
8. Benchmark artifacts are written to separate directories for `live_pipeline` and `benchmark_mode`.
9. Historical artifacts under `benchmark_baselines/v3_alpha/` are not presented as release truth.
10. No public score or production-readiness statement is made until all items above are satisfied.
