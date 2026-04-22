import Mathlib.Algebra.BigOperators.Ring.Finset
import Mathlib.Data.Real.Basic

open scoped BigOperators

/--
Finite-horizon lifetime utility sums discounted period payoffs and provides a
compact preamble object for recursive macro proofs.
-/
def DiscountedLifetimeUtility (β : ℝ) (utility : ℕ → ℝ) (horizon : ℕ) : ℝ :=
  Finset.sum (Finset.range horizon) fun t => β ^ t * utility t

/--
Zero horizon yields zero lifetime utility because no periods are accumulated.
-/
theorem discountedLifetimeUtility_zero (β : ℝ) (utility : ℕ → ℝ) :
    DiscountedLifetimeUtility β utility 0 = 0 := by
  simp [DiscountedLifetimeUtility]

/--
Extending the horizon by one period appends exactly one discounted payoff term.
-/
theorem discountedLifetimeUtility_succ (β : ℝ) (utility : ℕ → ℝ) (horizon : ℕ) :
    DiscountedLifetimeUtility β utility (horizon + 1) =
      DiscountedLifetimeUtility β utility horizon + β ^ horizon * utility horizon := by
  simp [DiscountedLifetimeUtility, Finset.sum_range_succ]
