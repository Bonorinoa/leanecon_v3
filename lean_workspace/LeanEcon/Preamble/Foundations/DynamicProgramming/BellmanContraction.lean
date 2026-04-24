import LeanEcon.Preamble.Foundations.DynamicProgramming.ContractionMapping
import LeanEcon.Preamble.Foundations.DynamicProgramming.ValueFunction

/--
A certificate that a Bellman-style recursive operator is a contraction. The
metric estimate is supplied as data rather than assumed from a bare discount
factor, keeping the bridge honest for different value-space metrics.
-/
structure BellmanContractionCertificate {V : Type*} [MetricSpace V] (T : V → V) where
  constant : NNReal
  contracting : ContractingWith constant T

/--
A Bellman contraction certificate fits the reusable contraction template.
-/
theorem BellmanContractionCertificate.isContraction {V : Type*} [MetricSpace V]
    {T : V → V} (h : BellmanContractionCertificate T) : IsContraction T := by
  exact ⟨h.constant, h.contracting⟩

/--
A certified Bellman-style contraction has a recursive fixed point.
-/
theorem BellmanContractionCertificate.exists_fixedPoint {V : Type*}
    [MetricSpace V] [CompleteSpace V] [Nonempty V]
    {T : V → V} (h : BellmanContractionCertificate T) :
    ∃ v, Function.IsFixedPt T v := by
  exact contraction_has_fixedPoint h.isContraction

/--
The value function selected from a certified Bellman-style contraction.
-/
noncomputable def BellmanContractionCertificate.valueFunction {V : Type*}
    [MetricSpace V] [CompleteSpace V] [Nonempty V]
    {T : V → V} (h : BellmanContractionCertificate T) : V :=
  ValueFunction T h.contracting

/--
The value function selected by a Bellman contraction certificate is fixed by
the underlying recursive operator.
-/
theorem BellmanContractionCertificate.valueFunction_isFixedPt {V : Type*}
    [MetricSpace V] [CompleteSpace V] [Nonempty V]
    {T : V → V} (h : BellmanContractionCertificate T) :
    Function.IsFixedPt T h.valueFunction := by
  simpa [BellmanContractionCertificate.valueFunction] using
    _root_.valueFunction_isFixedPt T h.contracting
