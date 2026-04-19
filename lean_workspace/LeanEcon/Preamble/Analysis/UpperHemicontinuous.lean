import Mathlib

/-- Economists' upper hemicontinuity wrapper for set-valued correspondences. -/
def upper_hemicontinuous {α : Type*} {β : Type*}
    [TopologicalSpace α] [TopologicalSpace β]
    (F : α → Set β) : Prop :=
  UpperHemicontinuous F
