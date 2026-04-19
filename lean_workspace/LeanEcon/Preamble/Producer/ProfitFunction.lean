import Mathlib

/-- Profit function for a single-input firm. -/
noncomputable def profit (p w A α : ℝ) (x_star : ℝ) : ℝ :=
  p * (A * Real.rpow x_star α) - w * x_star
