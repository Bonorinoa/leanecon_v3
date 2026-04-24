import Mathlib.Topology.Instances.Real.Lemmas
import Mathlib.Topology.Order.MonotoneConvergence

/--
A monotone real sequence bounded above converges. This names the standard
monotone-convergence bridge in a form that benchmark traces can retrieve
directly.
-/
theorem monotone_boundedAbove_converges
    {u : ℕ → ℝ}
    (hu_mono : Monotone u)
    (hu_bdd : BddAbove (Set.range u)) :
    ∃ l, Filter.Tendsto u Filter.atTop (nhds l) := by
  exact ⟨⨆ i, u i, tendsto_atTop_ciSup hu_mono hu_bdd⟩
