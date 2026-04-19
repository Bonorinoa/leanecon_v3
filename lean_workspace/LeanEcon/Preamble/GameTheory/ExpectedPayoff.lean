import Mathlib

/-- Expected payoff for a `2 x 2` mixed-strategy game. -/
noncomputable def expected_payoff_2x2
    (u₁₁ u₁₂ u₂₁ u₂₂ p q : ℝ) : ℝ :=
  p * q * u₁₁ + p * (1 - q) * u₁₂ +
  (1 - p) * q * u₂₁ + (1 - p) * (1 - q) * u₂₂
