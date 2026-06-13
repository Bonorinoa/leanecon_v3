# Sprint 30: Redeployable Alpha Checkpoint

**Theme:** local release candidate first, hosted redeploy second.  
**Primary outcome:** decide whether LeanEcon v3 alpha is ready to publish.

## Starting Point

The Sprint 26-29 working docs have been replaced by stable checkpoint docs:

- `docs/ALPHA_CHECKPOINT.md`
- `docs/ROADMAP_SPRINTS_30_35.md`
- `docs/ARCHITECTURE_v3.md`
- `docs/PROVER_STATE_MACHINE.md`

The current release denominator is `tier1_core_preamble_definable` scoped as
`release_reliable`. Frontier sets remain diagnostic and are excluded from
release reliability.

## Required Local Gates

Run these before any deployment work:

```bash
./.venv/bin/python -m pytest -o addopts=''
lake env lean LeanEcon.lean
./.venv/bin/python -m evals.local_gate --claim-set tier1_core_preamble_definable --output-dir /private/tmp/leanecon-s30-tier1 --allow-unready
```

Then run frontier diagnostics if the release gates are green:

```bash
./.venv/bin/python -m evals.local_gate --claim-set tier2_frontier_preamble_definable --output-dir /private/tmp/leanecon-s30-tier2-preamble --allow-unready
./.venv/bin/python -m evals.local_gate --claim-set tier2_frontier_mathlib_native --output-dir /private/tmp/leanecon-s30-tier2-mathlib --allow-unready
```

Treat the full `lake build LeanEcon` as a release-image or infrastructure gate.
It is too expensive and noisy to be the normal local edit-loop gate until Lake
and Mathlib caching are improved.

## Release Criteria

LeanEcon v3 alpha is publishable when:

- deterministic Python tests pass
- the fast Lean root check passes
- tier 1 release-reliable claims meet the agreed pass-rate target
- release and frontier metrics are separated in outputs
- frontier failures produce queue records with failure classes and next actions
- API health, metrics, and bounded job behavior pass a local smoke test
- docs clearly state scope, limits, and deployment status

## Non-Goals

- Do not broaden the release denominator to improve headline capability.
- Do not count frontier claims against release reliability.
- Do not do broad architecture rewrites.
- Do not redeploy while local release gates are red.

## Suggested Sprint 30 Goal Prompt

```text
/goal Complete the Sprint 30 local release-candidate audit for LeanEcon v3.

Read docs/ALPHA_CHECKPOINT.md, docs/ROADMAP_SPRINTS_30_35.md,
docs/ARCHITECTURE_v3.md, docs/PROVER_STATE_MACHINE.md, and this file.

Objectives:
1. Run deterministic Python tests, the fast Lean root check, and the tier1_core_preamble_definable local gate.
2. If those pass, run tier2_frontier_preamble_definable and tier2_frontier_mathlib_native as diagnostics only.
3. Inspect outputs for scope-separated metrics, frontier queue records, latency, timeout behavior, and generated-artifact hygiene.
4. Fix only release-blocking bugs, severe documentation mismatches, or small maintainability issues.
5. Run a local API smoke if the release gates are green.
6. Report whether Railway redeploy should proceed or remain deferred.

Constraints:
- Do not expand the economics preamble.
- Do not merge frontier claims into the release denominator.
- Do not redeploy unless local gates justify it.
- Treat full lake build as an infrastructure/release-image gate unless cache state makes it practical.

Before final response, report exact commands, pass/fail results, output paths,
release metrics by scope, frontier diagnostics, and remaining blockers.
```
