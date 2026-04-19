import Mathlib

/-- Two-factor CES production function. -/
noncomputable def ces_production (A K L σ α : ℝ) : ℝ :=
  A * Real.rpow
    (α * Real.rpow K ((σ - 1) / σ) + (1 - α) * Real.rpow L ((σ - 1) / σ))
    (σ / (σ - 1))
