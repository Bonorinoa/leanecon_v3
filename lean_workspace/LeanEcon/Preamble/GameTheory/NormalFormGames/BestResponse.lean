import Mathlib.Data.Real.Basic

/--
An action is a best response when it yields at least as much payoff as every
alternative against the fixed opponents' behavior.
-/
def IsBestResponse {Opponent Action : Type*}
    (payoff : Opponent → Action → ℝ) (opponents : Opponent) (action : Action) : Prop :=
  ∀ alternative, payoff opponents alternative ≤ payoff opponents action

/--
The defining inequality of a best response can be invoked directly for any
alternative action.
-/
theorem IsBestResponse.payoff_le {Opponent Action : Type*}
    {payoff : Opponent → Action → ℝ} {opponents : Opponent} {action : Action}
    (h : IsBestResponse payoff opponents action) (alternative : Action) :
    payoff opponents alternative ≤ payoff opponents action := by
  exact h alternative
