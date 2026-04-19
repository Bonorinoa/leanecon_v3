import Mathlib

/-- Recursive competitive equilibrium packaged as value and policy objects with
    an abstract market-clearing condition. -/
structure recursive_competitive_eq (S A M : Type*) where
  value : S → ℝ
  policy : S → A
  market_clearing : M → Prop
