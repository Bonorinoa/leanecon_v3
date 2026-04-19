import Mathlib

/-- Value function as the fixed point of a contracting Bellman operator. -/
noncomputable def value_function {V : Type*}
    [MetricSpace V] [CompleteSpace V] [Nonempty V]
    {K : NNReal} (T : V → V) (hT : ContractingWith K T) : V :=
  ContractingWith.fixedPoint T hT
