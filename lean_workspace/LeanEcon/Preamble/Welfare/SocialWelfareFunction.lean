import Mathlib

/-- Utilitarian social welfare function as a weighted sum of utilities. -/
noncomputable def utilitarian_swf {n : ℕ} {X : Type*}
    (w : Fin n → ℝ) (u : Fin n → X → ℝ) (x : X) : ℝ :=
  Finset.univ.sum fun i => w i * u i x
