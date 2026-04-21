# Econ Preamble Contribution Guide

## Purpose

The Preamble exists to provide small, reusable economic facts that survive across many claims. Add or refine entries only when they improve retrieval and formalization beyond a single benchmark.

## Principles

- Prefer one concept per file with a clear economic interpretation.
- Keep imports minimal and justified.
- Favor reusable primitives and lemmas over claim-specific wrappers.
- Treat metadata as retrieval support, not as a place for tactic scripts.
- Avoid duplication across domains; shared concepts belong in `Foundations/`.

## Lean Guidance

- State definitions and lemmas in the most general form the existing library supports.
- Use names and signatures that a planner can reuse without claim-specific context.
- Keep theorem statements close to the actual reusable mathematical fact.

## Anti-Patterns

- Do not add entries that only help one benchmark.
- Do not encode cookbook proofs, target-specific recipes, or long tactic inventories.
- Do not create broad aggregate files that hide the reusable concept boundary.
