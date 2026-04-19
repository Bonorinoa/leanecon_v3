import Mathlib

/-- Roy's identity stated as a derivative relationship between indirect utility
    and Marshallian demand in a one-good price/income environment. -/
noncomputable def roys_identity
    (v x : ℝ → ℝ → ℝ) (p w : ℝ) : Prop :=
  ∃ dv_dp dv_dw,
    HasDerivAt (fun p' => v p' w) dv_dp p ∧
    HasDerivAt (fun w' => v p w') dv_dw w ∧
    dv_dw ≠ 0 ∧
    x p w = -dv_dp / dv_dw
