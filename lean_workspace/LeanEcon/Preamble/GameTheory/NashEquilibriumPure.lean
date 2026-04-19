import Mathlib
import LeanEcon.Preamble.GameTheory.BestResponse

/-- Pure-strategy Nash equilibrium: every player's action is a best response. -/
def nash_equilibrium_pure {ι : Type*} [DecidableEq ι]
    (G : normal_form_game ι) (profile : (i : ι) → G.Strategy i) : Prop :=
  ∀ player, profile player ∈ best_response G player profile
