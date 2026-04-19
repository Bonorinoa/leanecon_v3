import Mathlib

/-- Single-crossing property on ordered real decisions and parameters. -/
def single_crossing (f : ℝ → ℝ → ℝ) : Prop :=
  ∀ {x₁ x₂ t₁ t₂ : ℝ}, x₁ ≤ x₂ → t₁ ≤ t₂ →
    f x₁ t₂ - f x₁ t₁ ≤ f x₂ t₂ - f x₂ t₁
