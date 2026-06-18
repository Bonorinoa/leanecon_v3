# Railway Hobby Deployment Checklist

This checklist is the authoritative deployment gate for LeanEcon v3 hosted
alpha. Historical sprint plans and no-go records are preserved in
`docs/LeanEcon Engineering Log.md`; this file describes the current path.

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
13. Tier 2 is presented only as public beta/diagnostic unless a future benchmark decision promotes it.
14. No public score or production-readiness statement is made until all items above are satisfied.

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
LEANECON_PROVER_PROVIDER=mistral
LEANECON_PROVER_MODEL=labs-leanstral-2603
LEANECON_PROVER_FALLBACK_BACKEND=leanstral
```

Do not deploy hosted alpha with `frontier` or `research`. Non-Mistral provider
paths and cheap fallback exploration are non-release diagnostics only.

## Gate Separation

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
needs it, asserts that the base image's `/lean_workspace/lean-toolchain`
matches the repository's `lean_workspace/lean-toolchain`, and runs
`lean --version`, `lake --version`, and
`cd /app/lean_workspace && lake env lean LeanEcon.lean` during image build.

The Lean base image is now reproducible from `Dockerfile.lean-base`.
`Dockerfile.lean-base` installs the Lean toolchain from
`lean_workspace/lean-toolchain`, copies the Lean workspace into
`/lean_workspace`, runs `lake exe cache get`, runs `lake build LeanEcon`, and
asserts `/root/.elan`, `/lean_workspace`, `lean --version`, `lake --version`,
and `lake env lean LeanEcon.lean`. The manual/push workflow
`.github/workflows/lean-base-image.yml` publishes
`ghcr.io/bonorinoa/leanecon-lean-base:latest` and a commit-SHA tag.

The full Lake build remains an infrastructure gate. It is required before
hosted redeploy, but it is not part of normal edit-loop work. If the release
image lane has a cold cache, first run `lake exe cache get`; do not delete Lake
or mathlib caches as part of deployment prep unless a stale-cache diagnosis
requires it.

`lean-lsp-mcp` readiness is useful deployment telemetry for the
mathlib-native frontier path. It is not a release blocker for
`tier1_core_preamble_definable` unless `/health` or the prover runtime cannot
report the LSP state cleanly.

## Current Operational Blockers

The remaining deployment blockers are operational, not code-policy changes:

- Publish or otherwise make available
  `ghcr.io/bonorinoa/leanecon-lean-base:latest` built from
  `Dockerfile.lean-base`. Rebuild and publish the base image after every Lean
  toolchain or Lean workspace change. The app-image build now fails fast if the
  base image's `/lean_workspace/lean-toolchain` differs from the repository's
  `lean_workspace/lean-toolchain`.
- Ensure the builder can resolve or already has `python:3.11-slim`. The latest
  local check succeeded after pulling
  `python:3.11-slim@sha256:ae52c5bef62a6bdd42cd1e8dffef86b9cd284bde9427da79839de7a4b983e7ca`.
- Re-run `docker build --pull=false -t leanecon-v3:ci .` in the deploy
  environment after the refreshed base image is available there. The latest
  local check rebuilt the Lean base at `leanprover/lean4:v4.31.0`, produced
  `ghcr.io/bonorinoa/leanecon-lean-base:latest` image `8552fcb5da58`, built
  `leanecon-v3:ci` image `ae921f431b21`, and verified container runtime
  `lean --version`, `lake --version`, and
  `cd /app/lean_workspace && lake env lean LeanEcon.lean`.
- Run hosted smoke against the deployed URL with real Mistral credentials.
- Run live provider benchmarks only with explicit approval. The latest approved
  local live gate verified Tier 1 at 100.0% (24/24), focused Tier 2 preamble at
  66.7% (6/9), and Tier 2 mathlib-native at 100.0% (3/3), but hosted smoke is
  still required before public deployment.

Local Docker or the CI release-image lane must prove all of the following
before hosted alpha deployment:

- the Lean base image has `/root/.elan`
- the Lean base image has `/lean_workspace`
- the Lean base image's `/lean_workspace/lean-toolchain` matches this repo's
  `lean_workspace/lean-toolchain`
- the app image can run `lean --version`
- the app image can run `lake --version`
- the app image can run `cd /app/lean_workspace && lake env lean LeanEcon.lean`

Hosted smoke is required after image readiness and before any public alpha
readiness statement. Capture `/health`, `/metrics`, `/metrics/prometheus`,
bounded proof job acceptance, `/jobs/{job_id}`, `/jobs/{job_id}/events`, review
approve/reject transitions, and one release-profile proof smoke when provider
credentials and endpoint access permit it.

## Rollback Notes

If hosted smoke fails after deploy:

1. Stop the readiness announcement and keep the release denominator unchanged.
2. Roll Railway back to the last deployment whose image passed `/health`,
   `/metrics`, `/metrics/prometheus`, and a bounded release-profile proof smoke.
3. Restore the last known-good production environment values for
   `LEANECON_BUDGET_PROFILE`, planner, formalizer, prover, and Mistral
   credentials.
4. Re-run the hosted deployment gate against the rolled-back deployment and
   save smoke responses under a fresh `/private/tmp/leanecon-api-smoke-*`
   directory.
5. Treat any frontier, mathlib-native, or non-release profile failure as
   diagnostic unless it breaks release-profile health, metrics, queue bounds, or
   Lean kernel verification for `tier1_core_preamble_definable`.

Rollback does not authorize broadening the release denominator, promoting
frontier results, or weakening Sprint 31 release budget defaults.

## Alpha Release Statement Draft

LeanEcon v3 hosted alpha is release-reliable only for the frozen
`tier1_core_preamble_definable` denominator with
`claim_scope = release_reliable`. Frontier surfaces, including mathlib-native
and frontier preamble claim sets, are diagnostic and experimental; they are not
part of release reliability. Lean kernel verification is the trust anchor for
proof success. Cost, latency, token usage, provider/model posture, budget
profile, and queue behavior are observable through release artifacts,
`/health`, `/metrics`, and `/metrics/prometheus`. Hosted alpha jobs are bounded
by the `release` budget profile.
