# Lean 4 Proving Guidance

## Core Rules

- The Lean kernel is the trust anchor.
- Prefer direct proofs of the target over elaborate decompositions.
- Use decomposition only when the target has a real structural boundary.
- Stop repeating failed actions; let Lean feedback change the plan.

## Reasoning Guidance

- Match the theorem statement to the strongest already-available hypothesis or lemma.
- Normalize definitions before escalating to stronger tactics.
- Keep helper lemmas small, local, and mathematically motivated.

## Anti-Patterns

- Long tactic inventories baked into prompts.
- Benchmark-specific recipes disguised as general strategy.
- Decomposing because the model is uncertain rather than because the theorem structure demands it.
