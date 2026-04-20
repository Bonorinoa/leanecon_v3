import LeanEcon.Preamble.Foundations.Primitives.Measure
import LeanEcon.Preamble.Foundations.Optimization.KuhnTucker
import LeanEcon.Preamble.Foundations.Optimization.ConstrainedOptimization
import LeanEcon.Preamble.Foundations.Preferences.ConvexPreference
import LeanEcon.Preamble.Foundations.Primitives.TopologicalSpace

open Classical

theorem apollo_formalizer_budget_equality_from_optimization_2 : ∀ (p1 p2 m x1 x2 : ℝ), 0 < p1 → 0 < p2 → 0 ≤ m → 0 ≤ x1 → 0 ≤ x2 → (∃ (feasible : Set (ℝ × ℝ)), (x1, x2) ∈ feasible ∧ ∀ (y1 y2 : ℝ), (y1, y2) ∈ feasible → p1 * y1 + p2 * y2 ≤ m) := by
  sorry
