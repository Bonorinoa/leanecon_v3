# Railway Hobby Deployment Checklist

Sprint 34 deployment work must wait until the local release-candidate audit in
`docs/SPRINT_30_LOCAL_RC_AUDIT.md` is green. The broader Sprint 30-35 execution
plan is `docs/SPRINTS_30_35_MASTER_PLAN.md`.

## Minimum Bar Before Any Readiness Claim
1. The developer edit-loop gate passes on the deployment branch:
   `PYTHONPATH=. ./.venv/bin/python -m pytest -o addopts=''` and
   `cd lean_workspace && lake env lean LeanEcon.lean`.
2. The local release-candidate gate passes for the release denominator:
   `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set tier1_core_preamble_definable --output-dir /private/tmp/leanecon-alpha-tier1 --allow-unready`.
3. The release-image gate passes before any hosted redeploy:
   `cd lean_workspace && lake exe cache get`, `cd lean_workspace && lake build`,
   and `docker build --pull=false -t leanecon-v3:ci .`.
4. `GET /health` reports Lean availability, backend capability metadata, and no missing required secrets.
5. `GET /health` reports `budget_profile.active = release` and a release-compliant provider guardrail.
6. `GET /metrics` returns integrity metrics, benchmark category mix, backend status, active budget profile, provider guardrail, cost by stage/model/claim type/claim scope, token usage source, latency by stage, and budget exhaustion counters without schema errors.
7. `GET /jobs/{job_id}/events` emits ordered SSE events:
   `job.update`, stage start/completion or failure, prover turn/tool events, and terminal status.
8. `POST /jobs/{job_id}/review` is exercised for approve and reject transitions.
9. SQLite-backed jobs survive planner, formalizer, and prover lifecycles plus concurrent polling.
10. Planner, formalizer, prover, and final compile timeouts are exercised explicitly.
11. Benchmark artifacts are written to separate directories for `live_pipeline` and `benchmark_mode`.
12. Historical artifacts under `benchmark_baselines/v3_alpha/` are not presented as release truth.
13. No public score or production-readiness statement is made until all items above are satisfied.

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

- Developer edit-loop gate:
  `PYTHONPATH=. ./.venv/bin/python -m pytest -o addopts=''` plus
  `cd lean_workspace && lake env lean LeanEcon.lean`. This is the ordinary
  developer and PR CI lane; it deliberately avoids full `lake build`.
- Local release-candidate gate:
  the developer edit-loop gate plus
  `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set tier1_core_preamble_definable --output-dir /private/tmp/leanecon-alpha-tier1 --allow-unready`.
  This is the release denominator only.
- Release-image gate:
  `cd lean_workspace && lake exe cache get`, `cd lean_workspace && lake build`,
  and `docker build --pull=false -t leanecon-v3:ci .`. This lane validates
  cache/image readiness and may be slower on cold cache.
- Hosted deployment gate:
  production `/health`, `/metrics`, `/metrics/prometheus`, bounded jobs, SSE,
  review transitions, and one release-profile proof smoke after image readiness.

Frontier claim sets are diagnostics only. They must not be presented as hosted
alpha release reliability.

## Lean Image And Cache Expectations

The Railway image is built from `Dockerfile`. The application stage copies
`/root/.elan` and `/lean_workspace` from
`ghcr.io/bonorinoa/leanecon-lean-base:latest`, puts `/root/.elan/bin` on
`PATH`, installs `uv` so `uvx lean-lsp-mcp` is available when frontier tooling
needs it, and runs `lean --version`, `lake --version`, and
`cd /app/lean_workspace && lake env lean LeanEcon.lean` during image build.

The full Lake build remains an infrastructure gate. It is required before
hosted redeploy, but it is not part of normal edit-loop work. If the release
image lane has a cold cache, first run `lake exe cache get`; do not delete Lake
or mathlib caches as part of deployment prep unless a stale-cache diagnosis
requires it.

`lean-lsp-mcp` readiness is useful deployment telemetry for the
mathlib-native frontier path. It is not a release blocker for
`tier1_core_preamble_definable` unless `/health` or the prover runtime cannot
report the LSP state cleanly.
