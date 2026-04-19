import Mathlib

/-- Arrow-Pratt coefficient of relative risk aversion. -/
noncomputable def relative_risk_aversion (c u' u'' : ℝ) : ℝ :=
  -(c * u'') / u'
