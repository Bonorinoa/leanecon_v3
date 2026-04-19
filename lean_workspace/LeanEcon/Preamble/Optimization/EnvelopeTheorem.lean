import Mathlib

/-- Envelope theorem setup: the derivative of the value function agrees with
    the derivative of the partially optimized Lagrangian. -/
def envelope_theorem
    (value partialL : ℝ → ℝ) (theta : ℝ) : Prop :=
  ∃ dv dL,
    HasDerivAt value dv theta ∧
    HasDerivAt partialL dL theta ∧
    dv = dL
