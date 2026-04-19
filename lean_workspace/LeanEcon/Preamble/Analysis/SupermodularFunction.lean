import Mathlib

/-- Supermodularity on a distributive lattice. -/
def supermodular_function {α : Type*} [DistribLattice α] (f : α → ℝ) : Prop :=
  ∀ x y, f (x ⊔ y) + f (x ⊓ y) ≥ f x + f y
