# Frontier Flywheel

**Date:** 14 June 2026  
**Sprint:** 33  
**Status:** non-release frontier diagnostic asset

Sprint 33 keeps the alpha release denominator unchanged:

- claim set: `tier1_core_preamble_definable`
- scope: `release_reliable`
- claim type: `preamble_definable`

All frontier and synthesis experiments in this document are non-release. They do
not change Sprint 31 release defaults, do not expand the release denominator, and
do not support a redeploy decision.

## Artifacts

Fresh Sprint 33 artifacts:

| Path | Contents | Status |
| --- | --- | --- |
| `/private/tmp/leanecon-s33-tier2-preamble/tier2_frontier_preamble_definable.progress.jsonl` | Partial `frontier` profile preamble frontier run | Interrupted after claim 5 because the run entered a long `lake build` subprocess after the claim reached 600s. |
| `/private/tmp/leanecon-s33-tier2-mathlib/tier2_frontier_mathlib_native.json` | Baseline mathlib-native frontier run | Completed 3/3, frontier-only. |
| `/private/tmp/leanecon-s33-tier2-mathlib/tier2_frontier_mathlib_native.frontier_queue.jsonl` | Baseline mathlib-native frontier queue | Contains operational fields from Sprint 33 schema update. |
| `/private/tmp/leanecon-s33-synth-after/tier2_frontier_mathlib_native.json` | Controlled after-run for corrected queue accounting | Completed 3/3, frontier-only. |
| `/private/tmp/leanecon-s33-synth-after/tier2_frontier_mathlib_native.frontier_queue.jsonl` | Corrected after-run frontier queue | Includes budget, timing, usage, tool-budget, synthesis, candidate, and retrieval counts. |

Historical fallback artifacts inspected:

- `benchmark_baselines/v3_alpha/benchmark_mode/tier2_frontier_preamble_definable.json`
- `benchmark_baselines/v3_alpha/benchmark_mode/tier2_frontier_mathlib_native.json`
- `/private/tmp/leanecon-s30-timeout-tier2-mathlib-3/tier2_frontier_mathlib_native.json`

## Failure Taxonomy

Use these classes for frontier queue triage:

| Failure class | Meaning | Recommended next action |
| --- | --- | --- |
| `missing_preamble_definition` | Lean identifier or primitive is unavailable. | Add a candidate preamble definition, then rerun the frontier slice. |
| `missing_preamble_theorem` | Definitions exist, but no proven bridge theorem matches the claim. | Add a small preamble theorem or bridge lemma with a direct theorem stub. |
| `formalizer_template_gap` | Planner/formalizer did not produce a usable theorem target or parseable Lean. | Improve the formalizer template or prompt rule for that claim shape. |
| `planner_assumption_gap` | Claim needs stronger assumptions/defaults or rejects vacuity. | Improve planner defaults and assumption questions. |
| `retrieval_premise_gap` | Mathlib/native path lacks usable premise evidence. | Add retrieval seed coverage or a better LeanSearch query pattern. |
| `synthesis_tactic_assembly_gap` | Premises or synthesis events exist, but generated tactics fail or stall. | Improve tactic assembly, premise resolution, or hypothesis-aware candidate generation. |
| `provider_or_tooling_failure` | Timeout, LSP/provider failure, budget cap, or infrastructure issue. | Fix infrastructure, provider readiness, timeout clamping, or cache behavior. |
| `out_of_scope` | Claim is outside the current LeanEcon surface. | Keep out of release metrics and record as future research material. |

## Priority Rules

Prioritize frontier work in this order:

1. Missing preamble candidates that affect multiple preamble-definable frontier
   claims and can be closed by small LeanEcon bridge lemmas.
2. Prompt/formalizer template gaps that prevent usable theorem targets.
3. Retrieval premise gaps where no useful Mathlib premise reaches the prover.
4. Synthesis/tactic assembly gaps where retrieved premises are present but
   candidate tactics fail.
5. Infrastructure/provider/budget issues that make frontier runs unbounded or
   non-reproducible.
6. Out-of-scope claims, which should be retained only as research backlog.

## Sprint 33 Queue

Fresh preamble frontier run:

- Completed claims before interruption: 4 verified, 1 failed.
- `t2_ces_crs` failed with `target_timeout` after 600s, then the process entered
  a long `lake build` subprocess before the next claim completed.
- Next action: classify as `provider_or_tooling_failure`; prevent frontier
  local-gate runs from invoking full `lake build` in the edit loop before
  rerunning the full preamble frontier set.

Historical preamble fallback summary:

- `tier2_frontier_preamble_definable`: 7/10 verified in the historical
  benchmark-mode artifact.
- Historical failures: `t2_ces_crs` (`compile_failed`), `t2_bellman_contraction`
  (`lsp_unavailable`), `t2_indirect_utility_roys_identity` (`lsp_unavailable`).
- Next actions:
  - `t2_ces_crs`: inspect direct-close trace for a missing fixed-point or
    contraction bridge theorem candidate.
  - `t2_bellman_contraction`: fix LSP/tooling availability before treating this
    as a proof gap.
  - `t2_indirect_utility_roys_identity`: fix LSP/tooling availability, then
    decide whether a Nash witness bridge is still needed.

Fresh mathlib-native frontier summary:

- Baseline: `/private/tmp/leanecon-s33-tier2-mathlib`, 3/3 verified,
  `release_reliable_metrics = 0/0`, total estimated cost `$0.0018`.
- After-run: `/private/tmp/leanecon-s33-synth-after`, 3/3 verified,
  `release_reliable_metrics = 0/0`, total estimated cost `$0.0017`.
- Frontier records contain `budget_profile`, `failure_code`,
  `termination_reason`, `timing_breakdown`, `usage_by_stage`, `tool_budget`,
  `budget_exhaustion`, `synthesis_event_count`, `candidate_attempt_count`, and
  `retrieval_event_count`.

## Synthesis Experiment

Controlled slice:

- `t2_contraction_mapping_fixed_point`
- `t2_extreme_value_repair`
- `t2_monotone_sequence_converges`

Baseline result:

- Pass@1: 3/3.
- `synthesis_efficiency`: 0.75.
- `premise_match_rate@3`: 0.75.
- `candidate_attempt_count`: 24.
- `candidate_success_rate`: 0.0.

After-result:

- Pass@1: 3/3.
- `synthesis_efficiency`: 0.857143.
- `premise_match_rate@3`: 0.857143.
- `candidate_attempt_count`: 24.
- `candidate_success_rate`: 0.0.

Interpretation:

- No pass-rate lift was available because the fresh mathlib-native slice already
  verified all three claims.
- The useful diagnostic is that candidate tactics still failed at 0% success
  before fallback closure. The traces repeatedly attempted premise names such as
  `tendsto_atTop'` or `tendsto_atTop_atTop` that Lean rejected as unknown
  identifiers.
- Sprint 33 therefore made an observability/schema improvement rather than a
  broad prover rewrite: frontier queue records now preserve candidate attempt
  counts and synthesis/retrieval evidence so the next prover change can target
  premise resolution directly.

Sprint 34 candidate:

- Tighten `ProofSynthesizer.resolve_premises` so LeanSearch/RAG names are not
  treated as usable global identifiers unless they are namespace-qualified,
  file-namespace-resolved, or validated by a cheap local check.

## Economist HIL Protocol

Use 3-5 `phd_qual_alpha` claims as evaluation material only:

- `v3_bellman_fixed_point`
- `v3_blackwell_sufficient_conditions`
- `v3_value_function_monotone`
- `v3_walras_law_excess_demand`
- `v3_excess_demand_homogeneous_degree_zero`

Session format:

1. Show the economist the raw claim and planner questions/defaults.
2. Ask whether the planner asked the right clarification questions.
3. Show the formalized theorem statement and assumptions.
4. Ask whether the formalization is faithful to the economic claim.
5. If the proof fails, show the failure explanation and next action.
6. Ask whether the workflow is more useful than manual formalization for this
   claim.

Record 1-5 ratings plus short notes:

| Metric | Rating prompt |
| --- | --- |
| Planner question quality | Did the planner ask the right economics questions? |
| Formalizer faithfulness | Did the Lean statement preserve the intended claim? |
| Assumption/default usefulness | Were assumptions explicit and economically reasonable? |
| Failure explanation clarity | If failed, was the explanation actionable? |
| Workflow value | Was this better than manual formalization for the reviewer? |

Acceptance for the first HIL pass:

- At least three completed reviewer rows.
- No release reliability claims from these rows.
- Any reviewer-reported faithfulness issue becomes a frontier queue item, not a
  release blocker unless it touches `tier1_core_preamble_definable`.

## Stronger Model Experiment Design

This is design-only for Sprint 33. A stronger prover model experiment, for
example a Goedel-Prover-V2-class backend, may run only under:

- `--budget-profile frontier`, or
- `--budget-profile research`.

Guardrails:

- Never use the release profile for stronger model experiments.
- Do not add the model to release defaults.
- Report provider, model, token usage source, estimated cost, timeout behavior,
  and failure class separately from release metrics.
- Treat provider auth, pricing uncertainty, and long Mathlib/LSP setup as
  explicit experiment risks.

Minimum experiment shape:

```bash
PYTHONPATH=. ./.venv/bin/python -m evals.local_gate \
  --benchmark-mode \
  --budget-profile frontier \
  --claim-set tier2_frontier_mathlib_native \
  --focused-sample \
  --output-dir /private/tmp/leanecon-s33-goedel-frontier \
  --allow-unready
```

Do not promote any result from this experiment into release reliability.
