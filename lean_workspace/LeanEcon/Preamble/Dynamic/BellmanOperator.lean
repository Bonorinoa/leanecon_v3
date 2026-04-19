import Mathlib

/-- Bellman operator for deterministic dynamic programming with a feasible
    action correspondence. -/
noncomputable def bellman_operator {S A : Type*}
    (feasible : S → Set A) (reward : S → A → ℝ) (transition : S → A → S)
    (beta : ℝ) (V : S → ℝ) (x : S) : ℝ :=
  sSup ((fun a => reward x a + beta * V (transition x a)) '' feasible x)
