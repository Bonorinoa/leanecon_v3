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

/--
Any two fixed points of a contraction coincide. This is the uniqueness side of
the contraction-mapping theorem used for recursive economic equations.
-/
theorem contraction_fixedPoint_unique {α : Type*}
    [MetricSpace α] [CompleteSpace α] [Nonempty α]
    {f : α → α} (hf : IsContraction f) {x y : α}
    (hx : Function.IsFixedPt f x) (hy : Function.IsFixedPt f y) : x = y := by
  rcases hf with ⟨K, hK⟩
  exact ContractingWith.fixedPoint_unique' hK hx hy

/--
Every contraction on a complete metric space has a unique fixed point.
-/
theorem contraction_has_unique_fixedPoint {α : Type*}
    [MetricSpace α] [CompleteSpace α] [Nonempty α]
    {f : α → α} (hf : IsContraction f) :
    ∃! x, Function.IsFixedPt f x := by
  rcases hf with ⟨K, hK⟩
  refine ⟨ContractingWith.fixedPoint (f := f) hK, ?_, ?_⟩
  · exact ContractingWith.fixedPoint_isFixedPt (f := f) hK
  · intro y hy
    exact ContractingWith.fixedPoint_unique hK hy
