import Mathlib

/-- Stone-Geary utility for two goods. -/
noncomputable def stone_geary_utility (x₁ x₂ α γ₁ γ₂ : ℝ) : ℝ :=
  α * Real.log (x₁ - γ₁) + (1 - α) * Real.log (x₂ - γ₂)
