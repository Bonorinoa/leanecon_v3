# Lean 4 Proving Guidance

## Core Rules

- The Lean kernel is the trust anchor.
- Prefer direct proofs of the target over elaborate decompositions.
- Use decomposition only when the target has a real structural boundary.
- Stop repeating failed actions; let Lean feedback change the plan.
- Respect `claim_type`: preamble-definable claims should exploit LeanEcon metadata; mathlib-native claims should use LSP search/inspection instead of Preamble shortcuts.

## Reasoning Guidance

- Match the theorem statement to the strongest already-available hypothesis or lemma.
- Normalize definitions before escalating to stronger tactics.
- Keep helper lemmas small, local, and mathematically motivated.
- For mathlib-native goals, use `lean-lsp-mcp` tools early: diagnostics, active goals, code actions, hover/type context, LeanSearch, and Loogle.
- Trust the harness-supplied premises in context. The harness owns retrieval (`retrieve_premises(goal_state, k)`); the model should use the retrieved list as a search bias rather than re-querying or guessing names from memory.
- When a proof sketch is present, follow its likely-premise order before inventing theorem names. The sketch is advisory, but it encodes planner subgoals and premise conclusion overlap from the current state.
- For compact/extreme-value goals, first obtain the witness from the compact maximum/minimum premise, then refine the target existential or constrained-maximum wrapper.
- For monotone bounded convergence goals, prefer a theorem whose conclusion is already `Tendsto`; introduce an existential witness only when the active goal is existential.
- If a mathlib-native turn stalls, extract one small helper lemma from the current goal or proof sketch rather than repeating the same tactic shape.
- Record enough context for later audit: claim type, target kind, LSP tool names, search query, candidate count, selected lemma, and whether compiled candidates closed the target.

## Anti-Patterns

- Long tactic inventories baked into prompts.
- Benchmark-specific recipes disguised as general strategy.
- Decomposing because the model is uncertain rather than because the theorem structure demands it.
- Spending many preamble-derived direct-close attempts on mathlib-native claims.
