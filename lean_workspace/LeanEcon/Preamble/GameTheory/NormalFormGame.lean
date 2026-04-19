import Mathlib

/-- Finite-player normal-form game with pure strategy spaces and payoff functions. -/
structure normal_form_game (ι : Type*) where
  Strategy : ι → Type*
  payoff : ((i : ι) → Strategy i) → ι → ℝ
