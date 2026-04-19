# Econ Preamble Model (v3)

## Purpose

The Lean preamble is the moat. `src/preamble_library.py` is only an index over the Lean source of truth in `lean_workspace/LeanEcon/Preamble/`.

## Retrieval Shape

Formalizer context should be injected in four blocks:

1. Definitions
2. Proven lemmas
3. Theorem template hints
4. Tactic hints

## v3 Runtime Hooks

- `src/formalizer/service.py` selects preamble entries from the library metadata.
- `src/memory/store.py` records which preamble entries actually contributed to successful proofs.
- `src/guardrails/semantic_frame.py` treats preamble imports as evidence for primitive faithfulness.

## TODOs

- Add richer per-entry metadata files once the Grok/CTO review locks the JSON schema.
- Replace lexical preamble selection with hybrid keyword + embedding retrieval.
