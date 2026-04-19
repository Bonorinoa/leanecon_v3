import Mathlib

/-- Pareto dominance for a finite economy. -/
def pareto_dominates {n : ℕ} {X : Type*}
    (u : Fin n → X → ℝ) (x y : X) : Prop :=
  (∀ i, u i x ≤ u i y) ∧ (∃ i, u i x < u i y)

/-- Pareto efficiency for a finite economy. -/
def pareto_efficient {n : ℕ} {X : Type*}
    (u : Fin n → X → ℝ) (feasible : Set X) (x : X) : Prop :=
  x ∈ feasible ∧ ∀ y, y ∈ feasible → ¬pareto_dominates u x y
