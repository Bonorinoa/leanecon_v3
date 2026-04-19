import Mathlib.Dynamics.FixedPoints.Basic
import Mathlib.Topology.MetricSpace.Contracting

/--
The value function is the fixed point selected by a contracting Bellman-style
operator on a complete metric space of candidate value functions.
-/
noncomputable def ValueFunction {V : Type*}
    [MetricSpace V] [CompleteSpace V] [Nonempty V]
    {K : NNReal} (T : V → V) (hT : ContractingWith K T) : V :=
  ContractingWith.fixedPoint (f := T) hT

/--
The constructed value function solves its own recursive fixed-point equation,
which is the formal core of many dynamic-programming arguments.
-/
theorem valueFunction_isFixedPt {V : Type*}
    [MetricSpace V] [CompleteSpace V] [Nonempty V]
    {K : NNReal} (T : V → V) (hT : ContractingWith K T) :
    Function.IsFixedPt T (ValueFunction T hT) := by
  simpa [ValueFunction] using
    ContractingWith.fixedPoint_isFixedPt (f := T) hT
