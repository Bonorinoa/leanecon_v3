# Sprint 30 Local Release Candidate Audit

**Date:** 13 June 2026  
**Status:** ready for targeted execution  
**Purpose:** decide whether the current LeanEcon v3 alpha should be published as
a local release candidate before hosted redeployment work begins.

## Scope Contract

Release-reliable denominator:

- `tier1_core_preamble_definable`
- `claim_scope = release_reliable`
- `claim_type = preamble_definable`

Diagnostic frontier sets:

- `tier2_frontier_preamble_definable`
- `tier2_frontier_mathlib_native`

Sprint 30 must not expand the preamble, broaden the release denominator, change
frontier strategy, or redeploy to Railway. Code changes are allowed only for
release-blocking bugs or severe documentation/artifact mismatches exposed by the
audit.

## Gate Checklist

Run gates in this order.

| Gate | Command | Required Result | Notes |
| --- | --- | --- | --- |
| Python deterministic suite | `PYTHONPATH=. ./.venv/bin/python -m pytest -o addopts=''` | pass | Release confidence gate. |
| Fast Lean root check | `cd lean_workspace && lake env lean LeanEcon.lean` | pass | Edit-loop Lean gate. |
| Tier 1 release local gate | `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set tier1_core_preamble_definable --output-dir /private/tmp/leanecon-s30-tier1 --allow-unready` | pass with release metrics separated | Release denominator only. |
| Tier 2 preamble frontier diagnostic | `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set tier2_frontier_preamble_definable --output-dir /private/tmp/leanecon-s30-tier2-preamble --allow-unready` | diagnostic artifact | Run only after release gates pass. |
| Tier 2 Mathlib frontier diagnostic | `PYTHONPATH=. ./.venv/bin/python -m evals.local_gate --claim-set tier2_frontier_mathlib_native --output-dir /private/tmp/leanecon-s30-tier2-mathlib --allow-unready` | diagnostic artifact | Run only after release gates pass. |
| Local API smoke | checklist below | pass or documented blocker | Run only after deterministic gates pass. |

Treat full `lake build LeanEcon` as a release-image or infrastructure gate. It
is not the normal Sprint 30 edit-loop gate until Sprint 32 improves Lake/Mathlib
caching.

## Artifact Paths

Use temp output directories so Sprint 30 audit artifacts do not overwrite
canonical baselines:

- `/private/tmp/leanecon-s30-tier1`
- `/private/tmp/leanecon-s30-tier2-preamble`
- `/private/tmp/leanecon-s30-tier2-mathlib`
- `/private/tmp/leanecon-s30-api-smoke` if an API smoke script or captured
  responses are added during the execution session

Expected local-gate files per directory:

- `<claim_set>.json`
- `<claim_set>.progress.jsonl`
- `<claim_set>.frontier_queue.jsonl`
- `local_gate.json`
- `local_gate.progress.jsonl`
- `local_gate.frontier_queue.jsonl`

## Local API Smoke Checklist

Run this only after deterministic gates are green.

1. Start the API with release-profile environment posture and the normal local
   port.
2. `GET /health` returns backend capability metadata, Lean status, and no
   missing required release secrets.
3. `GET /metrics` returns usage totals, usage by stage, benchmark claim counts,
   backend status, and job queue metrics without schema errors.
4. `GET /metrics/prometheus` exposes token and estimated cost counters.
5. `POST /prove` accepts one release-profile proof job without exceeding
   `JOB_MAX_CONCURRENT`.
6. `GET /jobs/{job_id}` reaches a terminal state.
7. `GET /jobs/{job_id}/events` emits ordered SSE events including job updates
   and stage/prover progress or a documented current blocker.
8. Queue behavior is bounded: attempts beyond configured concurrency stay queued
   or fail predictably rather than silently spawning unbounded prover work.

Record whether the API smoke is benchmark-ready, release-smoke-ready only, or
blocked. The existing benchmark guide notes that local-gate progress artifacts
are currently richer than public API SSE; this should be reported honestly if it
remains true.

## Cost, Latency, And Token Report Template

Fill this table from `local_gate.json`, claim-set summaries, `/metrics`, and API
job payloads.

| Surface | Claim Set Or Endpoint | Pass/Fail | Claims Or Jobs | Avg Total Latency | Planner Cost | Formalizer Cost | Prover Cost | Total Cost | Token Usage Source | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| release | `tier1_core_preamble_definable` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | release denominator |
| frontier diagnostic | `tier2_frontier_preamble_definable` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | excluded from release |
| frontier diagnostic | `tier2_frontier_mathlib_native` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | excluded from release |
| API smoke | `/health`, `/metrics`, `/prove`, `/jobs` | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | release smoke only |

Projected release-profile Mistral cost for 10 concurrent jobs:

```text
observed_avg_release_job_cost_usd =
  tier1_total_estimated_cost_usd / tier1_claims_total

projected_10_concurrent_release_jobs_usd =
  observed_avg_release_job_cost_usd * 10
```

Report Railway infrastructure cost separately from provider token cost. The
current pricing registry treats `labs-leanstral-2603` as a zero-cost sentinel
unless a real hosted price is configured.

## Frontier Queue Audit Checklist

For each frontier output directory, inspect:

- frontier queue path exists or absence is explained by all claims verifying
- records include schema version and claim identifiers
- each failed frontier attempt has `failure_class`
- each failed frontier attempt has `recommended_next_action`
- failure classes distinguish retrieval gaps, synthesis gaps, budget exhaustion,
  provider/tool failures, timeouts, and out-of-scope cases where possible
- frontier records are excluded from `release_reliable_metrics`
- frontier diagnostics do not change deploy go/no-go except for system-level
  blockers such as hangs, unbounded cost, broken artifact writing, or missing
  failure classification

## Go/No-Go Template

Use one of these outcomes.

### Go For Hosted Alpha Prep

Use only if:

- all release gates pass
- tier 1 release metrics are clean and scope-separated
- local API smoke is at least release-smoke-ready
- cost/latency/token reporting is sufficient for a public alpha scope statement
- frontier diagnostics terminate cleanly and produce structured records

Required statement:

```text
LeanEcon v3 is ready for Sprint 34 hosted alpha prep under the narrow release
scope `tier1_core_preamble_definable`. Frontier surfaces remain experimental and
are excluded from release reliability.
```

### No-Go: Fix Release Blockers First

Use if any release gate fails, API smoke is structurally broken, costs are
unbounded, or release/frontier metrics are mixed.

Required statement:

```text
Railway redeploy remains deferred. The next session should fix the listed
release blockers without expanding the release denominator or changing frontier
strategy.
```

### Conditional Go: Infrastructure Gate Pending

Use if release gates pass but release-image work remains.

Required statement:

```text
Local release candidate is green, but hosted deployment waits for Sprint 32
release-image predictability and Sprint 34 production smoke validation.
```

## Sprint 30 Execution Report Template

```markdown
# Sprint 30 Local RC Audit Result

Date:
Branch:
Commit:

## Commands Run

- `...`: pass/fail, elapsed time, artifact path

## Release-Reliable Metrics

- claim set: `tier1_core_preamble_definable`
- pass@1:
- claims passed/total:
- average total latency:
- cost by stage:
- cost by model:
- token usage source:
- tool calls:
- LSP/native tool calls:

## Frontier Diagnostics

- `tier2_frontier_preamble_definable`: pass/fail, records, key failure classes
- `tier2_frontier_mathlib_native`: pass/fail, records, key failure classes
- recommended next actions:

## API Smoke

- `/health`:
- `/metrics`:
- `/metrics/prometheus`:
- `/prove`:
- `/jobs/{job_id}`:
- `/jobs/{job_id}/events`:
- bounded queue behavior:

## Cost Projection

- observed average release job cost:
- projected 10 concurrent release jobs:
- Railway infrastructure cost:

## Go/No-Go

Decision:
Reason:
Blockers:
Next focused session:
```

## Targeted Goal-Mode Prompt

```text
/goal Complete the Sprint 30 local release-candidate audit for LeanEcon v3.

Read docs/SPRINT_30_LOCAL_RC_AUDIT.md, docs/SPRINTS_30_35_MASTER_PLAN.md,
docs/ALPHA_CHECKPOINT.md, docs/ARCHITECTURE_v3.md,
docs/PROVER_STATE_MACHINE.md, docs/RAILWAY_DEPLOYMENT_CHECKLIST.md, and
benchmark_baselines/RUNNING_BENCHMARKS.md.

Objectives:
1. Run the deterministic Python suite, fast Lean root check, and
   tier1_core_preamble_definable local gate.
2. If those pass, run tier2_frontier_preamble_definable and
   tier2_frontier_mathlib_native as diagnostics only.
3. Inspect all outputs for scope separation, frontier records, failure classes,
   recommended next actions, latency, token usage, estimated cost, tool budgets,
   and generated-artifact hygiene.
4. Fix only release-blocking bugs, severe documentation mismatches, or small
   artifact/reporting defects.
5. Run local API smoke if deterministic gates are green.
6. Report whether hosted redeploy should proceed, remain deferred, or wait for
   Sprint 32 release-image work.

Constraints:
- Do not expand the economics preamble.
- Do not merge frontier claims into the release denominator.
- Do not change frontier strategy.
- Do not redeploy.
- Treat full lake build as a release-image/infrastructure gate unless cache
  state makes it practical.

Final response must include exact commands, pass/fail results, output paths,
release metrics by scope, frontier diagnostics, cost/latency/token report,
projected 10-concurrent-job provider cost, API smoke result, and go/no-go.
```

## Suggested Commit Message

After this planning asset is committed:

```text
docs: add sprints 30-35 execution plan
```
