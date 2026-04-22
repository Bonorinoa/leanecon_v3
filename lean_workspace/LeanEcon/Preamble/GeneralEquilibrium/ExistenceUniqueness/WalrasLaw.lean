import Mathlib.Algebra.BigOperators.Ring.Finset
import Mathlib.Data.Real.Basic

open scoped BigOperators

/--
Walras' law states that the value of aggregate excess demand is zero at a given
price vector.
-/
def SatisfiesWalrasLaw {Commodity : Type*} [Fintype Commodity]
    (price excessDemand : Commodity → ℝ) : Prop :=
  ∑ good, price good * excessDemand good = 0

/--
If excess demand vanishes good by good, Walras' law holds immediately.
-/
theorem satisfiesWalrasLaw_of_marketClearing {Commodity : Type*} [Fintype Commodity]
    (price excessDemand : Commodity → ℝ)
    (hclear : ∀ good, excessDemand good = 0) :
    SatisfiesWalrasLaw price excessDemand := by
  simp [SatisfiesWalrasLaw, hclear]
