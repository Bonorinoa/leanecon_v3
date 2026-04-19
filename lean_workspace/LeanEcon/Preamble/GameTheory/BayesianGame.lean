import Mathlib

/-- Bayesian game with type spaces, action spaces, a prior on type profiles,
    and payoff functions indexed by realized types and actions. -/
structure bayesian_game (ι : Type*) where
  TypeSpace : ι → Type*
  ActionSpace : ι → Type*
  prior : ((i : ι) → TypeSpace i) → ENNReal
  payoff : ((i : ι) → TypeSpace i) → ((i : ι) → ActionSpace i) → ι → ℝ
