import Mathlib

open scoped BigOperators

/-- Finite-dimensional Lagrangian for inequality-constrained optimization. -/
noncomputable def lagrangian {n m : ℕ}
    (objective : (Fin n → ℝ) → ℝ)
    (constraints : Fin m → (Fin n → ℝ) → ℝ)
    (x : Fin n → ℝ) (lam : Fin m → ℝ) : ℝ :=
  objective x - ∑ i, (lam i * constraints i x)
