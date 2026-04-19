import Mathlib

/-- A valid geometric discount factor lies strictly between zero and one. -/
def discount_factor (beta : ℝ) : Prop :=
  0 < beta ∧ beta < 1

/-- Present value with geometric discounting for a constant stream. -/
noncomputable def present_value_constant (x beta : ℝ) (T : ℕ) : ℝ :=
  x * (1 - beta ^ T) / (1 - beta)
