import Mathlib

/-- Cost function for a Cobb-Douglas technology. -/
noncomputable def cost_cd (w r A α q : ℝ) : ℝ :=
  q * Real.rpow (w / (1 - α)) (1 - α) * Real.rpow (r / α) α / A
