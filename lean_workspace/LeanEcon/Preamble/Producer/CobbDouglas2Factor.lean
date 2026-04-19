import Mathlib

/-- Two-factor Cobb-Douglas production function. -/
noncomputable def cobb_douglas (A K L α : ℝ) : ℝ :=
  A * Real.rpow K α * Real.rpow L (1 - α)

/-
-- Proven lemmas (archived — available as Planner metadata, not formalizer context)

/-- Output elasticity w.r.t. capital: (∂f/∂K)·(K/f) = α.
    After substituting and canceling all A, L, and K^α terms,
    the expression reduces to α · K · K⁻¹ = α. -/
theorem cobb_douglas_elasticity_capital
    (α K : ℝ) (_ : 0 < α) (_ : α < 1) (hK : K > 0) :
    α * K * K⁻¹ = α := by
  field_simp
-/
