import Mathlib
import LeanEcon.Preamble.GameTheory.NormalFormGame

/-- Pure-strategy best-response correspondence in a normal-form game. -/
noncomputable def best_response {ι : Type*} [DecidableEq ι]
    (G : normal_form_game ι) (player : ι) (profile : (i : ι) → G.Strategy i) :
    Set (G.Strategy player) :=
  {s | ∀ s' : G.Strategy player,
      G.payoff (Function.update profile player s') player ≤
        G.payoff (Function.update profile player s) player}
