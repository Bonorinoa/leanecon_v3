import Mathlib

/-- KKT conditions for finite inequality constraints. Stationarity is supplied
    as a separate proposition so the definition stays lightweight. -/
def kkt_conditions {n m : ℕ}
    (stationarity : Prop)
    (constraints : Fin m → (Fin n → ℝ) → ℝ)
    (lam : Fin m → ℝ) (x : Fin n → ℝ) : Prop :=
  stationarity ∧
  (∀ i, constraints i x ≤ 0) ∧
  (∀ i, 0 ≤ lam i) ∧
  ∀ i, lam i * constraints i x = 0
