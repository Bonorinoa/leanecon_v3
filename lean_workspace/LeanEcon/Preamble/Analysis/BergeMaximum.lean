import Mathlib

/-- Berge maximum theorem setup: feasible correspondence, objective, and
    regularity conditions sufficient to discuss continuity and argmax behavior. -/
structure berge_maximum {X Y : Type*} [TopologicalSpace X] [TopologicalSpace Y] where
  feasible : X → Set Y
  objective : X → Y → ℝ
  feasible_uhc : UpperHemicontinuous feasible
  objective_continuous : Continuous fun xy : X × Y => objective xy.1 xy.2
