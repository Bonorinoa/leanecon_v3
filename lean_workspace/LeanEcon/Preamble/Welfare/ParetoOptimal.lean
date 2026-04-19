import Mathlib
import LeanEcon.Preamble.Welfare.ParetoEfficiency

/-- Pareto optimality as the economist's name for Pareto efficiency. -/
def pareto_optimal {n : ℕ} {X : Type*}
    (u : Fin n → X → ℝ) (feasible : Set X) (x : X) : Prop :=
  pareto_efficient u feasible x
