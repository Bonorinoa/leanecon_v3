import Mathlib

/-- Price elasticity of demand: `(dq/dp) * (p/q)`. -/
noncomputable def price_elasticity (dq_dp p q : ℝ) : ℝ :=
  dq_dp * (p / q)
