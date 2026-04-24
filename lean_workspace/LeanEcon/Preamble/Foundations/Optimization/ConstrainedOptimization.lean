import Mathlib.Data.Real.Basic
import Mathlib.Topology.Instances.Real.Lemmas
import Mathlib.Topology.Order.Compact

/--
A constrained maximum is a feasible choice that weakly dominates every other
feasible alternative in the decision problem.
-/
def IsConstrainedMaximum {α : Type*}
    (f : α → ℝ) (feasible : Set α) (x : α) : Prop :=
  x ∈ feasible ∧ ∀ ⦃y : α⦄, y ∈ feasible → f y ≤ f x

/--
A constrained maximizer is, by definition, feasible.
Economically, the optimizer must satisfy the admissibility constraints.
-/
theorem IsConstrainedMaximum.feasible {α : Type*}
    {f : α → ℝ} {feasible : Set α} {x : α}
    (hx : IsConstrainedMaximum f feasible x) : x ∈ feasible := by
  exact hx.1

/--
Every feasible alternative yields weakly lower objective value at a constrained
maximum, which is the core comparison used in revealed-optimization arguments.
-/
theorem IsConstrainedMaximum.value_le {α : Type*}
    {f : α → ℝ} {feasible : Set α} {x y : α}
    (hx : IsConstrainedMaximum f feasible x) (hy : y ∈ feasible) :
    f y ≤ f x := by
  exact hx.2 hy

/--
A continuous objective on a nonempty compact feasible set admits a constrained
maximum. This wraps Mathlib's extreme-value theorem in the local optimizer
certificate used by the economics preamble.
-/
theorem exists_isConstrainedMaximum_of_isCompact_continuousOn {α : Type*}
    [TopologicalSpace α]
    {feasible : Set α} {f : α → ℝ}
    (hcompact : IsCompact feasible) (hne : feasible.Nonempty)
    (hcontinuous : ContinuousOn f feasible) :
    ∃ x, IsConstrainedMaximum f feasible x := by
  rcases hcompact.exists_isMaxOn hne hcontinuous with ⟨x, hx, hmax⟩
  refine ⟨x, hx, ?_⟩
  intro y hy
  exact hmax hy
