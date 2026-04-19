import Mathlib.Data.Real.Basic

/--
A Kuhn-Tucker point packages primal feasibility, nonnegative multipliers, and
complementary slackness for an inequality-constrained problem.
-/
structure KuhnTuckerPoint {α ι : Type*}
    (x : α) (g : α → ι → ℝ) (μ : ι → ℝ) : Prop where
  primal_feasible : ∀ i, g x i ≤ 0
  dual_feasible : ∀ i, 0 ≤ μ i
  slackness : ∀ i, μ i * g x i = 0

/--
Complementary slackness says each shadow price is zero unless its constraint
binds, which is the economic content of Kuhn-Tucker multipliers.
-/
theorem KuhnTuckerPoint.complementary_slackness {α ι : Type*}
    {x : α} {g : α → ι → ℝ} {μ : ι → ℝ}
    (hkt : KuhnTuckerPoint x g μ) (i : ι) : μ i * g x i = 0 := by
  exact hkt.slackness i
