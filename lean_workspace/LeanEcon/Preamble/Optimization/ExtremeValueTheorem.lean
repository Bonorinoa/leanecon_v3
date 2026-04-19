import Mathlib

/- Extreme value theorem (Weierstrass): a continuous real-valued function
   on a nonempty compact set attains its maximum and minimum.
   Re-exports Mathlib's IsCompact.exists_isMaxOn and IsCompact.exists_isMinOn. -/

/-
-- Proven lemmas (archived — available as Planner metadata, not formalizer context)

/-- A continuous function on a nonempty compact set attains a maximum. -/
theorem continuous_attains_max_on_compact
    {α : Type*} [TopologicalSpace α]
    {s : Set α} {f : α → ℝ}
    (hs : IsCompact s) (hne : s.Nonempty)
    (hf : ContinuousOn f s) :
    ∃ x ∈ s, IsMaxOn f s x :=
  hs.exists_isMaxOn hne hf

/-- A continuous function on a nonempty compact set attains a minimum. -/
theorem continuous_attains_min_on_compact
    {α : Type*} [TopologicalSpace α]
    {s : Set α} {f : α → ℝ}
    (hs : IsCompact s) (hne : s.Nonempty)
    (hf : ContinuousOn f s) :
    ∃ x ∈ s, IsMinOn f s x :=
  hs.exists_isMinOn hne hf
-/
