# Railway Hobby Deployment Checklist

Sprint 34 deployment work must wait until the local release-candidate audit in
`docs/SPRINT_30_LOCAL_RC_AUDIT.md` is green. The broader Sprint 30-35 execution
plan is `docs/SPRINTS_30_35_MASTER_PLAN.md`.

## Minimum Bar Before Any Readiness Claim
1. `PYTHONPATH=. pytest -q -o addopts=''` passes on the deployment branch.
2. `GET /health` reports Lean availability, backend capability metadata, and no missing required secrets.
3. `GET /health` reports `budget_profile.active = release` and a release-compliant provider guardrail.
4. `GET /metrics` returns integrity metrics, benchmark category mix, backend status, active budget profile, provider guardrail, cost by stage/model/claim type/claim scope, token usage source, latency by stage, and budget exhaustion counters without schema errors.
5. `GET /jobs/{job_id}/events` emits ordered SSE events:
   `job.update`, stage start/completion or failure, prover turn/tool events, and terminal status.
6. `POST /jobs/{job_id}/review` is exercised for approve and reject transitions.
7. SQLite-backed jobs survive planner, formalizer, and prover lifecycles plus concurrent polling.
8. Planner, formalizer, prover, and final compile timeouts are exercised explicitly.
9. Benchmark artifacts are written to separate directories for `live_pipeline` and `benchmark_mode`.
10. Historical artifacts under `benchmark_baselines/v3_alpha/` are not presented as release truth.
11. No public score or production-readiness statement is made until all items above are satisfied.

## Required Production Budget Posture

Hosted alpha production must set or inherit:

```env
LEANECON_BUDGET_PROFILE=release
LEANECON_PLANNER_BACKEND=mistral-structured
LEANECON_PLANNER_PROVIDER=mistral
LEANECON_PLANNER_MODEL=mistral-large-2512
LEANECON_FORMALIZER_BACKEND=leanstral
LEANECON_FORMALIZER_MODEL=labs-leanstral-2603
LEANECON_PROVER_BACKEND=leanstral
LEANECON_PROVER_FALLBACK_BACKEND=leanstral
```

Do not deploy hosted alpha with `frontier` or `research`. Non-Mistral provider
paths and cheap fallback exploration are non-release diagnostics only.

## Sprint 30-35 Gate Separation

- Developer edit-loop gate: focused Python tests plus
  `cd lean_workspace && lake env lean LeanEcon.lean`.
- Local release-candidate gate: deterministic Python suite, fast Lean root
  check, and `tier1_core_preamble_definable` local gate.
- Release-image gate: full Lake build and cache/image readiness.
- Hosted deployment gate: `/health`, `/metrics`, bounded jobs, SSE, and one
  release-profile proof smoke in production.

Frontier claim sets are diagnostics only. They must not be presented as hosted
alpha release reliability.
