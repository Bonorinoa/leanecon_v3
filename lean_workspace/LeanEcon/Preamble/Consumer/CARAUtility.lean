import Mathlib

/-- CARA (negative exponential) utility function. -/
noncomputable def cara_utility (c α : ℝ) : ℝ :=
  -(Real.exp (-α * c)) / α

/-
-- Proven lemmas (archived — available as Planner metadata, not formalizer context)

/-- CARA absolute risk aversion: -u''(c)/u'(c) = α.
    After substituting u'(c) = exp(-α·c) and u''(c) = -α·exp(-α·c),
    the exp terms cancel: -(-α · exp(-α·c)) / exp(-α·c) = α. -/
theorem cara_ara_simplified
    (α e : ℝ) (_ : α > 0) (he : e > 0) :
    -(-α * e) / e = α := by
  field_simp
-/
