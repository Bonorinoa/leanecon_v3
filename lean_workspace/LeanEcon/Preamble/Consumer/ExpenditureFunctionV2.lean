import Mathlib

/-- Expenditure as the infimum cost of reaching a target utility level. -/
noncomputable def expenditure_function_v2 {X : Type*}
    (expenditure : X → ℝ) (u : X → ℝ) (target : ℝ) : ℝ :=
  sInf (expenditure '' {x | target ≤ u x})
