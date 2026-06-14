# LeanEcon v3 Sprints 30-35 Master Plan

**Date:** 13 June 2026  
**Status:** execution-ready planning asset  
**Canonical scope:** this document is the working master plan for Sprints 30-35.
`docs/ROADMAP_SPRINTS_30_35.md` remains as the compact roadmap summary.

## Operating Contract

The alpha release denominator is frozen:

- claim set: `tier1_core_preamble_definable`
- scope: `release_reliable`
- claim type: `preamble_definable`
- trust anchor: deterministic Lean 4 verification

Frontier claim sets stay outside the release denominator:

- `tier2_frontier_preamble_definable`
- `tier2_frontier_mathlib_native`

Every sprint must keep release-reliable work separate from frontier or
experimental work. Cost, latency, token usage, tool calls, timeout behavior, and
failure classification are release artifacts, not optional diagnostics.

## Sprint 30: Local Release Candidate Audit

**Goal:** decide whether the current alpha is ready to publish as a local release
candidate.

**Release-reliable work:**

- Run deterministic gates: Python suite, fast Lean root check, and
  `tier1_core_preamble_definable` local gate.
- Run local API smoke checks only after deterministic gates are green.
- Audit generated artifacts for scope-separated metrics, cost, latency, token
  usage, and deployment readiness.
- Fix only release-blocking bugs, severe documentation mismatches, or artifact
  hygiene issues exposed by the gates.

**Frontier work:**

- Run `tier2_frontier_preamble_definable` and `tier2_frontier_mathlib_native`
  only as diagnostics after release gates pass.
- Confirm frontier outputs include `failure_class` and
  `recommended_next_action` records.
- Do not count frontier pass rate against release readiness.

**Success criteria:**

- `pytest`, `lake env lean LeanEcon.lean`, and tier 1 local gate pass cleanly.
- Sprint 30 report includes exact commands, output paths, release metrics,
  average latency by stage, cost by stage/model, token usage source, and a
  deploy go/no-go.
- Frontier queue records are present for failed frontier attempts and are
  excluded from `release_reliable_metrics`.
- Mistral token economics and projected cost for 10 concurrent release-profile
  jobs are documented.

**Assets:**

- `docs/SPRINT_30_LOCAL_RC_AUDIT.md`
- benchmark run directories under `/private/tmp/leanecon-s30-*`
- cost/latency/token report section in the Sprint 30 audit result
- short goal-mode prompt for targeted execution

**Ready for targeted execution prompt:**

```text
/goal Complete Sprint 30 local release-candidate audit for LeanEcon v3.

Read docs/SPRINT_30_LOCAL_RC_AUDIT.md, docs/ALPHA_CHECKPOINT.md,
docs/ARCHITECTURE_v3.md, docs/PROVER_STATE_MACHINE.md, and
benchmark_baselines/RUNNING_BENCHMARKS.md.

Run only the deterministic Sprint 30 gates first. If they pass, run frontier
diagnostics and local API smoke. Fix only release-blocking bugs or severe docs
mismatches. Final response must report commands, pass/fail, artifact paths,
release metrics, frontier diagnostics, cost/latency/token results, and deploy
go/no-go.
```

**Suggested commit message after asset generation:** `docs: add sprints 30-35 execution plan`

## Sprint 31: Explicit Budget Profiles, Cost Observability, Provider Guardrails

**Goal:** make capability ceilings and cost explicit, enforced, and visible.

**Release-reliable work:**

- Add `release`, `frontier`, and `research` budget profiles as first-class
  configuration, with `release` as the default for public/API release paths.
- Surface active profile, timeout caps, tool-call caps, provider/model, latency,
  token usage, and estimated cost in CLI summaries, API job payloads, and
  `/metrics`.
- Attribute cost by stage, model, claim set, claim type, and claim scope in
  benchmark reports.
- Add provider guardrails so alpha release uses Mistral-primary defaults unless
  explicitly overridden for non-release experiments.

**Frontier work:**

- Permit cheaper planner-only fallback exploration only behind a non-release
  profile.
- Record provider strategy in a decision note before changing defaults.

**Success criteria:**

- Budget profiles are enforced and visible in benchmark JSON, API job state,
  and metrics output.
- Budget exhaustion is distinguishable from capability gaps and provider/tool
  failures.
- Provider decision record recommends Mistral-primary for alpha release or
  explicitly documents any non-release fallback experiment.

**Assets:**

- budget profile configuration and tests
- benchmark/API metrics schema updates
- docs update in `docs/ARCHITECTURE_v3.md`
- provider decision record

**Ready for targeted execution prompt:**

```text
/goal Implement Sprint 31 budget profiles and cost observability.

Add release/frontier/research profiles, enforce caps consistently, expose active
profile and cost/latency/token fields in CLI, API, metrics, and benchmark
artifacts. Keep release profile Mistral-primary and ensure frontier experiments
cannot change release defaults. Add tests for profile selection, budget
exhaustion reporting, metrics fields, and benchmark summary schema.
```

## Sprint 32: Lean Build And Infrastructure Predictability

**Goal:** make Lean infrastructure fast and predictable for developers and
deployment.

**Release-reliable work:**

- Define separate command lanes:
  - developer edit-loop gate: Python focused tests plus `lake env lean LeanEcon.lean`
  - release local gate: deterministic suite plus tier 1 local gate
  - release-image gate: full Lake build with cache/image expectations
  - deployment gate: hosted health/metrics/job smoke after image readiness
- Improve CI and Docker/Lake cache strategy so full Lean builds are
  infrastructure checks, not routine proof-attempt overhead.
- Document how deployment images obtain a predictable Lean workspace state.

**Frontier work:**

- Keep Mathlib-native frontier LSP/search infrastructure observable, but do not
  make frontier success a release-image requirement.

**Success criteria:**

- Developers have documented fast checks that do not require replaying a full
  Mathlib build.
- CI/deployment image flow has a clear cache or pre-warm strategy.
- `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md` distinguishes local, image, and hosted
  gates.

**Assets:**

- updated deployment and developer docs
- CI/Docker/Lake cache improvements if needed
- command reference for developer, release, and deployment checks

**Ready for targeted execution prompt:**

```text
/goal Implement Sprint 32 Lean build and infrastructure predictability.

Audit current Dockerfile, CI workflow, lake workspace, and deployment checklist.
Separate developer edit-loop, release local, release-image, and hosted deployment
commands. Improve caching/pre-warm behavior where practical and document exact
commands plus failure interpretation. Keep frontier diagnostics separate from
release-image readiness.
```

## Sprint 33: Frontier Data Flywheel, Synthesis Experiments, Human-Factors Eval

**Goal:** turn frontier failures into actionable engineering data and begin
measuring the "peer for economists" workflow.

**Release-reliable work:**

- Preserve the Sprint 30/31 release denominator and budget profile boundaries.
- Use frontier findings only to propose future release-surface candidates, not
  to expand release claims without deterministic evidence.

**Frontier work:**

- Classify all frontier failures by `failure_class` and
  `recommended_next_action`.
- Prioritize missing preamble entries, prompt changes, decomposition patterns,
  and synthesis improvements from real traces.
- Strengthen `ProofSynthesizer` where traces show repeated tactic-pattern or
  hypothesis-decomposition gaps.
- Run controlled synthesis experiments on the frontier slice, including
  stronger prover models such as Goedel-Prover-V2-class models only outside the
  release profile.
- Design and run 3-5 PhD-style HIL sessions measuring planner question quality,
  formalizer faithfulness, and workflow value versus manual formalization.

**Success criteria:**

- Frontier queue has prioritized records with concrete next actions.
- Synthesis experiment reports measurable lift or a clear diagnostic explaining
  why lift did not occur.
- Human-factors protocol and initial findings are documented.

**Assets:**

- `docs/FRONTIER_FLYWHEEL.md` or equivalent
- synthesis experiment report
- HIL economist evaluation protocol and initial results
- prioritized preamble/prompt/synthesis backlog

**Ready for targeted execution prompt:**

```text
/goal Execute Sprint 33 frontier flywheel and lightweight economist HIL eval.

Aggregate frontier queues, classify failures, prioritize next actions, run a
controlled synthesis experiment on frontier claims only, and draft the first HIL
protocol for 3-5 economist/PhD-style claims. Keep release metrics unchanged and
make every frontier experiment explicitly non-release.
```

## Sprint 34: Deployment Hardening And Alpha Release

**Goal:** safely redeploy once local gates are green.

**Release-reliable work:**

- Deploy only after Sprint 30 local release-candidate gates and Sprint 32 image
  readiness are green.
- Enforce release budget profile in production.
- Validate `/health`, `/metrics`, bounded job queue behavior, SSE event stream,
  and one end-to-end release-profile proof smoke.
- Ensure Lean/LSP mode, backend capability, cost attribution, token usage, and
  timeout behavior are observable in production.
- Publish a scope statement: release-reliable on
  `tier1_core_preamble_definable`; frontier is experimental.

**Frontier work:**

- Hosted frontier attempts, if enabled, must be opt-in, capped by non-release
  budget profiles, and excluded from public reliability claims.

**Success criteria:**

- Hosted alpha matches local release-candidate scope and reliability.
- Deployment checklist and rollback steps are current.
- Release announcement is honest about denominator, limits, cost visibility, and
  frontier status.

**Assets:**

- updated `docs/RAILWAY_DEPLOYMENT_CHECKLIST.md`
- deployment runbook and rollback notes
- production smoke artifacts
- release announcement draft

**Ready for targeted execution prompt:**

```text
/goal Execute Sprint 34 deployment hardening and alpha release.

Confirm local release gates and release-image gates are green, deploy with
release budget profile enforcement, validate health/metrics/jobs/SSE/proof smoke,
and update runbooks plus release announcement. Do not present frontier metrics as
release reliability.
```

## Sprint 35: Careful Expansion And Human-Factors Baseline

**Goal:** broaden reliable undergraduate mathematical economics coverage without
weakening release honesty.

**Release-reliable work:**

- Promote new release-reliable claims only after deterministic evidence under
  the release profile.
- Prefer preamble plus direct-close paths for first expansion.
- Expand benchmark examples across consumer theory, producer theory, games,
  constrained optimization, general equilibrium primitives, and dynamic
  programming only when the theorem surface is stable.
- Update claim sets and manifests with clear provenance and scope.

**Frontier work:**

- Continue using frontier traces to identify expansion candidates and model
  improvement data.
- Produce a decision record on whether production traces justify fine-tuning
  data collection.
- Run a small economist/PhD-student cohort using the Sprint 33 HIL protocol.

**Success criteria:**

- Release denominator grows only where deterministic proof evidence is present.
- Human-factors baseline is documented with workflow-value findings and failure
  modes.
- Next-step model improvement recommendation is explicit.

**Assets:**

- updated claim sets and benchmark manifest
- expanded HIL protocol and results
- release-surface expansion report
- fine-tuning/data-collection decision record

**Ready for targeted execution prompt:**

```text
/goal Execute Sprint 35 careful release-surface expansion and HIL baseline.

Use frontier data and stable preamble paths to propose a small set of new
release-reliable claims. Promote only claims with deterministic evidence. Run the
economist HIL cohort, update claim sets/manifests, and write the fine-tuning data
collection decision record.
```

## Cross-Sprint Acceptance Checks

- Release and frontier metrics remain separated in docs, code outputs, and
  public statements.
- Cost, latency, tokens, tool calls, LSP calls, and timeout behavior are visible
  in benchmark and API artifacts.
- Every frontier failure has `failure_class` and `recommended_next_action`.
- Thin harness discipline is preserved: orchestration stays small, skills and
  verified Lean artifacts carry domain complexity.
- The Lean kernel remains the only trust anchor for proof success.
