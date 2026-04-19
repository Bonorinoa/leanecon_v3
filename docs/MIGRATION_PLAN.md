# Lean Econ v3 Migration Plan

## Objective

Bootstrap the v3 clean-slate repository from the v2 reference while preserving the preamble moat and only the runtime primitives explicitly carried forward by the charter.

## Preserved From v2

- `lean_workspace/` copied verbatim.
- `src/preamble_library.py` carried forward as the Python index over the Lean preamble.
- REPL backbone: LeanInteract session wrapper, prover file controller, REPL dispatcher, compile/error helpers.
- Guardrail base: vacuity checks, REPL identifier validation, and the seed semantic checks redesigned into the v3 semantic-frame scorer.
- Observability/job flow: SQLite jobs, SSE updates, telemetry spans, tool budgets.

## Deleted Or Excluded

- Legacy planner implementations and search stack.
- MCTS, autoresearch, React/frontend surface, and prompt sprawl.
- Provider-specific v2 runtime packages not required by the chartered thin harness.
- Single-file proof residue and stray editor artifacts.

## v3 Bootstrap Layers

- `src/api`: `/plan`, `/formalize`, `/verify`, `/jobs/{id}`, `/health`, `/metrics`
- `src/planner`: HILBERT planner packet skeleton and TODO prompt spec hooks
- `src/formalizer`: backend registry, preamble selection, vacuity + faithfulness hooks
- `src/prover`: APOLLO-oriented verification harness, REPL path, tool registry integration
- `src/guardrails`: vacuity rejection, semantic-frame scorer, REPL identifier validation
- `src/memory`: SQLite episodic traces plus semantic retrieval stub
- `src/observability`: telemetry, SSE encoding, tool-budget tracking
- `src/tools`: `ToolSpec`, tool calls/results, default Lean tool registry

## Benchmarks And CI

- Canonical claim sets copied: `tier0_smoke`, `tier1_core`, `tier2_frontier`
- New claim set added: `phd_qual_alpha`
- Bootstrap local-gate runner writes scaffold baseline summaries into `benchmark_baselines/v3_alpha/`
- CI runs Lean build, focused tests, and the scaffolded local-gate threshold check

## Remaining TODOs

- Replace scaffold formalization output with model-backed faithful theorem stubs.
- Replace bootstrap local-gate summaries with live end-to-end benchmark execution.
- Flesh out APOLLO sub-lemma decomposition and semantic retrieval over episodic memory.
- Add Grok/Feynman-reviewed ontology expansion for the semantic faithfulness scorer.
