# Sprint 30 Plan: Redeployable Alpha Checkpoint

**Theme:** Publishable LeanEcon v3 alpha.  
**Primary outcome:** A working system capable of advanced undergraduate mathematical economics within explicit scope boundaries.

## Goal

Sprint 30 is the checkpoint for cleanup, release validation, and redeployment.

The core goal is:

> Ship an honest alpha, not a research prototype with inflated claims.

## Workstreams

### 1. Final Audit

Audit:

- code quality,
- API behavior,
- docs,
- benchmark artifacts,
- release examples,
- scope classification,
- frontier data capture,
- local deployment path.

Target result:

- no known critical mismatch between docs and behavior.

### 2. Release Gate

Required checks:

- full Python tests,
- Lean build,
- deterministic CI smoke,
- release benchmark,
- local deployment smoke,
- API health and metrics,
- no accidental baseline mutation.

Target result:

- release decision is evidence-based.

### 3. Polish And Commit

Before redeployment:

- remove stale generated files,
- update changelog or engineering log,
- ensure docs link to the right commands,
- commit coherent changes,
- push only after tests pass.

Target result:

- repository state is clean and explainable.

### 4. Railway Redeploy

Only after local gates pass:

- recreate Railway service,
- configure secrets,
- validate `/health`,
- validate `/metrics`,
- run a small API proof smoke,
- record deployment status and limitations.

Target result:

- hosted alpha exists and reflects the documented scope.

## Sprint 30 Release Criteria

LeanEcon v3 alpha is publishable if:

- release-reliable undergraduate claims reach the agreed pass-rate target,
- frontier claims are classified and collected separately,
- failed release-reliable claims have clear failure classes,
- API and local deployment are stable,
- docs clearly state limitations,
- hosted deployment passes smoke checks.

## Sprint 30 Non-Goals

- Do not make last-minute broad architecture changes.
- Do not expand frontier capability to rescue release metrics.
- Do not deploy if local gates are red.

## Codex Goal Prompt Draft

```text
/goal Complete Sprint 30 redeployable alpha checkpoint for LeanEcon v3.

Assume Sprints 27-29 are complete. Focus on final audit, release gates, cleanup, commit readiness, and Railway redeployment only if gates pass.

Primary objectives:
1. Run a final code/docs/benchmark/deployment audit.
2. Run full tests, Lean build, deterministic CI smoke, release benchmark, and local deployment smoke.
3. Fix only release-blocking issues; avoid broad new architecture work.
4. Clean generated artifacts and prepare coherent commit-ready changes.
5. If all gates pass, redeploy with Railway and validate health, metrics, and a small API proof smoke.
6. Update docs with final alpha status, limitations, and deployment notes.

Constraints:
- Do not redeploy with failing local gates.
- Do not count frontier claims against release-reliable pass rate.
- Do not make undocumented provider or credential assumptions.

Before final response:
- Report exact commands run and outcomes.
- Report release benchmark result by scope tier.
- Report deployment URL only if Railway redeployment succeeds.
- Report remaining limitations candidly.
```

