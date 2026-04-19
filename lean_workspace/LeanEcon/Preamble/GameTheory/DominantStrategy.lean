import Mathlib
import LeanEcon.Preamble.GameTheory.NormalFormGame

/-- A pure strategy is dominant if it weakly beats every deviation under every profile. -/
def dominant_strategy {ι : Type*} [DecidableEq ι]
    (G : normal_form_game ι) (player : ι) (s : G.Strategy player) : Prop :=
  ∀ profile : (i : ι) → G.Strategy i,
    ∀ s' : G.Strategy player,
      G.payoff (Function.update profile player s') player ≤
        G.payoff (Function.update profile player s) player
