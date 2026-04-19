import Mathlib

/-- Geometric series partial sum. -/
noncomputable def geometric_partial_sum (a r : ℝ) (n : ℕ) : ℝ :=
  a * (1 - r ^ n) / (1 - r)
