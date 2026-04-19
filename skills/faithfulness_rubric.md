# Semantic Faithfulness Rubric (v3)

## Current Runtime

`src/guardrails/semantic_frame.py` implements the bootstrap scorer.

Weighted dimensions:

- Concept coverage: 30%
- Structural isomorphism: 25%
- Primitive faithfulness: 20%
- Tactic alignment placeholder: 15%
- Human review override: 10%

## Gate Policy

- `< 4.0`: reject
- `4.0 - 4.49`: require human review
- `>= 4.5`: passes automatic gate

## TODOs For Grok/Feynman

- Replace placeholder concept extraction with an econ ontology.
- Upgrade quantifier and structural parsing from regex to Lean-aware structure.
- Add reviewer override logging into episodic memory.
