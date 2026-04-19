import Mathlib.Dynamics.FixedPoints.Basic
import Mathlib.Topology.MetricSpace.Contracting

/--
A contraction is a self-map with some global Lipschitz constant strictly below
one, the standard sufficient condition for stable recursive problems.
-/
def IsContraction {α : Type*} [MetricSpace α] (f : α → α) : Prop :=
  ∃ K : NNReal, ContractingWith K f

/--
Every contraction on a complete metric space has a fixed point.
Economically, contraction structure guarantees a unique recursive solution.
-/
theorem contraction_has_fixedPoint {α : Type*}
    [MetricSpace α] [CompleteSpace α] [Nonempty α]
    {f : α → α} (hf : IsContraction f) :
    ∃ x, Function.IsFixedPt f x := by
  rcases hf with ⟨K, hK⟩
  refine ⟨ContractingWith.fixedPoint (f := f) hK, ?_⟩
  exact ContractingWith.fixedPoint_isFixedPt (f := f) hK
