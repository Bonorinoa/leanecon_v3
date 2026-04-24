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
- Record enough context for later audit: claim type, target kind, LSP tool names, search query, candidate count, selected lemma, and whether compiled candidates closed the target.

## Anti-Patterns

- Long tactic inventories baked into prompts.
- Benchmark-specific recipes disguised as general strategy.
- Decomposing because the model is uncertain rather than because the theorem structure demands it.
- Spending many preamble-derived direct-close attempts on mathlib-native claims.
