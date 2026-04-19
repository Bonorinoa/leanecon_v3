import Mathlib.Topology.Continuous
import Mathlib.Topology.ContinuousOn

/--
A preference representation is continuous when small changes in bundles generate
small changes in utility.
-/
def ContinuousPreference {α : Type*}
    [TopologicalSpace α] [TopologicalSpace ℝ] (u : α → ℝ) : Prop :=
  Continuous u

/--
A continuous preference remains continuous on every feasible subset.
Economically, continuity is preserved when attention is restricted to a budget set.
-/
theorem continuousPreference_continuousOn {α : Type*}
    [TopologicalSpace α] [TopologicalSpace ℝ] {u : α → ℝ}
    (hu : ContinuousPreference u) (s : Set α) : ContinuousOn u s := by
  exact Continuous.continuousOn hu
