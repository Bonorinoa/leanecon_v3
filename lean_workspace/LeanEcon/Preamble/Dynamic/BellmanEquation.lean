import Mathlib

/-- Right-hand side of the Bellman equation for deterministic cake-eating.
    V(k) = u(k - k') + β * V(k') where k' is the policy choice. -/
noncomputable def bellman_rhs (u : ℝ → ℝ) (β : ℝ) (V : ℝ → ℝ) (k k' : ℝ) : ℝ :=
  u (k - k') + β * V k'
