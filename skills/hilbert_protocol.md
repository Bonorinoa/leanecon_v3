# HILBERT Protocol

## Planner Responsibilities

- Read the claim and isolate the mathematically decisive objects and assumptions.
- Choose standard textbook defaults only when the claim leaves them implicit.
- Emit the minimum sufficient subgoals for later formalization.
- Prefer statements that reuse retrieved Preamble vocabulary directly.

## Planning Norms

- Mirror an authoritative theorem stub when one is supplied.
- Use one subgoal for direct closures, a small chain for ordinary claims, and deeper decompositions only when forced by structure.
- Keep the plan economist-facing and the subgoals Lean-facing.

## Anti-Patterns

- Padding the packet with synthetic intermediate goals.
- Inventing unsupported lemmas or symbols.
- Treating review status as a substitute for precision.
