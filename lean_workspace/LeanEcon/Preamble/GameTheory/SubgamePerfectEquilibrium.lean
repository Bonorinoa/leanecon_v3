import Mathlib
import LeanEcon.Preamble.GameTheory.ExtensiveFormGame

/-- Lightweight subgame-perfect equilibrium predicate parameterized by a
    continuation-equilibrium notion over subgame roots. -/
def subgame_perfect_equilibrium {ι : Type*}
    (G : extensive_form_game ι)
    (Strategy : ι → Type*)
    (subgame_root : G.Node → Prop)
    (continuation_equilibrium : G.Node → ((i : ι) → Strategy i) → Prop)
    (σ : (i : ι) → Strategy i) : Prop :=
  ∀ n, subgame_root n → continuation_equilibrium n σ
