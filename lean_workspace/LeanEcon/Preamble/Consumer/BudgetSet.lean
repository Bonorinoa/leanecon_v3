import Mathlib

/-- Budget set for two goods under a linear budget constraint. -/
def in_budget_set (p₁ p₂ m x₁ x₂ : ℝ) : Prop :=
  p₁ * x₁ + p₂ * x₂ ≤ m
