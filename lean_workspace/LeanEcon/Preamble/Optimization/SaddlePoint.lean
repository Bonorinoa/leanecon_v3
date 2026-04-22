import Mathlib.Data.Real.Basic

/--
A saddle point simultaneously maximizes the Lagrangian in the dual argument and
minimizes it in the primal argument.
-/
def IsSaddlePoint {Primal Dual : Type*}
    (lagrangian : Primal → Dual → ℝ) (primal : Primal) (dual : Dual) : Prop :=
  (∀ alternativeDual, lagrangian primal alternativeDual ≤ lagrangian primal dual) ∧
  ∀ alternativePrimal, lagrangian primal dual ≤ lagrangian alternativePrimal dual

/--
The dual component of a saddle point is optimal holding the primal choice fixed.
-/
theorem IsSaddlePoint.dual_optimal {Primal Dual : Type*}
    {lagrangian : Primal → Dual → ℝ} {primal : Primal} {dual : Dual}
    (h : IsSaddlePoint lagrangian primal dual) (alternativeDual : Dual) :
    lagrangian primal alternativeDual ≤ lagrangian primal dual := by
  exact h.1 alternativeDual

/--
The primal component of a saddle point is optimal holding the dual choice fixed.
-/
theorem IsSaddlePoint.primal_optimal {Primal Dual : Type*}
    {lagrangian : Primal → Dual → ℝ} {primal : Primal} {dual : Dual}
    (h : IsSaddlePoint lagrangian primal dual) (alternativePrimal : Primal) :
    lagrangian primal dual ≤ lagrangian alternativePrimal dual := by
  exact h.2 alternativePrimal
