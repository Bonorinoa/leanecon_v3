import Mathlib

/-- Indirect utility for Cobb-Douglas preferences. -/
noncomputable def indirect_utility_cd (α p₁ p₂ m : ℝ) : ℝ :=
  Real.rpow (α / p₁) α * Real.rpow ((1 - α) / p₂) (1 - α) * m
