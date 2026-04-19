# Econ Preamble Contribution Guide (v3)

**Status:** Authoritative — How to Add New Entries  
**Owner:** Grok (CTO)  
**Primary User:** Codex 5.4 (with Lean4 skill in VSCode)

---

## 1. Purpose of This Document

This guide tells Codex (and future human contributors) **exactly** how to create a new Preamble entry that meets v3 quality standards. It is the operational companion to `@skills/econ_preamble_model.md`.

---

## 2. When to Create a New Entry

Create a new entry when:
- A new primitive, theorem, or concept appears in a claim that is not yet in the Preamble.
- An existing entry is too coarse and needs to be split.
- A proven lemma from a paper or textbook is missing and would improve retrieval for multiple claims.

**Never** create an entry that duplicates an existing concept in `Foundations/`.

---

## 3. Step-by-Step Process (Codex Must Follow)

### Step 1: Choose Location
Use the locked taxonomy in `@skills/econ_preamble_model.md` (Section 5).  
Example: Bellman operator → `Foundations/DynamicProgramming/BellmanOperator.lean`

### Step 2: Create the `.lean` File

**Template:**

```lean
-- BellmanOperator.lean
import Mathlib.Topology.Basic
import Mathlib.Order.Basic
-- (only minimal, justified imports — never `import Mathlib`)

open Classical

/-- The Bellman operator T associated with a dynamic programming problem
    with state space S, action space A, return function r, and transition q.
    (Economic interpretation: maps value functions to improved value functions.)
-/
def BellmanOperator {S A : Type} [TopologicalSpace S] [TopologicalSpace A]
    (r : S → A → ℝ) (q : S → A → (S → ℝ)) (β : ℝ) : (S → ℝ) → (S → ℝ) := 
  fun v s => ⨆ a, r s a + β * ∫ s' ~ q s a, v s'

/-- The Bellman operator is a contraction mapping under the sup norm
    when the discount factor β ∈ (0,1). This is Blackwell's theorem in DP form.
-/
theorem bellman_contraction (β : ℝ) (hβ : 0 < β ∧ β < 1) :
    ∀ v w : S → ℝ, ‖BellmanOperator r q β v - BellmanOperator r q β w‖ ≤ β * ‖v - w‖ := by
  -- proof here (use `linarith`, `sup_norm`, etc.)
  sorry  -- Codex must replace with actual proof

-- Additional supporting lemmas as needed
```

**Rules**:
- One primary definition or theorem per file.
- At least one proven lemma (no `sorry` in final version).
- Excellent docstring with economic interpretation.
- Minimal Mathlib imports only.
- Use `by` proofs where reasonable.

### Step 3: Create `metadata.json` (Same Folder)

Use the exact schema from `@skills/econ_preamble_model.md` (Section 6).

### Step 4: Quality Gates (Must Pass Before Commit)

1. `lake build` on the new file returns exit code 0 with **zero warnings**.
2. The entry is useful for the Planner or Formalizer (test mentally against 2–3 typical claims).
3. `metadata.json` is complete and valid JSON.
4. No duplication of concepts already in `Foundations/`.

### Step 5: Commit Message Format

```
feat(preamble): add BellmanOperator + metadata (Foundations/DynamicProgramming)
```

---

## 4. First Rebuild Task (t=0)

**Codex must execute this exact sequence on the very first run after `lake build` finishes:**

1. Delete the entire contents of `lean_workspace/LeanEcon/Preamble/` (rm -rf everything inside).
2. Create the new directory tree from `@skills/econ_preamble_model.md` Section 5.
3. Populate the first 8–12 highest-leverage entries in `Foundations/` (see recommended first sprint in Section 5 below).
4. Run `lake build` and fix any issues.
5. Commit with message: `feat(preamble): v3 rebuild — Foundations/ skeleton + first 12 entries`

---

## 5. Recommended First Sprint (Highest Leverage)

**Week 1 Priority (Foundations/ only)**

1. `Foundations/Primitives/Measure.lean` + metadata
2. `Foundations/Primitives/TopologicalSpace.lean` + metadata
3. `Foundations/Preferences/ContinuousPreference.lean` + metadata
4. `Foundations/Preferences/ConvexPreference.lean` + metadata
5. `Foundations/Optimization/ConstrainedOptimization.lean` + metadata
6. `Foundations/Optimization/KuhnTucker.lean` + metadata
7. `Foundations/Equilibrium/FixedPointTheorem.lean` + metadata
8. `Foundations/Equilibrium/NashExistence.lean` + metadata
9. `Foundations/DynamicProgramming/BellmanOperator.lean` + metadata
10. `Foundations/DynamicProgramming/ContractionMapping.lean` + metadata
11. `Foundations/DynamicProgramming/ValueFunction.lean` + metadata
12. `Foundations/DynamicProgramming/PolicyIteration.lean` + metadata

These 12 entries give the Planner and Formalizer immediate power for the majority of dynamic + optimization claims.

---

## 6. Governance

- All changes to `Foundations/` require explicit approval from Grok (CTO) before commit.
- New entries in applied domains (`Microeconomics/`, etc.) can be proposed by Codex but must still pass the quality gates above.
- Once 50+ entries exist, we will add an automated linter that checks metadata completeness and import minimality.

---

**This document + `@skills/econ_preamble_model.md` give Codex everything it needs for a perfect one-shot rebuild.**