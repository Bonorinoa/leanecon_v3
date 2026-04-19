# Econ Preamble Model (v3)

**Status:** Authoritative Design Document — Locked 19 April 2026  
**Owner:** Grok (CTO)  
**Readers:** Human experts + Codex 5.4 + Feynman

---

## 1. Purpose

The Preamble is **Lean Econ’s core knowledge moat** — a versioned, metadata-rich, retrievable library of economic primitives and theorems that the Planner, Formalizer, and Prover can reliably draw upon.

It is **not** a dump of textbook excerpts. It is a carefully engineered, machine-actionable knowledge base designed for:
- High-precision retrieval by the Formalizer (structured context injection)
- Few-shot reasoning by the Planner (HILBERT-style)
- Long-term compounding via Memory traces

---

## 2. Design Principles (What We Stand For)

1. **Micro-foundations first**  
   Modern economics is built on shared primitives. We organize `Foundations/` as the single source of truth for concepts that appear across micro, macro, GE, and game theory (dynamic programming, optimization, equilibrium, preferences, etc.).

2. **One concept per file**  
   Granular entries beat monolithic files. Each `.lean` file contains exactly one primary definition/theorem + supporting lemmas.

3. **Metadata is first-class**  
   Every entry ships with a complete `metadata.json`. Retrieval and context building are driven by the `concepts` array and embeddings — not by folder names.

4. **Human + machine excellence**  
   The structure must be intuitive for PhD students and researchers to browse, while being perfectly machine-readable for LLMs.

5. **Quality over quantity**  
   Every entry must be kernel-validated, have at least one proven lemma, include tactic hints, and cite a canonical textbook source.

6. **No duplication**  
   Cross-cutting concepts (especially DynamicProgramming, Equilibrium, Optimization) live once in `Foundations/` and are imported everywhere else.

---

## 3. What We Have Deliberately Chosen NOT to Do

- **We do not** follow a pure textbook chapter structure (MWG Ch. 1–18, SLP Ch. 1–17). That would create massive duplication and poor retrieval.
- **We do not** put everything under “Microeconomics/” even though modern macro is micro-founded. We give GeneralEquilibrium its own top-level home because it has unique existence/uniqueness/computation literature.
- **We do not** rely on folder names for retrieval. Folders are for human navigation only.
- **We do not** allow entries without `metadata.json`. No exceptions.
- **We do not** import the entire `Mathlib` in any Preamble file. Only minimal, justified imports.
- **We do not** accept entries that are only definitions with no proven lemma.
- **We do not** duplicate DynamicProgramming, Optimization, or Equilibrium concepts across domains.

---

## 4. Current State at t=0 (19 April 2026)

**At the moment this document is written, the Preamble contains the old v2 structure** (11 aggregated domain files from the previous taxonomy).

**The very first action Codex 5.4 must take** after the `lake build` completes is:

1. **Delete everything** inside `lean_workspace/LeanEcon/Preamble/` (including all old `.lean` files and folders).
2. Create the new directory structure exactly as defined in Section 5.
3. Populate the first batch of high-leverage entries in `Foundations/` according to the contribution rules in `@skills/econ_preamble_contribution.md`.

From that point forward, the Preamble is considered a **fresh v3 artifact**. We never carry forward the old aggregated files.

---

## 5. Final Locked Taxonomy & Directory Structure

```
lean_workspace/LeanEcon/Preamble/
├── Foundations/
│   ├── Primitives/
│   ├── Preferences/
│   ├── Optimization/
│   ├── Equilibrium/
│   └── DynamicProgramming/
├── Microeconomics/
│   ├── ConsumerTheory/
│   ├── ProducerTheory/
│   ├── PartialEquilibrium/
│   └── WelfareEconomics/
├── Macroeconomics/
│   ├── GrowthModels/
│   ├── BusinessCycleModels/
│   ├── MonetaryEconomics/
│   └── FiscalPolicy/
├── GameTheory/
│   ├── NormalFormGames/
│   ├── ExtensiveFormGames/
│   ├── RepeatedGames/
│   └── MechanismDesign/
├── GeneralEquilibrium/
│   ├── ArrowDebreu/
│   ├── ExistenceUniqueness/
│   └── ComputationApproximation/
└── Tools/
```

**Leaf folders** contain:
- One `.lean` file per concept
- One `metadata.json` per concept

---

## 6. Metadata Schema (Mandatory)

Every entry must have a `metadata.json` with exactly these fields:

```json
{
  "id": "bellman_operator",
  "domain": "dynamic_programming",
  "concepts": ["bellman_operator", "value_function", "contraction_mapping"],
  "tactic_hints": ["apply contraction_mapping", "simp [continuous, tendsto]"],
  "proven_lemmas": ["bellman_contraction", "value_iteration_converges"],
  "textbook_source": "SLP Ch. 4",
  "status": "proven",
  "related": ["value_function", "policy_iteration"]
}
```

---

## 7. Success Criteria for v3.0 Alpha

- 120+ entries by end of alpha
- 100 % of entries have complete `metadata.json`
- 100 % pass `lake build` with zero warnings
- Hybrid retrieval (concepts + embeddings) achieves ≥ 0.85 precision@5 on 50 test claims
- Every major domain (Foundations + 4 applied) has at least 15 entries

---

This document + `@skills/econ_preamble_contribution.md` together contain everything Codex needs to execute the rebuild perfectly.