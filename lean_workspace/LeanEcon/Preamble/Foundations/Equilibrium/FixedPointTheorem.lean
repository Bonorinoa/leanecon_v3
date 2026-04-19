import Mathlib.Dynamics.FixedPoints.Basic
import Mathlib.Topology.MetricSpace.Contracting

/--
A global contraction on a complete metric space admits a fixed point.
Economically, this is the reusable existence engine behind equilibrium and
recursive value equations.
-/
theorem exists_fixedPoint_of_contractingWith {α : Type*}
    [MetricSpace α] [CompleteSpace α] [Nonempty α]
    {K : NNReal} {f : α → α} (hf : ContractingWith K f) :
    ∃ x, Function.IsFixedPt f x := by
  refine ⟨ContractingWith.fixedPoint (f := f) hf, ?_⟩
  exact ContractingWith.fixedPoint_isFixedPt (f := f) hf

/--
The canonical fixed point chosen by the contraction mapping theorem is indeed
fixed, giving a concrete equilibrium object for downstream constructions.
-/
theorem fixedPoint_isFixedPt {α : Type*}
    [MetricSpace α] [CompleteSpace α] [Nonempty α]
    {K : NNReal} {f : α → α} (hf : ContractingWith K f) :
    Function.IsFixedPt f (ContractingWith.fixedPoint (f := f) hf) := by
  exact ContractingWith.fixedPoint_isFixedPt (f := f) hf
