# Prover State Machine

## High-Level Summary

LeanEcon v3 now has an explicit finite-state machine for the mathlib-native
prover path. The implementation is intentionally small: it tracks the prover's
current strategic state, validates allowed transitions, exposes per-state
configuration, and emits state context into prompts, memory retrieval, progress
events, and result traces.

The state machine is useful today as an orchestration, observability, and
execution-policy layer. It changes prompt guidance and memory example selection
for recovery states, records when the harness enters stall recovery,
unknown-identifier rescue, decomposition, verification, or failure, and enforces
the active `StateConfig` at the model/tool boundary.

## Current Capabilities

### States

- `Synthesizing`: Default state for normal proof search. It allows broad memory
  retrieval, full tool availability, and decomposition when other gates allow it.
- `Stalled`: Recovery state entered after progress tracking reports an unchanged
  mathlib-native state and the second-retrieval gate permits recovery work. It
  focuses prompt guidance on diagnosing the current goal and recent failure, and
  switches memory retrieval to failure-focused examples.
- `Decomposing`: Helper-lemma/subgoal state entered from `Stalled` when helper
  lemma extraction starts. It switches prompt and memory context toward subgoal
  decomposition.
- `Rescue`: Narrow one-shot recovery state entered after recent trace analysis
  detects an unknown identifier and constructs a rescue LeanSearch query. It
  uses rescue-focused prompt rules and a narrow helper-memory strategy.
- `Verified`: Terminal success state entered after local/final Lean validation
  confirms the proof.
- `Failed`: Terminal failure state entered after mathlib-native proving fails or
  final compile validation fails.

### StateConfig

`StateConfig` lives in `src/prover/state_machine.py` and is available through
`get_state_config(state)` and `Prover.current_state_config`.

Each config contains:

- `allowed_tools`: Tool allowlist for the state. Prompt-visible tools are built
  from this list, and `_execute_tool()` rejects disallowed model-requested tools
  before budget accounting.
- `prompt_rules`: Mode and natural-language guidance for prompt synthesis.
- `memory_filter`: Strategy used by `ProverSynthesisMixin._memory_examples`.
- `max_tool_calls`: Per-state tool-call budget, counted from the moment the
  state is entered and mapped onto the existing `BudgetTracker.tool_history`.
  `None` preserves legacy unrestricted behavior.
- `allow_decompose`: Decomposition permission for the state. Caller-level
  `allow_decomposition` must also be true.
- `terminal`: Whether the state is terminal.

`StateConfig.copy()` protects the canonical config table from caller mutation.
`StateConfig.to_dict()` produces the JSON-ready form used by prover metadata,
progress events, and traces.

Today, all `StateConfig` fields are active except terminal states still rely on
the existing transition/finalization paths to prevent further prover work.

### Transitions

Transitions are validated by `StateMachine.transition()`. Re-entering the same
state is treated as a no-op. Invalid transitions raise `ValueError`; prover
integration usually calls `_try_transition_prover_state()`, which silently keeps
the current state when the requested edge is not valid.

Allowed transitions:

- `Synthesizing -> Stalled | Rescue | Verified | Failed`
- `Stalled -> Synthesizing | Decomposing | Rescue | Failed`
- `Decomposing -> Verified | Failed`
- `Rescue -> Synthesizing | Failed`
- `Verified` and `Failed` are terminal

Important trigger points:

- New prove run: `_reset_prover_state()` resets to `Synthesizing`.
- Progress stall: `_enter_mathlib_stalled_state()` enters `Stalled` when a
  `ProgressDelta` reports no useful state change.
- Successful progress after stall: `_recover_mathlib_stall()` returns to
  `Synthesizing`.
- Helper lemma extraction after stall: `_recover_mathlib_stall(...,
  next_state=ProverState.Decomposing)` enters `Decomposing`.
- Unknown identifier rescue: `_enter_mathlib_rescue_state()` enters `Rescue`;
  `_recover_mathlib_rescue()` returns to `Synthesizing` after rescue retrieval.
- Successful validation: mathlib-native proof validation transitions to
  `Verified`.
- Failure normalization/final failure: mathlib-native failure transitions to
  `Failed`.

### Prompt And Memory Flow

Generic prompt synthesis uses `_build_prompt(..., current_state,
state_config)`. The default `Synthesizing` state intentionally keeps the legacy
prompt shape. Non-default states add `state_context` plus instruction fields
such as `state_prompt_rules`, `state_memory_filter`, and state-specific
guidance.

Prompt-visible tool specs flow through `ProverSynthesisMixin._tool_specs_for_prompt()`.
That builder applies claim-type filtering and the current state's `allowed_tools`
from the same policy used by `_execute_tool()`, so hidden tools and executable
tools cannot drift.

The mathlib-native harness prompt uses the same state-context helper. For
non-default states, state guidance is appended to the harness rules. This keeps
the normal prompt compact while making recovery/decomposition/rescue visible to
the model.

Memory selection flows through `ProverSynthesisMixin._memory_examples()`:

- `broad`: similar verified traces, with mathlib-helper fallback.
- `failure_focused`: similar traces regardless of outcome, ranked toward
  failed/repair-heavy examples.
- `subgoal_focused`: mathlib helper traces first, broad fallback.
- `rescue_identifier`: one mathlib helper trace first, verified fallback.
- `none`: no memory examples.

### Observability

State metadata is emitted through `Prover._prover_state_metadata()` as:

- `current_state`
- `current_state_config`

The metadata appears in:

- `ProverStateTransition` payloads in `_prover_state_transitions`.
- Progress callback events, including the dedicated
  `prover_state_transition` event.
- Generic `_emit_progress()` metadata for prover progress events.
- `SynthesisEvent` payloads emitted from both legacy and mathlib-native paths.
- Final `ProverResult` fields such as `prover_state_transitions`,
  `synthesis_events`, `state_transitions`, `progress_deltas`, and
  `tool_usage_traces`.

`src/observability/models.py` includes `ProverStateTransition` and
state-aware `SynthesisEvent` fields. Tests cover the expected event shapes.

## Simplifications Completed

- Centralized state config serialization in `StateConfig.to_dict()`.
- Updated stale `StateConfig` wording to reflect current prompt, memory, and
  observability integration.
- Reused `StateConfig.to_dict()` in `Prover._prover_state_metadata()` so config
  metadata has one source of truth.
- Added this documentation artifact for sprint planning and future onboarding.

No dead files were found in the analyzed state-machine surface.

## Rough Edges

- `_try_transition_prover_state()` suppresses invalid edges. That is useful for
  instrumentation-only integration, but it can hide transition mistakes unless
  tests or logs catch them.
- `Decomposing` has no transition back to `Synthesizing`. That matches the
  current matrix, but it means decomposition either verifies or fails at the
  state-machine level even if the broader harness resumes normal search.
- State prompt context intentionally omits `allowed_tools`; allowed tools are
  communicated through the actual prompt tool list instead.
- There are multiple synthesis-event creation sites that repeat current-state
  metadata plumbing. This is manageable now but should be centralized if more
  event types gain state context.

## Suggested Next-Sprint Priorities

1. Clarify the intended `Decomposing` lifecycle. Either keep it terminal-like
   after helper extraction or add an explicit recovery edge if normal synthesis
   should resume.
2. Consider logging blocked invalid transitions in `_try_transition_prover_state`
   so instrumentation remains forgiving without making state drift invisible.
3. Centralize synthesis-event state metadata if more event types gain state
   context.
