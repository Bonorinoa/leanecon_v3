import Mathlib.Data.Real.Basic

/--
One policy improves on another when it delivers at least as high an evaluation
according to a chosen objective criterion.
-/
def PolicyImproves {π : Type*}
    (criterion : π → ℝ) (oldPolicy newPolicy : π) : Prop :=
  criterion oldPolicy ≤ criterion newPolicy

/--
Any policy weakly improves on itself, which is the baseline consistency property
used when policy-iteration arguments start from a candidate rule.
-/
theorem policyImproves_refl {π : Type*}
    (criterion : π → ℝ) (policy : π) :
    PolicyImproves criterion policy policy := by
  exact le_rfl
