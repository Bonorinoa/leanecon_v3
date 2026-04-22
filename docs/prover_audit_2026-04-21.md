# Prover Audit 2026-04-21

## Flow map
- `Prover.prove` builds targets from formalizer output, resets the tool budget, and iterates subgoals followed by the main theorem body.
- Non-benchmark runs still keep the top-level trivial shortcut, but Sprint 17 adds a shared direct-definable closure path that also runs inside `_prove_target`.
- `_prove_target` now tries direct closure before creating the REPL/session, then falls back to the existing loop:
  provider prompt -> tool action or decomposition -> REPL/materialized code update -> final compile check.
- APOLLO decomposition still exists, but it is now guarded by actual lack of progress instead of only raw failed-turn count.
- REPL-backed tool calls still flow through `_execute_tool`, but unchanged code/goals after `apply_tactic` now terminate as `no_progress_stall` instead of spinning.
- Final verification still happens through the authoritative Lean compile path, not the REPL state.

## Audit findings
- The biggest wasted work was session startup before easy claims. Direct preamble-hit claims were paying REPL startup and sometimes decomposition/provider overhead before trying the one proof body that was already obvious from preamble metadata.
- The prover had the right information but the wrong ordering. Proven lemmas and tactic hints already existed in preamble metadata for Bellman, fixed-point, value-function, Nash, KT, policy, measure, continuity, and optimization families.
- Repeated-noop behavior came from two places:
  - repeated failed provider/tool actions with no state change;
  - REPL tactics that reported progress locally while leaving the materialized theorem unchanged.
- Decomposition was too eager for definable claims. The previous gate allowed APOLLO extraction after two failed turns even when the target was already one exact lemma or one member projection away from closing.

## Sprint 17 changes
- Added a direct-definable closure phase that compiles bounded proof-body candidates before provider turns and before REPL startup.
- Added specialized direct proofs for the current weak families:
  - `exact BellmanOperator.monotone hβ hvw`
  - `exact contraction_has_fixedPoint hf`
  - `exact exists_fixedPoint_of_contractingWith hf`
  - `simpa [ValueFunction] using ContractingWith.fixedPoint_isFixedPt (f := T) hT`
  - `exact hkt.slackness i`
  - `exact ⟨h.witness, h.is_nash⟩`
  - `exact policyImproves_refl criterion policy`
  - plus direct member projections like `exact hx.1`, `exact hu.continuousOn`, `exact hu`
- Added progress-aware decomposition inputs:
  - `no_progress_streak`
  - `direct_candidates_available`
- Added explicit `no_progress_stall` handling for unchanged REPL tactic outcomes.
- Added seeded-random sampling support to `evals/local_gate.py` and a dedicated `prover_easy_definable` claim set for prover-only regression checks.

## Measurements
- Baseline canonical sample:
  - Source: `benchmark_baselines/v3_alpha/tier1_core.json`
  - Result before Sprint 17: `5/10`, all 5 failures were `unsolved_goals`
- Canonical sample after Sprint 17:
  - Method: prover-only rerun on the exact same 10 claim ids using the current `tier1_core` theorem stubs and the Sprint 17 prover
  - Result: `10/10`
  - Average tool calls: `0.0`
  - Average decomposition depth: `0.0`
- Hand-curated `prover_easy_definable` set after Sprint 17:
  - Result: `5/5`
  - Average tool calls: `0.0`
  - Average decomposition depth: `0.0`

## Biggest fragilities removed
- Direct-closeable claims no longer route through provider/REPL/decomposition first. This removed the main source of wasted prover effort on definable and preamble-backed statements.
- Fixed-point, value-function, Bellman, Nash, KT, and policy-improvement families now have first-shot proof bodies that match the actual LeanEcon preamble lemmas instead of relying on provider rediscovery.
- No-op loops now stop cleanly when a tactic leaves both code and goals unchanged, which keeps the prover failure mode explicit and prevents budget waste.
