import Mathlib

/-- Marshallian demand for good 1 in a two-good Cobb-Douglas economy. -/
noncomputable def marshallian_demand_good1 (α m p₁ : ℝ) : ℝ :=
  α * m / p₁

/-- Marshallian demand for good 2 in a two-good Cobb-Douglas economy. -/
noncomputable def marshallian_demand_good2 (α m p₂ : ℝ) : ℝ :=
  (1 - α) * m / p₂
