import Mathlib.Topology.Continuous

/--
A commodity topology specifies which bundles are close enough to count as small
perturbations in prices, endowments, or allocations.
-/
abbrev CommodityTopology (α : Type*) := TopologicalSpace α

/--
Constant economic environments are continuous maps on any commodity topology.
This captures the idea that a policy with no state dependence has no jumps.
-/
theorem continuous_const_commodity {α β : Type*}
    [TopologicalSpace α] [TopologicalSpace β] (b : β) :
    Continuous fun _ : α => b := by
  simpa using continuous_const
