import Mathlib

/-- New Keynesian Phillips Curve (NKPC).
    Current inflation equals discounted expected future inflation plus
    the slope coefficient times the output gap: π = β * π_next + κ * x. -/
noncomputable def nkpc (β π_next κ x : ℝ) : ℝ :=
  β * π_next + κ * x
