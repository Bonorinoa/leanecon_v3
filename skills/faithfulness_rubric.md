# Semantic Faithfulness Rubric

## What Matters

- Concept coverage: the formal stub should reflect the economic objects named in the claim.
- Structural alignment: the stub should preserve the logical shape of the claim rather than replacing it with a nearby but easier theorem.
- Primitive faithfulness: imported definitions and lemmas should match the intended mathematical content.

## Gate

- Reject tautological, vacuous, or concept-dropping rewrites.
- Require human review when the formalization narrows or broadens the claim in a material way.
- Auto-pass only when the stub is recognizably the same claim in Lean form.

## Anti-Patterns

- Replacing the claim with `True`, `False`, or a generic witness statement.
- Smuggling in stronger assumptions not present in the claim or stub.
- Using a proof-friendly surrogate theorem when the target theorem is already available.
