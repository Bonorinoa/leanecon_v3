import Mathlib

open scoped BigOperators

/-- Aggregate excess demand in a one-good exchange economy. -/
noncomputable def excess_demand {ι : Type*} [Fintype ι]
    (demand : ι → ℝ → ℝ) (endowment : ι → ℝ) (p : ℝ) : ℝ :=
  ∑ i, (demand i p - endowment i)
