# Lean 4 Proving Playbook (v3)

## Core Rules

1. Lean kernel is the only trust anchor.
2. Use the REPL path first; materialize files only for checkpointing and final verification.
3. Route all proving actions through the `ToolSpec` registry in `src/tools/registry.py`.
4. Stop repeating the same tactic after two failures and record the failed trace in memory.

## Preferred Openings

- Direct hypothesis goals: `exact h`
- Definitional identities: `simpa [def_name]`
- Ring/field algebra: `field_simp`, then `ring`
- Arithmetic normalization: `norm_num`
- Routine structural goals: `simp`, `aesop`

## APOLLO TODOs

- Extract sub-lemmas before long proofs.
- Attach tactic traces to episodic memory for later retrieval.
- Add targeted recipes for Bellman, Walras, Roy, and single-crossing claims after Grok/Feynman review.
