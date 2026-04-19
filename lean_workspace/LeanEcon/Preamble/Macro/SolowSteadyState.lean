import Mathlib

/-- Solow model steady-state capital per effective worker.
    At steady state: s * A * k^α = (n + g + δ) * k. -/
noncomputable def solow_investment (s A k α : ℝ) : ℝ :=
  s * A * Real.rpow k α

noncomputable def solow_depreciation (n g δ k : ℝ) : ℝ :=
  (n + g + δ) * k
