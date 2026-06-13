# Sprint 26 Baseline

**Status:** Current baseline  
**Purpose:** Record the state from which Sprints 27-30 should proceed.

## Current Reality

Sprint 26 added an explicit prover state machine and documentation in `docs/PROVER_STATE_MACHINE.md`. The state machine is valuable for orchestration and observability, but several fields are not yet normative execution controls.

Important Sprint 26 baseline facts:

- `allowed_tools`, `max_tool_calls`, and `allow_decompose` are visible in `StateConfig` but not consistently enforced.
- The prover execution loop remains concentrated in a large `src/prover/execution.py` mixin.
- The Lean LSP bridge is lazy at import time, and tests cover the client with fake subprocess streams or injected fake clients rather than spawning `uvx`.
- CI is deterministic for the scaffold gate: `evals.local_gate --limit 0` runs without live model providers, credentials, network, or `uvx`, and writes to a temporary output directory.
- The preamble is kernel-backed and useful, but it is mostly a thin wrapper layer around Mathlib facts plus economics-facing names.

## Baseline Strengths

- Lean kernel remains the final authority.
- Unit test coverage around prover, state machine, local gate, retrieval, and observability is broad.
- The architecture already distinguishes `preamble_definable` from `mathlib_native`.
- Benchmark traces already include useful observability fields.
- The current docs are unusually candid about mathlib-native limits.

## Baseline Risks

- Runtime behavior is harder to reason about than the architecture implies.
- Execution-state fields can drift from prompt guidance.
- Full mathlib-native runs can still require explicit LSP availability, but unit tests and the CI scaffold should not.
- Benchmark and scaffold commands can mutate checked-in baseline files if run without an explicit `--output-dir`.
- Deployment docs assume a hosted setup that currently does not exist.

## Sprint 26 Exit Gate

Sprint 26 should be considered complete when:

- all current tests pass,
- CI no longer fails due to missing `uvx`,
- local-gate scaffold is deterministic in CI and writes outside checked-in benchmark baselines,
- Sprint 27 technical plan is explicit.

## Handoff To Sprint 27

Sprint 27 should not add new agentic capability. It should make the existing capability easier to trust.

The right first epic is:

> Make prover orchestration deterministic, state-enforced, and dependency-injected.

## Sprint 27A Handoff Correction

Sprint 27A closed the first handoff gap without expanding proving capability:
`StateConfig.allowed_tools`, `max_tool_calls`, and `allow_decompose` now control
prompt-visible tools, executable tool dispatch, and decomposition gates. Lean LSP
also has explicit ready/disabled/unavailable status and a disabled client path
for deterministic tests.
