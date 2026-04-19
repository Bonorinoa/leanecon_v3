import Mathlib

/-- CRRA (isoelastic) utility function. -/
noncomputable def crra_utility (c γ : ℝ) : ℝ :=
  Real.rpow c (1 - γ) / (1 - γ)

/-
-- Proven lemmas (archived — available as Planner metadata, not formalizer context)

/-- CRRA relative risk aversion equals γ.
    After substituting u'(c) = c^(-γ) and u''(c) = -γ·c^(-γ-1) into
    -c·u''/u' and simplifying, the expression reduces to -c·(-γ·c⁻¹) = γ. -/
theorem crra_rra_simplified
    (c γ : ℝ) (hc : c > 0) (_ : γ > 0) (_ : γ ≠ 1) :
    -c * (-γ * c⁻¹) = γ := by
  field_simp
-/
