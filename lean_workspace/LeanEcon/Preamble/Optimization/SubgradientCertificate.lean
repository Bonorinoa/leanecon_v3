import Mathlib.Data.Real.Basic

/--
A scalar subgradient certificate lower-bounds the objective everywhere by the
affine expansion at the reference point.
-/
def IsSubgradientAt (objective : ℝ → ℝ) (subgradient point : ℝ) : Prop :=
  ∀ candidate, objective point + subgradient * (candidate - point) ≤ objective candidate

/--
A zero subgradient certifies global optimality in the scalar convex setting.
-/
theorem IsSubgradientAt.globalMinimizer_of_zero {objective : ℝ → ℝ} {point : ℝ}
    (h : IsSubgradientAt objective 0 point) (candidate : ℝ) :
    objective point ≤ objective candidate := by
  specialize h candidate
  simpa [IsSubgradientAt] using h
