import Mathlib.Data.Real.Basic

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
