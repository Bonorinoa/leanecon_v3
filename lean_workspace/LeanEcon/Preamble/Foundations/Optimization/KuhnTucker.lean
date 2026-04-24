import Mathlib.Algebra.GroupWithZero.NonZeroDivisors
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

/--
If an inequality constraint has strictly negative slack, its Kuhn-Tucker
multiplier must vanish.
-/
theorem KuhnTuckerPoint.multiplier_eq_zero_of_slack {α ι : Type*}
    {x : α} {g : α → ι → ℝ} {μ : ι → ℝ}
    (hkt : KuhnTuckerPoint x g μ) {i : ι}
    (hslack : g x i < 0) :
    μ i = 0 := by
  have hg_ne : g x i ≠ 0 := ne_of_lt hslack
  exact eq_zero_of_ne_zero_of_mul_right_eq_zero hg_ne (hkt.slackness i)
