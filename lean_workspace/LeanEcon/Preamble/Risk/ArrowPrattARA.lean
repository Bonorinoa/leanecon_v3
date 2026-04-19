import Mathlib

/-- Arrow-Pratt coefficient of absolute risk aversion. -/
noncomputable def absolute_risk_aversion (u' u'' : ℝ) : ℝ :=
  -(u'') / u'
