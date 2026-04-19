import Mathlib

/-- Income elasticity of demand: `(dq/dm) * (m/q)`. -/
noncomputable def income_elasticity (dq_dm m q : ℝ) : ℝ :=
  dq_dm * (m / q)
