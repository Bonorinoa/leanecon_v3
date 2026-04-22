import Mathlib.Logic.Function.Iterate

/--
A steady state is a fixed point of the law of motion governing the macro state.
-/
def IsSteadyState {State : Type*} (lawOfMotion : State → State) (state : State) : Prop :=
  lawOfMotion state = state

/--
Once a state is steady, every iterate of the law of motion leaves it unchanged.
-/
theorem IsSteadyState.iterate_eq {State : Type*}
    {lawOfMotion : State → State} {state : State}
    (h : IsSteadyState lawOfMotion state) (periods : ℕ) :
    lawOfMotion^[periods] state = state := by
  exact Function.iterate_fixed h periods
