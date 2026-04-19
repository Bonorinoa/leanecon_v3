import Mathlib
import LeanEcon.Preamble.GameTheory.BayesianGame

/-- Bayesian Nash equilibrium as a strategy profile with no profitable
    player-specific deviation under a supplied Bayesian best-response notion. -/
def bayesian_nash_equilibrium {ι : Type*}
    (_G : bayesian_game ι)
    (Strategy : ι → Type*)
    (no_profitable_deviation : ((i : ι) → Strategy i) → ι → Prop)
    (σ : (i : ι) → Strategy i) : Prop :=
  ∀ player, no_profitable_deviation σ player
