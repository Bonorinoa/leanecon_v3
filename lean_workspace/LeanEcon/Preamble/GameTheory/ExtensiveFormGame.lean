import Mathlib

/-- Extensive-form game with nodes, turn function, actions, transition rule,
    terminal nodes, and terminal payoffs. -/
structure extensive_form_game (ι : Type*) where
  Node : Type*
  Turn : Node → Option ι
  Action : Node → Type*
  next : (n : Node) → Action n → Node
  terminal : Set Node
  payoff : Node → ι → ℝ
