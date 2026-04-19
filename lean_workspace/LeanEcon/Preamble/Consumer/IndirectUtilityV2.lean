import Mathlib

/-- Indirect utility as the supremum of utility over the budget correspondence. -/
noncomputable def indirect_utility_v2 {X : Type*}
    (u : X → ℝ) (budget : ℝ → ℝ → Set X) (p w : ℝ) : ℝ :=
  sSup (u '' budget p w)
