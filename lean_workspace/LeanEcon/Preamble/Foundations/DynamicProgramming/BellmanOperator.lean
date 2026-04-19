import Mathlib.Data.Real.Basic

/--
The deterministic Bellman operator updates a continuation-value guess using
current reward plus discounted value of the next state.
-/
def BellmanOperator {S : Type*}
    (reward : S → ℝ) (transition : S → S) (β : ℝ) :
    (S → ℝ) → (S → ℝ) :=
  fun v s => reward s + β * v (transition s)

/--
If one continuation-value function dominates another and the discount factor is
nonnegative, the Bellman operator preserves that ranking state by state.
-/
theorem BellmanOperator.monotone {S : Type*}
    {reward : S → ℝ} {transition : S → S} {β : ℝ}
    (hβ : 0 ≤ β) {v w : S → ℝ} (hvw : ∀ s, v s ≤ w s) :
    ∀ s, BellmanOperator reward transition β v s ≤
      BellmanOperator reward transition β w s := by
  intro s
  dsimp [BellmanOperator]
  have hmul : β * v (transition s) ≤ β * w (transition s) := by
    exact mul_le_mul_of_nonneg_left (hvw (transition s)) hβ
  simpa using add_le_add_left hmul (reward s)
