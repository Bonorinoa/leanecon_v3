import Mathlib
import LeanEcon.Preamble.Welfare.ParetoOptimal

/-- First welfare theorem setup: every competitive equilibrium allocation is
    Pareto optimal. -/
def first_welfare_thm {n : ℕ} {X : Type*}
    (competitiveEquilibrium : X → Prop)
    (u : Fin n → X → ℝ) (feasible : Set X) (x : X) : Prop :=
  competitiveEquilibrium x → pareto_optimal u feasible x
