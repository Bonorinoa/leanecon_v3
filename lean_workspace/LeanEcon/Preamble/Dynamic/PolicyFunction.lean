import Mathlib

/-- Policy correspondence selecting Bellman maximizers at each state. -/
noncomputable def policy_function {S A : Type*}
    (feasible : S → Set A) (reward : S → A → ℝ) (transition : S → A → S)
    (beta : ℝ) (V : S → ℝ) (x : S) : Set A :=
  {a | a ∈ feasible x ∧
      ∀ a' ∈ feasible x,
        reward x a' + beta * V (transition x a') ≤
          reward x a + beta * V (transition x a)}
