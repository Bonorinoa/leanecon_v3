# Sprint 34 Release-Prep No-Go Result

**Date:** 2026-06-13 MST  
**Status:** no-go for hosted alpha deployment  
**Scope:** release-prep only; no deployment performed

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
- Hosted smoke was not run because no hosted URL or Railway credentials were
  available in the execution environment.

## Diagnosis

The tier 1 release gate failure is best diagnosed as a network/DNS reachability
failure during the benchmark local-gate run, not as release-profile drift,
missing Mistral credentials, placeholder secrets, missing pricing coverage, or
model unavailability.

Supporting facts:

- `.env` selects the release budget profile and the Mistral-primary alpha
  provider posture.
- `MISTRAL_API_KEY` is present and non-placeholder when inspected without
  exposing the secret.
- Provider guardrail is release-compliant.
- Pricing registry covers the required planner and Leanstral models.
- A no-claim `/models` preflight succeeds when network access is explicitly
  allowed.

The release-image blocker is separate: the local Docker daemon is available,
but the Lean base image cannot be fetched anonymously from GHCR. Release-image
validation needs either authenticated GHCR access or CI proof from an
environment that can pull `ghcr.io/bonorinoa/leanecon-lean-base:latest`.

## Required Before Hosted Alpha

1. Re-run the tier 1 release local gate with approved provider network access,
   or run an explicitly approved one-claim tier 1 sample first. Do not send
   benchmark claims to external providers without explicit approval.
2. Validate the release image by either:
   - authenticating Docker/GHCR locally and running
     `docker build --pull=false -t leanecon-v3:ci .`, or
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
