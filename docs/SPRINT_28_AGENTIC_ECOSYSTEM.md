# Sprint 28 Plan: Agentic Ecosystem On Stable Rails

**Theme:** Planner-formalizer-prover contracts and preamble-backed reliability.  
**Primary outcome:** Non-frontier undergraduate mathematical economics claims reliably route through `preamble_definable` mode.

## Goal

Sprint 28 should improve the agentic pipeline using the technical foundation from Sprint 27.

The core goal is:

> Make the system reliably distinguish in-scope claims from frontier claims and route in-scope claims through stable preamble-backed workflows.

## Workstreams

### 1. Scope Classifier

Add an explicit scope classification layer.

Target classes:

- `release_reliable`,
- `supported_attempt`,
- `frontier_collect`,
- `out_of_scope`.

Classification inputs:

- raw claim,
- planner assumptions,
- selected preamble entries,
- theorem shape,
- missing definitions or lemmas,
- claim type,
- parse and proof feedback.

Target result:

- claims are categorized before pass rates are interpreted.

### 2. Stronger Planner Contract

Planner output should become less prose-heavy and more operational.

Add or strengthen fields such as:

- claim tier,
- claim type,
- required primitives,
- selected preamble entries,
- theorem-shape recommendation,
- assumption audit,
- proof strategy,
- reason if frontier or out of scope.

Target result:

- the formalizer receives a contract, not just a paragraph.

### 3. Template-First Formalizer

For preamble-backed claims, formalizer should prefer known theorem templates and metadata.

Deliverables:

- template selection from preamble metadata,
- generated theorem statements that preserve assumptions,
- stricter faithfulness check for supplied templates,
- fallback to model generation only when template path is unavailable.

Target result:

- common undergraduate claims are formalized predictably.

### 4. Preamble Metadata Consistency

Make the preamble contract testable.

Deliverables:

- every registered preamble entry has an existing Lean file,
- every metadata JSON file is discoverable from the registry or intentionally orphaned,
- every listed definition and lemma is present in Lean source or Lean outline,
- tactic hints are smoke-tested where feasible.

Target result:

- the preamble is a reliable API, not a loose prompt library.

### 5. Failure Classification And Frontier Capture

Every failed claim should produce a useful next-action label.

Target labels:

- add preamble definition,
- add preamble theorem,
- improve formalizer template,
- improve planner assumptions,
- improve proof search,
- add retrieval premise,
- collect for fine-tuning,
- mark out of scope.

Target result:

- failures feed the roadmap.

## Sprint 28 Non-Goals

- Do not chase broad mathlib-native pass-rate improvements as the main metric.
- Do not redeploy.
- Do not expand the preamble without a trace-backed reason.

## Sprint 28 Exit Gate

- Scope classification appears in API/job results and benchmark summaries.
- Release-reliable claims are separated from frontier claims in metrics.
- Preamble metadata consistency tests pass.
- Formalizer template path works for representative preamble-backed claims.
- Frontier queue artifacts are written in a documented format.

## Sprint 28 Implementation Status

Sprint 28 adds a small, explicit contract layer around existing planner,
formalizer, prover, and benchmark code without expanding the economics preamble
beyond narrow metadata templates for already-proven entries.

Implemented surfaces:

- `src/claim_scope.py` defines the stable scope classes:
  `release_reliable`, `supported_attempt`, `frontier_collect`, and
  `out_of_scope`.
- Planner packets now expose operational fields:
  `claim_scope`, `claim_type`, `required_primitives`,
  `theorem_shape_recommendation`, `assumption_audit`, and `scope_reason`.
- Formalizer contexts and packets carry the same fields plus
  `formalization_source`, so downstream traces distinguish
  `theorem_stub`, `preamble_template`, and `llm_generation`.
- Prover results expose `claim_scope`, `claim_type`, `failure_class`, and
  `recommended_next_action`.
- Local-gate and benchmark summaries include `claim_scope_counts`,
  `metrics_by_scope`, `release_reliable_metrics`, and `frontier_metrics`.
- Local-gate writes frontier queue artifacts as
  `<claim_set>.frontier_queue.jsonl` and `local_gate.frontier_queue.jsonl` in
  the explicit output directory selected for the run.
- Benchmark markdown reports include a scope-separated metrics table.

Frontier records contain:

- `claim_id`,
- `raw_claim`,
- `scope_classification`,
- `scope_reason`,
- `claim_type`,
- `selected_preamble_entries`,
- `required_primitives`,
- `theorem_shape_recommendation`,
- `assumption_audit`,
- `lean_statement`,
- `parse_result`,
- `proof_result`,
- `failure_class`,
- `recommended_next_action`,
- `failure_reason`.

Failure next-action labels are:

- `add_preamble_definition`,
- `add_preamble_theorem`,
- `improve_formalizer_template`,
- `improve_planner_assumptions`,
- `improve_proof_search`,
- `add_retrieval_premise`,
- `collect_for_finetuning`,
- `mark_out_of_scope`.

Template-first formalization now uses preamble metadata before model generation
when a claim is classified as `release_reliable` and the selected preamble entry
has a theorem template. Sprint 28 adds narrow metadata templates for existing
proven entries:

- `monotone_sequence`,
- `constrained_optimization`,
- `best_response`.

Preamble metadata consistency tests now check:

- every registered preamble entry has an existing Lean file,
- every registered definition and proven lemma is present in source,
- every adjacent `*.metadata.json` file is linked to a registry entry,
- metadata-listed proven lemmas match registry entries or source.

## Codex Goal Prompt Draft

```text
/goal Complete Sprint 28 agentic ecosystem improvements for LeanEcon v3.

Assume Sprint 27 technical foundation is complete. Focus on planner-formalizer-prover contracts, scope classification, preamble metadata reliability, and frontier data capture.

Primary objectives:
1. Add an explicit claim scope classifier with release_reliable, supported_attempt, frontier_collect, and out_of_scope categories.
2. Strengthen planner output/metadata so the formalizer receives operational contract fields, not only prose.
3. Implement or improve template-first formalization for preamble-backed claims.
4. Add full preamble metadata consistency tests across Lean files, JSON metadata, and src/preamble_library.py.
5. Add structured failure classification and frontier queue output for failed or out-of-scope claims.
6. Update docs and benchmark summaries to separate reliable-surface metrics from frontier metrics.

Constraints:
- Do not make frontier mathlib-native pass rate the release metric.
- Keep current successful preamble-definable behavior intact.
- Make failures more informative even if pass rate does not immediately improve.

Before final response:
- Run focused planner/formalizer/prover/local_gate tests.
- Run metadata consistency tests.
- Run full pytest if feasible.
- Report any tests not run.
```
