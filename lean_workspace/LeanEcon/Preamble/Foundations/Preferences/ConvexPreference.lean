import Mathlib.Analysis.Convex.Function
import Mathlib.Analysis.Normed.Module.Basic

/--
A preference representation is convex when mixtures of bundles are never worse
than the average of their utility levels.
-/
def ConvexPreference {E : Type*} [NormedAddCommGroup E] [NormedSpace ℝ E]
    (u : E → ℝ) : Prop :=
  ConvexOn ℝ Set.univ u

/--
Convex preferences expose a globally convex utility representation on the whole
commodity space, which is the reusable object used by downstream proofs.
-/
theorem convexPreference_convexOn_univ {E : Type*}
    [NormedAddCommGroup E] [NormedSpace ℝ E] {u : E → ℝ}
    (hu : ConvexPreference u) : ConvexOn ℝ Set.univ u := by
  exact hu
