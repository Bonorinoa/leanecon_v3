import Mathlib.MeasureTheory.Measure.MeasureSpaceDef

open MeasureTheory

/--
An economic measure records how mass, probability, or population weight is assigned
to measurable events in a state space.
-/
abbrev EconomicMeasure (α : Type*) [MeasurableSpace α] := Measure α

/--
Any admissible economic measure assigns zero mass to the impossible event.
Economically, a null event carries no probability or market weight.
-/
theorem economicMeasure_empty {α : Type*} [MeasurableSpace α]
    (μ : EconomicMeasure α) : μ ∅ = 0 := by
  simp
