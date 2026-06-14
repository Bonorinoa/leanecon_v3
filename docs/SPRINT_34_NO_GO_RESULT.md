# Sprint 34 Release-Prep No-Go Result

**Date:** 2026-06-13 MST  
**Status:** closed; no-go for hosted alpha deployment
**Scope:** release-prep only; no deployment performed

Sprint 34 is closed. Railway was not deployed, and the repository remains in
dev mode pending Sprint 35's objective evaluation work.

## Decision

No-go: fix release blockers first.

The release denominator remains unchanged:

- claim set: `tier1_core_preamble_definable`
- claim scope: `release_reliable`
- claim type: `preamble_definable`

Frontier surfaces remain diagnostic and experimental. They were not run as
release gates and do not contribute to release reliability.

## Evidence

Passed:

- Python deterministic suite:
  `PYTHONPATH=. ./.venv/bin/python -m pytest -o addopts=''`
  passed with `299 passed`.
- Fast Lean root check:
  `cd lean_workspace && lake env lean LeanEcon.lean`
  passed.
- Local API release-profile smoke passed for `/health`, `/metrics`,
  `/metrics/prometheus`, bounded proof job acceptance, `/jobs/{job_id}`,
  `/jobs/{job_id}/events`, and review approve/reject transitions.
  Artifacts: `/private/tmp/leanecon-s34-api-smoke`.
- Redacted `.env` posture check found release-profile Mistral/Leanstral
  configuration, non-placeholder `MISTRAL_API_KEY`, release-compliant provider
  guardrail, and pricing registry coverage for `mistral-large-2512` and
  `labs-leanstral-2603`.
- No-claim live Mistral preflight against `/models` passed with HTTP 200 and
  confirmed access to `mistral-large-2512` and `labs-leanstral-2603`.
- Explicitly approved one-claim provider-backed tier 1 release sample passed:
  `LEANECON_BUDGET_PROFILE=release PYTHONPATH=. ./.venv/bin/python -m
  evals.local_gate --claim-set tier1_core_preamble_definable --budget-profile
  release --limit 1 --sample-seed 17 --output-dir
  /private/tmp/leanecon-s34-provider-sample --allow-unready`.
  Result: `100.0% (1/1)` release-reliable, no failures, selected claim
  `t1_cara_utility_negative`, verified via `trivial_shortcut`.
  Artifacts: `/private/tmp/leanecon-s34-provider-sample`.

Failed or incomplete:

- Tier 1 release local gate:
  `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set
  tier1_core_preamble_definable --budget-profile release --output-dir
  /private/tmp/leanecon-s34-tier1 --allow-unready`
  failed `0/24`; all claims failed with `provider_unavailable`.
  Artifact: `/private/tmp/leanecon-s34-tier1`.
- The saved local-gate readiness record shows only one blocker:
  `planner_endpoint_reachable`.
- Readiness detail:
  `Planner provider unavailable: Mistral endpoint unreachable at
  https://api.mistral.ai/v1 (<urlopen error [Errno 8] nodename nor servname
  provided, or not known>)`.
- Docker release-image build reached the local daemon but failed before build
  execution because GHCR denied anonymous access to
  `ghcr.io/bonorinoa/leanecon-lean-base:latest` with `403 Forbidden`.
- After local `docker login ghcr.io` succeeded, `docker build --pull=false -t
  leanecon-v3:ci .` authenticated to GHCR but still failed before build
  execution because `ghcr.io/bonorinoa/leanecon-lean-base:latest` resolved as
  `not found`.
- A new local Lean base image source was added at `Dockerfile.lean-base` and
  built successfully as `ghcr.io/bonorinoa/leanecon-lean-base:latest`. The base
  build proved `/root/.elan`, `/lean_workspace`, `lean --version`,
  `lake --version`, `lake build LeanEcon`, and
  `lake env lean LeanEcon.lean`.
- With that local base image present, `docker build --pull=false -t
  leanecon-v3:ci .` passed. The app image build copied `/root/.elan` and
  `/lean_workspace` from the base image and ran `lean --version`,
  `lake --version`, and `cd /app/lean_workspace && lake env lean LeanEcon.lean`
  successfully during the build. A direct runtime check with a non-login shell
  also found `/root/.elan/bin/lean` and ran the same Lean/Lake checks.
- Hosted smoke was not run because no hosted URL or Railway credentials were
  available in the execution environment.

## Diagnosis

The original tier 1 release gate failure is best diagnosed as a network/DNS
reachability failure during the benchmark local-gate run, not as
release-profile drift, missing Mistral credentials, placeholder secrets,
missing pricing coverage, or model unavailability. The approved one-claim
sample demonstrates that the current release provider path is reachable for a
sampled `tier1_core_preamble_definable` claim, but it does not replace the
full 24-claim tier 1 release gate.

Supporting facts:

- `.env` selects the release budget profile and the Mistral-primary alpha
  provider posture.
- `MISTRAL_API_KEY` is present and non-placeholder when inspected without
  exposing the secret.
- Provider guardrail is release-compliant.
- Pricing registry covers the required planner and Leanstral models.
- A no-claim `/models` preflight succeeds when network access is explicitly
  allowed.
- The approved one-claim provider-backed tier 1 sample passed with readiness
  checks green, including `planner_endpoint_reachable`.

The release-image blocker is partially resolved locally: the repo now has a
reproducible Lean base image definition, the base image validates the required
Lean workspace/toolchain state, and the app release image builds locally against
that base image. The remaining remote-image step is to publish
`ghcr.io/bonorinoa/leanecon-lean-base:latest` to GHCR so CI/Railway builders can
pull the same base image.

## Required Before Hosted Alpha

1. Re-run the full tier 1 release local gate with explicit approval. The
   one-claim provider-backed sample passed, but it does not prove the full
   24-claim release denominator. Do not send additional benchmark claims to
   external providers without explicit approval.
2. Validate the release image by either:
   - publishing `ghcr.io/bonorinoa/leanecon-lean-base:latest` from
     `Dockerfile.lean-base`, or
   - using CI to prove the image lane.
3. CI or local Docker must prove:
   - base image has `/root/.elan`
   - base image has `/lean_workspace`
   - app image can run `lean --version`
   - app image can run `lake --version`
   - app image can run `cd /app/lean_workspace && lake env lean LeanEcon.lean`
4. Run hosted smoke only after release local gate and release-image readiness
   are green.

Do not broaden the release denominator, promote frontier metrics, or weaken the
Sprint 31 release budget defaults to resolve these blockers.
