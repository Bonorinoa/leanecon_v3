"""
Metadata index for LeanEcon's reusable preamble modules.

The Lean source of truth lives under `lean_workspace/LeanEcon/Preamble/`.
This Python module only stores lookup metadata and reads the corresponding Lean
files from disk when prompt context or import statements are needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAN_WORKSPACE = PROJECT_ROOT / "lean_workspace"


@dataclass(frozen=True)
class PreambleEntry:
    """A reusable LeanEcon preamble module and its discovery metadata."""

    name: str
    lean_module: str
    description: str
    keywords: tuple[str, ...]
    auto_keywords: tuple[str, ...] | None = None
    parameters: tuple[str, ...] = ()
    planner_metadata: dict[str, Any] = field(default_factory=dict)

    # Structural metadata — populated from the corresponding .lean file
    definitions: tuple[str, ...] = ()            # noncomputable def / def names in the file
    definition_signatures: tuple[str, ...] = ()  # "(params : Types) : ReturnType" — NO body
    proven_lemmas: tuple[str, ...] = ()
    # DEPRECATED: use planner_metadata["proven_lemmas"]
    theorem_template: str | None = None
    # DEPRECATED: use planner_metadata["theorem_template"]
    tactic_hint: str | None = None
    # DEPRECATED: use planner_metadata["tactic_hint"]

    # Optional reference into PREAMBLE_CATALOG.md — format: "PREAMBLE_CATALOG#entry_name".
    # When set, planner_skill_hint returns the enriched tactic guidance section
    # from the skills catalog instead of the thin planner_tactic_hint string.
    skill_ref: str | None = None

    @property
    def lean_path(self) -> Path:
        return LEAN_WORKSPACE / Path(*self.lean_module.split(".")).with_suffix(".lean")

    @property
    def planner_skill_hint(self) -> str | None:
        """Richer tactic guidance from the skills catalog; falls back to planner_tactic_hint.

        Returns the body of the matching ``## entry_name`` section from
        ``PREAMBLE_CATALOG.md`` when a ``skill_ref`` is configured.  If the
        skills system is unavailable or the section is missing, returns ``None``
        so callers fall back to ``planner_tactic_hint``.
        """
        ref = self.skill_ref or self.planner_metadata.get("skill_ref")
        if not ref:
            return None
        skill_name, _, section = ref.partition("#")
        if not section:
            return None
        try:
            from src.skills import load_section

            return load_section(skill_name, section)
        except Exception:
            return None

    @property
    def planner_proven_lemmas(self) -> tuple[str, ...]:
        value = self.planner_metadata.get("proven_lemmas")
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        return self.proven_lemmas

    @property
    def planner_theorem_template(self) -> str | None:
        value = self.planner_metadata.get("theorem_template")
        if isinstance(value, str) and value.strip():
            return value
        return self.theorem_template

    @property
    def planner_tactic_hint(self) -> str | None:
        value = self.planner_metadata.get("tactic_hint")
        if isinstance(value, str) and value.strip():
            return value
        return self.tactic_hint


PREAMBLE_LIBRARY: dict[str, PreambleEntry] = {}


def _register(entry: PreambleEntry) -> None:
    planner_metadata = dict(entry.planner_metadata)
    if "proven_lemmas" not in planner_metadata and entry.proven_lemmas:
        planner_metadata["proven_lemmas"] = list(entry.proven_lemmas)
    if "theorem_template" not in planner_metadata and entry.theorem_template is not None:
        planner_metadata["theorem_template"] = entry.theorem_template
    if "tactic_hint" not in planner_metadata and entry.tactic_hint is not None:
        planner_metadata["tactic_hint"] = entry.tactic_hint

    entry = replace(
        entry,
        planner_metadata=planner_metadata,
        proven_lemmas=(),
        theorem_template=None,
        tactic_hint=None,
        skill_ref=entry.skill_ref,
    )
    PREAMBLE_LIBRARY[entry.name] = entry


_register(
    PreambleEntry(
        name="cobb_douglas_2factor",
        lean_module="LeanEcon.Preamble.Producer.CobbDouglas2Factor",
        description="Two-factor Cobb-Douglas production function with elasticity proof",
        keywords=(
            "cobb-douglas",
            "cobb douglas",
            "cd production",
            "output elasticity",
            "marginal product",
            "returns to scale",
            "production function",
            "diminishing returns",
            "factor share",
            "homogeneous of degree",
        ),
        auto_keywords=(
            "cobb-douglas",
            "cobb douglas",
            "cd production",
            "output elasticity",
            "factor share",
        ),
        parameters=("A", "K", "L", "α"),
        definitions=("cobb_douglas",),
        definition_signatures=("(A K L α : ℝ) : ℝ",),
        proven_lemmas=("cobb_douglas_elasticity_capital",),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Producer.CobbDouglas2Factor

/-- [state the claim about Cobb-Douglas here] -/
theorem your_theorem_name
    (A K L α : ℝ) (hA : A > 0) (hK : K > 0) (hL : L > 0) (hα : 0 < α) (hα1 : α < 1) :
    cobb_douglas A K L α = A * Real.rpow K α * Real.rpow L (1 - α) := by
  sorry""",
        tactic_hint="field_simp",
        skill_ref="PREAMBLE_CATALOG#cobb_douglas_2factor",
    )
)
_register(
    PreambleEntry(
        name="ces_2factor",
        lean_module="LeanEcon.Preamble.Producer.CES2Factor",
        description="Two-factor CES production function with elasticity of substitution σ",
        keywords=(
            "ces production",
            "ces function",
            "constant elasticity of substitution",
            "returns to scale",
            "homogeneous",
            "production function",
            "elasticity of substitution",
            "homogeneous of degree",
        ),
        auto_keywords=(
            "ces production",
            "ces function",
            "constant elasticity of substitution",
            "elasticity of substitution",
        ),
        parameters=("A", "K", "L", "σ", "α"),
        definitions=("ces_production",),
        definition_signatures=("(A K L σ α : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Producer.CES2Factor

/-- [state the claim about CES production here] -/
theorem your_theorem_name
    (A K L σ α : ℝ) (hA : A > 0) (hK : K > 0) (hL : L > 0) (hσ : σ > 0) (hσ1 : σ ≠ 1)
    (hα : 0 < α) (hα1 : α < 1) :
    ces_production A K L σ α = A * Real.rpow
      (α * Real.rpow K ((σ - 1) / σ) + (1 - α) * Real.rpow L ((σ - 1) / σ))
      (σ / (σ - 1)) := by
  sorry""",
        tactic_hint="simp [ces_production]",
    )
)
_register(
    PreambleEntry(
        name="crra_utility",
        lean_module="LeanEcon.Preamble.Consumer.CRRAUtility",
        description="CRRA utility function with derivative and RRA lemmas",
        keywords=(
            "crra",
            "isoelastic",
            "crra utility",
            "constant relative risk aversion",
            "risk aversion",
            "concave utility",
            "diminishing marginal utility",
            "power utility",
            "marginal utility",
            "derivative",
        ),
        auto_keywords=(
            "crra",
            "isoelastic",
            "crra utility",
            "constant relative risk aversion",
            "power utility",
        ),
        parameters=("c", "γ"),
        definitions=("crra_utility",),
        definition_signatures=("(c γ : ℝ) : ℝ",),
        proven_lemmas=("crra_rra_simplified",),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.CRRAUtility

/-- [state the claim about CRRA utility here] -/
theorem your_theorem_name
    (c γ : ℝ) (hc : c > 0) (hγ : γ > 0) (hγ1 : γ ≠ 1) :
    crra_utility c γ = Real.rpow c (1 - γ) / (1 - γ) := by
  sorry""",
        tactic_hint="field_simp",
        skill_ref="PREAMBLE_CATALOG#crra_utility",
    )
)
_register(
    PreambleEntry(
        name="cara_utility",
        lean_module="LeanEcon.Preamble.Consumer.CARAUtility",
        description="CARA utility function with derivative and ARA lemmas",
        keywords=(
            "cara",
            "cara utility",
            "constant absolute risk aversion",
            "exponential utility",
            "absolute risk aversion",
            "risk aversion",
            "arrow-pratt",
            "arrow pratt",
            "absolute",
            "coefficient",
            "exp",
            "exponential",
            "absolute risk",
            "marginal utility",
            "derivative",
        ),
        auto_keywords=(
            "cara",
            "cara utility",
            "constant absolute risk aversion",
            "exponential utility",
            "absolute risk aversion",
            "arrow-pratt",
            "absolute",
            "coefficient",
            "exp",
        ),
        parameters=("c", "α"),
        definitions=("cara_utility",),
        definition_signatures=("(c α : ℝ) : ℝ",),
        proven_lemmas=("cara_ara_simplified",),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.CARAUtility

/-- [state the claim about CARA utility here] -/
theorem your_theorem_name
    (c α : ℝ) (hc : c > 0) (hα : α > 0) :
    cara_utility c α = -(Real.exp (-α * c)) / α := by
  sorry""",
        tactic_hint="field_simp",
        skill_ref="PREAMBLE_CATALOG#cara_utility",
    )
)
_register(
    PreambleEntry(
        name="stone_geary_utility",
        lean_module="LeanEcon.Preamble.Consumer.StoneGearyUtility",
        description="Stone-Geary utility for two goods with marginal utility lemmas",
        keywords=("stone-geary", "stone geary", "les utility", "linear expenditure"),
        parameters=("x₁", "x₂", "α", "γ₁", "γ₂"),
        definitions=("stone_geary_utility",),
        definition_signatures=("(x₁ x₂ α γ₁ γ₂ : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.StoneGearyUtility

/-- [state the claim about Stone-Geary utility here] -/
theorem your_theorem_name
    (x₁ x₂ α γ₁ γ₂ : ℝ) (h₁ : x₁ > γ₁) (h₂ : x₂ > γ₂) (hα : 0 < α) (hα1 : α < 1) :
    stone_geary_utility x₁ x₂ α γ₁ γ₂ =
      α * Real.log (x₁ - γ₁) + (1 - α) * Real.log (x₂ - γ₂) := by
  sorry""",
        tactic_hint="simp [stone_geary_utility]",
    )
)
_register(
    PreambleEntry(
        name="price_elasticity",
        lean_module="LeanEcon.Preamble.Consumer.PriceElasticity",
        description="Price elasticity of demand as (dq/dp)·(p/q)",
        keywords=("price elasticity", "elasticity of demand", "demand elasticity"),
        parameters=("dq_dp", "p", "q"),
        definitions=("price_elasticity",),
        definition_signatures=("(dq_dp p q : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.PriceElasticity

/-- [state the claim about price elasticity here] -/
theorem your_theorem_name
    (dq_dp p q : ℝ) (hq : q ≠ 0) :
    price_elasticity dq_dp p q = dq_dp * (p / q) := by
  sorry""",
        tactic_hint="simp [price_elasticity]; ring",
    )
)
_register(
    PreambleEntry(
        name="income_elasticity",
        lean_module="LeanEcon.Preamble.Consumer.IncomeElasticity",
        description="Income elasticity of demand as (dq/dm)·(m/q)",
        keywords=("income elasticity",),
        parameters=("dq_dm", "m", "q"),
        definitions=("income_elasticity",),
        definition_signatures=("(dq_dm m q : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.IncomeElasticity

/-- [state the claim about income elasticity here] -/
theorem your_theorem_name
    (dq_dm m q : ℝ) (hq : q ≠ 0) :
    income_elasticity dq_dm m q = dq_dm * (m / q) := by
  sorry""",
        tactic_hint="simp [income_elasticity]; ring",
    )
)
_register(
    PreambleEntry(
        name="arrow_pratt_rra",
        lean_module="LeanEcon.Preamble.Risk.ArrowPrattRRA",
        description="Arrow-Pratt measure of relative risk aversion",
        keywords=(
            "relative risk aversion",
            "rra",
            "arrow-pratt",
            "arrow pratt",
            "risk premium",
            "risk aversion coefficient",
            "concavity of utility",
        ),
        auto_keywords=(
            "relative risk aversion",
            "rra",
            "arrow-pratt",
            "arrow pratt",
            "risk aversion coefficient",
        ),
        parameters=("c", "u'", "u''"),
        definitions=("relative_risk_aversion",),
        definition_signatures=("(c u' u'' : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Risk.ArrowPrattRRA

/-- [state the claim about relative risk aversion here] -/
theorem your_theorem_name
    (c u' u'' : ℝ) (hu' : u' ≠ 0) :
    relative_risk_aversion c u' u'' = -(c * u'') / u' := by
  sorry""",
        tactic_hint="simp [relative_risk_aversion]; ring",
    )
)
_register(
    PreambleEntry(
        name="arrow_pratt_ara",
        lean_module="LeanEcon.Preamble.Risk.ArrowPrattARA",
        description="Arrow-Pratt measure of absolute risk aversion",
        keywords=(
            "absolute risk aversion",
            "ara",
            "risk premium",
            "absolute risk",
            "concavity of utility",
        ),
        auto_keywords=(
            "absolute risk aversion",
            "ara",
            "absolute risk",
        ),
        parameters=("u'", "u''"),
        definitions=("absolute_risk_aversion",),
        definition_signatures=("(u' u'' : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Risk.ArrowPrattARA

/-- [state the claim about absolute risk aversion here] -/
theorem your_theorem_name
    (u' u'' : ℝ) (hu' : u' ≠ 0) :
    absolute_risk_aversion u' u'' = -(u'') / u' := by
  sorry""",
        tactic_hint="simp [absolute_risk_aversion]; ring",
    )
)
_register(
    PreambleEntry(
        name="budget_set",
        lean_module="LeanEcon.Preamble.Consumer.BudgetSet",
        description="Budget set for two goods under linear budget constraint",
        keywords=("budget set", "budget constraint", "feasible set"),
        parameters=("p₁", "p₂", "m"),
        definitions=("in_budget_set",),
        definition_signatures=("(p₁ p₂ m x₁ x₂ : ℝ) : Prop",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.BudgetSet

/-- [state the claim about the budget set here] -/
theorem your_theorem_name
    (p₁ p₂ m x₁ x₂ : ℝ) (h : p₁ * x₁ + p₂ * x₂ ≤ m) :
    in_budget_set p₁ p₂ m x₁ x₂ := by
  sorry""",
        tactic_hint="simp [in_budget_set]; linarith",
        skill_ref="PREAMBLE_CATALOG#budget_set",
    )
)
_register(
    PreambleEntry(
        name="geometric_series",
        lean_module="LeanEcon.Preamble.Dynamic.GeometricSeries",
        description="Geometric series and its closed-form partial sum",
        keywords=("geometric series", "geometric sum", "present value"),
        parameters=("a", "r", "n"),
        definitions=("geometric_partial_sum",),
        definition_signatures=("(a r : ℝ) (n : ℕ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Dynamic.GeometricSeries

/-- [state the claim about the geometric series here] -/
theorem your_theorem_name
    (a r : ℝ) (n : ℕ) (hr : r ≠ 1) :
    geometric_partial_sum a r n = a * (1 - r ^ n) / (1 - r) := by
  sorry""",
        tactic_hint="simp [geometric_partial_sum]; ring",
    )
)
_register(
    PreambleEntry(
        name="extreme_value_theorem",
        lean_module="LeanEcon.Preamble.Optimization.ExtremeValueTheorem",
        description="Extreme value theorem (Weierstrass) via Mathlib IsCompact.exists_isMaxOn",
        keywords=(
            "extreme value",
            "extreme value theorem",
            "weierstrass",
            "maximum theorem",
            "attains maximum",
            "attains minimum",
            "compact",
            "continuous maximum",
            "berge",
            "concave",
            "convex",
            "strictly concave",
            "strictly convex",
            "concavity",
            "convexity",
            "maximum",
            "minimum",
            "optimization",
        ),
        parameters=("f", "S"),
        definitions=(),
        definition_signatures=(),
        proven_lemmas=(
            "continuous_attains_max_on_compact",
            "continuous_attains_min_on_compact",
        ),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Optimization.ExtremeValueTheorem

/-- [state a claim using the extreme value theorem here] -/
theorem your_theorem_name
    {α : Type*} [TopologicalSpace α]
    {s : Set α} {f : α → ℝ}
    (hs : IsCompact s) (hne : s.Nonempty) (hf : ContinuousOn f s) :
    ∃ x ∈ s, IsMaxOn f s x := by
  sorry""",
        tactic_hint="exact hs.exists_isMaxOn hne hf",
        skill_ref="PREAMBLE_CATALOG#extreme_value_theorem",
    )
)
_register(
    PreambleEntry(
        name="pareto_efficiency",
        lean_module="LeanEcon.Preamble.Welfare.ParetoEfficiency",
        description="Pareto efficiency and Pareto dominance for finite economies",
        keywords=(
            "pareto",
            "pareto efficient",
            "pareto optimal",
            "pareto dominance",
            "welfare",
            "efficiency",
            "first welfare",
            "second welfare",
        ),
        parameters=("n", "u", "X"),
        definitions=("pareto_dominates", "pareto_efficient"),
        definition_signatures=(
            "{n : ℕ} {X : Type*} (u : Fin n → X → ℝ) (x y : X) : Prop",
            "{n : ℕ} {X : Type*} (u : Fin n → X → ℝ) (feasible : Set X) (x : X) : Prop",
        ),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Welfare.ParetoEfficiency

/-- [state the claim about Pareto efficiency here] -/
theorem your_theorem_name
    {n : ℕ} {X : Type*} (u : Fin n → X → ℝ)
    (feasible : Set X) (x : X) :
    pareto_efficient u feasible x ↔
      x ∈ feasible ∧ ∀ y, y ∈ feasible → ¬pareto_dominates u x y := by
  sorry""",
        tactic_hint="simp [pareto_efficient, pareto_dominates]",
        skill_ref="PREAMBLE_CATALOG#pareto_efficiency",
    )
)
_register(
    PreambleEntry(
        name="social_welfare_function",
        lean_module="LeanEcon.Preamble.Welfare.SocialWelfareFunction",
        description="Utilitarian social welfare function as weighted sum of utilities",
        keywords=(
            "social welfare function",
            "swf",
            "utilitarian",
            "welfare function",
            "weighted sum utilities",
        ),
        parameters=("n", "w", "u"),
        definitions=("utilitarian_swf",),
        definition_signatures=(
            "{n : ℕ} {X : Type*} (w : Fin n → ℝ) (u : Fin n → X → ℝ) (x : X) : ℝ",
        ),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Welfare.SocialWelfareFunction

/-- [state the claim about the utilitarian SWF here] -/
theorem your_theorem_name
    {n : ℕ} {X : Type*} (w : Fin n → ℝ) (u : Fin n → X → ℝ) (x : X) :
    utilitarian_swf w u x = Finset.univ.sum (fun i => w i * u i x) := by
  sorry""",
        tactic_hint="simp [utilitarian_swf]",
    )
)
_register(
    PreambleEntry(
        name="marshallian_demand",
        lean_module="LeanEcon.Preamble.Consumer.MarshallianDemand",
        description="Marshallian demand functions for two-good Cobb-Douglas preferences",
        keywords=(
            "marshallian demand",
            "ordinary demand",
            "demand function",
            "optimal consumption",
            "utility maximization demand",
            "budget constraint",
            "tangency condition",
        ),
        auto_keywords=(
            "marshallian demand",
            "ordinary demand",
            "demand function",
            "optimal consumption",
            "utility maximization demand",
        ),
        parameters=("α", "m", "p₁", "p₂"),
        definitions=("marshallian_demand_good1", "marshallian_demand_good2"),
        definition_signatures=(
            "(α m p₁ : ℝ) : ℝ",
            "(α m p₂ : ℝ) : ℝ",
        ),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.MarshallianDemand

/-- [state the claim about Marshallian demand here] -/
theorem your_theorem_name
    (α m p₁ p₂ : ℝ) (hα : 0 < α) (hα1 : α < 1) (hm : m > 0) (hp₁ : p₁ > 0) (hp₂ : p₂ > 0) :
    marshallian_demand_good1 α m p₁ = α * m / p₁ := by
  sorry""",
        tactic_hint="simp [marshallian_demand_good1]; ring",
        skill_ref="PREAMBLE_CATALOG#marshallian_demand",
    )
)
_register(
    PreambleEntry(
        name="general_equilibrium_walras",
        lean_module="LeanEcon.Preamble.GeneralEquilibrium.WalrasLaw",
        description="Walras law for two-good Cobb-Douglas Marshallian demand",
        keywords=(
            "walras",
            "walras law",
            "walrasian equilibrium",
            "general equilibrium",
            "market clearing",
            "excess demand",
            "budget exhaustion",
        ),
        auto_keywords=(
            "walras",
            "walras law",
            "general equilibrium",
            "walrasian equilibrium",
            "excess demand",
        ),
        parameters=("α", "m", "p₁", "p₂"),
        definitions=(),
        definition_signatures=(),
        proven_lemmas=("ag_walras_law",),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.GeneralEquilibrium.WalrasLaw

/-- [state the Walras law claim here] -/
theorem your_theorem_name
    (α m p₁ p₂ : ℝ)
    (hp₁ : p₁ ≠ 0) (hp₂ : p₂ ≠ 0) :
    marshallian_demand_good1 α m p₁ * p₁ +
    marshallian_demand_good2 α m p₂ * p₂ = m := by
  sorry""",
        tactic_hint="field_simp [hp₁, hp₂]; ring",
        skill_ref="PREAMBLE_CATALOG#general_equilibrium_walras",
    )
)
_register(
    PreambleEntry(
        name="indirect_utility",
        lean_module="LeanEcon.Preamble.Consumer.IndirectUtility",
        description="Indirect utility function for Cobb-Douglas preferences",
        keywords=(
            "indirect utility",
            "indirect utility function",
            "value function consumer",
            "v(p,m)",
            "homogeneous of degree 1",
            "homogeneity",
            "income scaling",
            "scaling income",
            "scales utility",
            "linearity in income",
            "income",
        ),
        auto_keywords=(
            "indirect utility",
            "homogeneous of degree 1",
            "homogeneity",
            "income scaling",
            "scaling income",
            "scales utility",
            "linearity in income",
            "income",
        ),
        parameters=("α", "p₁", "p₂", "m"),
        definitions=("indirect_utility_cd",),
        definition_signatures=("(α p₁ p₂ m : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Consumer.IndirectUtility

/-- [state the claim about indirect utility here].
For Cobb-Douglas indirect utility, the relevant claim is often that the
function is linear (homogeneous of degree 1) in income: scaling income by λ
scales utility by λ. -/
theorem your_theorem_name
    (α p₁ p₂ lam m : ℝ) (hα : 0 < α) (hα1 : α < 1) (hp₁ : p₁ > 0) (hp₂ : p₂ > 0)
    (hm : m > 0) :
    indirect_utility_cd α p₁ p₂ (lam * m) = lam * indirect_utility_cd α p₁ p₂ m := by
  sorry""",
        tactic_hint="simp [indirect_utility_cd]; ring",
        skill_ref="PREAMBLE_CATALOG#indirect_utility",
    )
)
_register(
    PreambleEntry(
        name="profit_function",
        lean_module="LeanEcon.Preamble.Producer.ProfitFunction",
        description="Profit function for a single-input firm",
        keywords=(
            "profit function",
            "profit maximization",
            "firm profit",
            "producer surplus",
            "marginal cost",
            "marginal revenue",
            "supply function",
            "first order condition",
            "foc",
        ),
        parameters=("p", "w", "A", "α"),
        definitions=("profit",),
        definition_signatures=("(p w A α x_star : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Producer.ProfitFunction

/-- [state the claim about the profit function here] -/
theorem your_theorem_name
    (p w A α x_star : ℝ) (hp : p > 0) (hw : w > 0) (hA : A > 0) (hα : 0 < α) (hα1 : α < 1) :
    profit p w A α x_star = p * (A * Real.rpow x_star α) - w * x_star := by
  sorry""",
        tactic_hint="simp [profit]; ring",
    )
)
_register(
    PreambleEntry(
        name="cost_function",
        lean_module="LeanEcon.Preamble.Producer.CostFunction",
        description="Cost function for Cobb-Douglas technology",
        keywords=(
            "cost function",
            "cost minimization",
            "conditional factor demand",
            "total cost",
            "marginal cost",
            "average cost",
            "isoquant",
            "shephard",
            "shephard's lemma",
        ),
        parameters=("w", "r", "A", "α", "q"),
        definitions=("cost_cd",),
        definition_signatures=("(w r A α q : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Producer.CostFunction

/-- [state the claim about the cost function here] -/
theorem your_theorem_name
    (w r A α q : ℝ) (hw : w > 0) (hr : r > 0) (hA : A > 0) (hα : 0 < α) (hα1 : α < 1)
    (hq : q > 0) :
    cost_cd w r A α q =
      q * Real.rpow (w / (1 - α)) (1 - α) * Real.rpow (r / α) α / A := by
  sorry""",
        tactic_hint="simp [cost_cd]; ring",
    )
)
_register(
    PreambleEntry(
        name="bellman_equation",
        lean_module="LeanEcon.Preamble.Dynamic.BellmanEquation",
        description="Bellman equation RHS for deterministic dynamic programming",
        keywords=(
            "bellman",
            "bellman equation",
            "bellman operator",
            "contraction",
            "dynamic programming",
            "value function iteration",
            "euler equation",
            "optimal savings",
            "ramsey",
            "cake eating",
            "recursive",
            "value function",
            "optimal control",
        ),
        auto_keywords=(
            "bellman",
            "bellman equation",
            "bellman operator",
            "contraction",
            "value function",
            "dynamic programming",
        ),
        parameters=("V", "u", "f", "β"),
        definitions=("bellman_rhs",),
        definition_signatures=("(u : ℝ → ℝ) (β : ℝ) (V : ℝ → ℝ) (k k' : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Dynamic.BellmanEquation

/-- [state the claim about the Bellman equation here].
For contraction-style claims, the target can be the pointwise norm inequality
`‖bellman_rhs u β V₁ k k' - bellman_rhs u β V₂ k k'‖ ≤ β * ‖V₁ k' - V₂ k'‖`
under the hypotheses `0 < β` and `β < 1`. -/
theorem your_theorem_name
    (u : ℝ → ℝ) (β : ℝ) (V : ℝ → ℝ) (k k' : ℝ) (hβ : 0 < β) (hβ1 : β < 1) :
    bellman_rhs u β V k k' = u (k - k') + β * V k' := by
  sorry""",
        tactic_hint="simp [bellman_rhs]",
        skill_ref="PREAMBLE_CATALOG#bellman_equation",
    )
)
_register(
    PreambleEntry(
        name="discount_factor",
        lean_module="LeanEcon.Preamble.Dynamic.DiscountFactor",
        description="Discount-factor predicate and present value with geometric discounting",
        keywords=(
            "present value",
            "discount factor",
            "discounting",
            "geometric discounting",
            "net present value",
        ),
        parameters=("x", "beta", "T"),
        definitions=("discount_factor", "present_value_constant"),
        definition_signatures=(
            "(beta : ℝ) : Prop",
            "(x beta : ℝ) (T : ℕ) : ℝ",
        ),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Dynamic.DiscountFactor

/-- [state the claim about the discount factor / present value here] -/
theorem your_theorem_name
    (x beta : ℝ) (T : ℕ) (hbeta : discount_factor beta) :
    present_value_constant x beta T = x * (1 - beta ^ T) / (1 - beta) := by
  sorry""",
        tactic_hint="simp [present_value_constant]; ring",
    )
)
_register(
    PreambleEntry(
        name="expected_payoff",
        lean_module="LeanEcon.Preamble.GameTheory.ExpectedPayoff",
        description="Expected payoff for 2x2 games with mixed strategies",
        keywords=(
            "expected payoff",
            "mixed strategy",
            "mixed strategies",
            "2x2 game",
            "game payoff",
            "bilinear",
        ),
        parameters=("u", "p", "q"),
        definitions=("expected_payoff_2x2",),
        definition_signatures=("(u₁₁ u₁₂ u₂₁ u₂₂ p q : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.GameTheory.ExpectedPayoff

/-- [state the claim about the expected payoff here] -/
theorem your_theorem_name
    (u₁₁ u₁₂ u₂₁ u₂₂ p q : ℝ)
    (hp : 0 ≤ p) (hp1 : p ≤ 1) (hq : 0 ≤ q) (hq1 : q ≤ 1) :
    expected_payoff_2x2 u₁₁ u₁₂ u₂₁ u₂₂ p q =
      p * q * u₁₁ + p * (1 - q) * u₁₂ + (1 - p) * q * u₂₁ + (1 - p) * (1 - q) * u₂₂ := by
  sorry""",
        tactic_hint="simp [expected_payoff_2x2]; ring",
        skill_ref="PREAMBLE_CATALOG#expected_payoff",
    )
)
_register(
    PreambleEntry(
        name="solow_steady_state",
        lean_module="LeanEcon.Preamble.Macro.SolowSteadyState",
        description="Solow model investment and depreciation definitions",
        keywords=(
            "solow",
            "solow model",
            "steady state",
            "steady-state",
            "solow steady state",
            "capital accumulation",
            "growth model",
            "golden rule",
            "convergence",
            "per capita",
        ),
        parameters=("s", "A", "n", "g", "δ", "α"),
        definitions=("solow_investment", "solow_depreciation"),
        definition_signatures=(
            "(s A k α : ℝ) : ℝ",
            "(n g δ k : ℝ) : ℝ",
        ),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Macro.SolowSteadyState

/-- [state the claim about the Solow steady state here] -/
theorem your_theorem_name
    (s A k α n g δ : ℝ) (hs : 0 < s) (hs1 : s < 1) (hA : A > 0) (hk : k > 0)
    (hα : 0 < α) (hα1 : α < 1) :
    solow_investment s A k α = solow_depreciation n g δ k := by
  sorry""",
        tactic_hint="simp [solow_investment, solow_depreciation]; ring",
        skill_ref="PREAMBLE_CATALOG#solow_steady_state",
    )
)
_register(
    PreambleEntry(
        name="phillips_curve",
        lean_module="LeanEcon.Preamble.Macro.PhillipsCurve",
        description="New Keynesian Phillips Curve with nkpc function and identity theorem",
        keywords=("phillips curve", "nkpc", "new keynesian", "inflation", "output gap"),
        parameters=("π", "β", "κ", "x"),
        definitions=("nkpc",),
        definition_signatures=("(β π_next κ x : ℝ) : ℝ",),
        proven_lemmas=(),
        theorem_template="""\
import Mathlib
import LeanEcon.Preamble.Macro.PhillipsCurve

/-- [state the claim about the New Keynesian Phillips Curve here] -/
theorem your_theorem_name
    (β π_next κ x : ℝ) :
    nkpc β π_next κ x = β * π_next + κ * x := by
  sorry""",
        tactic_hint="simp [nkpc]; ring",
        skill_ref="PREAMBLE_CATALOG#phillips_curve",
    )
)
_register(
    PreambleEntry(
        name="indirect_utility_v2",
        lean_module="LeanEcon.Preamble.Consumer.IndirectUtilityV2",
        description="Indirect utility as the supremum of utility over a budget correspondence",
        keywords=("indirect utility", "value function", "budget correspondence", "consumer"),
        parameters=("u", "budget", "p", "w"),
        planner_metadata={},
        definitions=("indirect_utility_v2",),
        definition_signatures=(
            "{X : Type*} (u : X → ℝ) (budget : ℝ → ℝ → Set X) (p w : ℝ) : ℝ",
        ),
    )
)
_register(
    PreambleEntry(
        name="expenditure_function_v2",
        lean_module="LeanEcon.Preamble.Consumer.ExpenditureFunctionV2",
        description=(
            "Expenditure function as the infimum expenditure needed to hit a utility target"
        ),
        keywords=("expenditure function", "dual", "cost minimization", "upper contour set"),
        parameters=("expenditure", "u", "target"),
        planner_metadata={},
        definitions=("expenditure_function_v2",),
        definition_signatures=(
            "{X : Type*} (expenditure : X → ℝ) (u : X → ℝ) (target : ℝ) : ℝ",
        ),
    )
)
_register(
    PreambleEntry(
        name="roys_identity",
        lean_module="LeanEcon.Preamble.Consumer.RoysIdentity",
        description=(
            "Roy's identity as a derivative-based relationship between indirect utility and demand"
        ),
        keywords=("roy", "roy's identity", "marshallian demand", "indirect utility", "derivative"),
        parameters=("v", "x", "p", "w"),
        planner_metadata={},
        definitions=("roys_identity",),
        definition_signatures=(
            "(v x : ℝ → ℝ → ℝ) (p w : ℝ) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="excess_demand",
        lean_module="LeanEcon.Preamble.GeneralEquilibrium.ExcessDemand",
        description="Aggregate excess demand as the sum of individual demands minus endowments",
        keywords=("excess demand", "aggregate demand", "endowment", "general equilibrium"),
        parameters=("demand", "endowment", "p"),
        planner_metadata={},
        definitions=("excess_demand",),
        definition_signatures=(
            "{ι : Type*} [Fintype ι] (demand : ι → ℝ → ℝ) (endowment : ι → ℝ) (p : ℝ) : ℝ",
        ),
    )
)
_register(
    PreambleEntry(
        name="walras_law",
        lean_module="LeanEcon.Preamble.GeneralEquilibrium.WalrasLaw",
        description="Walras' law as zero value of prices against aggregate excess demand",
        keywords=("walras law", "walras", "market clearing", "general equilibrium"),
        parameters=("prices", "excessDemand"),
        planner_metadata={},
        definitions=("walras_law",),
        definition_signatures=(
            "{ι : Type*} [Fintype ι] (prices excessDemand : ι → ℝ) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="pareto_optimal",
        lean_module="LeanEcon.Preamble.Welfare.ParetoOptimal",
        description="Pareto optimality as a thin economist-named wrapper around Pareto efficiency",
        keywords=("pareto optimal", "pareto optimum", "pareto efficient", "welfare"),
        parameters=("u", "feasible", "x"),
        planner_metadata={},
        definitions=("pareto_optimal",),
        definition_signatures=(
            "{n : ℕ} {X : Type*} (u : Fin n → X → ℝ) (feasible : Set X) (x : X) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="first_welfare_thm",
        lean_module="LeanEcon.Preamble.Welfare.FirstWelfareTheorem",
        description=(
            "First welfare theorem setup linking competitive equilibrium to Pareto optimality"
        ),
        keywords=("first welfare theorem", "competitive equilibrium", "pareto optimal", "welfare"),
        parameters=("competitiveEquilibrium", "u", "feasible", "x"),
        planner_metadata={},
        definitions=("first_welfare_thm",),
        definition_signatures=(
            "{n : ℕ} {X : Type*} (competitiveEquilibrium : X → Prop) "
            "(u : Fin n → X → ℝ) (feasible : Set X) (x : X) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="lagrangian",
        lean_module="LeanEcon.Preamble.Optimization.Lagrangian",
        description="Finite-dimensional Lagrangian for inequality-constrained optimization",
        keywords=("lagrangian", "multiplier", "constraint", "optimization"),
        parameters=("objective", "constraints", "x", "lam"),
        planner_metadata={},
        definitions=("lagrangian",),
        definition_signatures=(
            "{n m : ℕ} (objective : (Fin n → ℝ) → ℝ) "
            "(constraints : Fin m → (Fin n → ℝ) → ℝ) "
            "(x : Fin n → ℝ) (lam : Fin m → ℝ) : ℝ",
        ),
    )
)
_register(
    PreambleEntry(
        name="kkt_conditions",
        lean_module="LeanEcon.Preamble.Optimization.KKTConditions",
        description=(
            "KKT conditions packaged as stationarity, feasibility, dual feasibility, "
            "and slackness"
        ),
        keywords=("kkt", "kuhn tucker", "complementary slackness", "optimization"),
        parameters=("stationarity", "constraints", "lam", "x"),
        planner_metadata={},
        definitions=("kkt_conditions",),
        definition_signatures=(
            "{n m : ℕ} (stationarity : Prop) "
            "(constraints : Fin m → (Fin n → ℝ) → ℝ) "
            "(lam : Fin m → ℝ) (x : Fin n → ℝ) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="envelope_theorem",
        lean_module="LeanEcon.Preamble.Optimization.EnvelopeTheorem",
        description=(
            "Envelope theorem setup equating value derivatives with partial "
            "Lagrangian derivatives"
        ),
        keywords=("envelope theorem", "value function derivative", "lagrangian"),
        parameters=("value", "partialL", "theta"),
        planner_metadata={},
        definitions=("envelope_theorem",),
        definition_signatures=(
            "(value partialL : ℝ → ℝ) (theta : ℝ) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="convex_constraint_set",
        lean_module="LeanEcon.Preamble.Optimization.ConvexConstraintSet",
        description=(
            "Constraint set defined by finitely many inequality restrictions in finite dimension"
        ),
        keywords=(
            "convex constraint set",
            "feasible set",
            "inequality constraints",
            "optimization",
        ),
        parameters=("constraints",),
        planner_metadata={},
        definitions=("convex_constraint_set",),
        definition_signatures=(
            "{n m : ℕ} (constraints : Fin m → (Fin n → ℝ) → ℝ) : Set (Fin n → ℝ)",
        ),
    )
)
_register(
    PreambleEntry(
        name="bellman_operator",
        lean_module="LeanEcon.Preamble.Dynamic.BellmanOperator",
        description="Bellman operator for deterministic dynamic programming",
        keywords=("bellman operator", "dynamic programming", "value iteration", "recursive"),
        parameters=("feasible", "reward", "transition", "beta", "V", "x"),
        planner_metadata={},
        definitions=("bellman_operator",),
        definition_signatures=(
            "{S A : Type*} (feasible : S → Set A) (reward : S → A → ℝ) "
            "(transition : S → A → S) (beta : ℝ) (V : S → ℝ) (x : S) : ℝ",
        ),
    )
)
_register(
    PreambleEntry(
        name="value_function",
        lean_module="LeanEcon.Preamble.Dynamic.ValueFunction",
        description="Value function as the fixed point of a contracting Bellman operator",
        keywords=("value function", "fixed point", "contracting", "dynamic programming"),
        parameters=("T", "hT"),
        planner_metadata={},
        definitions=("value_function",),
        definition_signatures=(
            "{V : Type*} [MetricSpace V] [CompleteSpace V] [Nonempty V] "
            "{K : NNReal} (T : V → V) (hT : ContractingWith K T) : V",
        ),
    )
)
_register(
    PreambleEntry(
        name="policy_function",
        lean_module="LeanEcon.Preamble.Dynamic.PolicyFunction",
        description="Policy correspondence returning Bellman-maximizing actions",
        keywords=("policy function", "argmax", "bellman", "dynamic programming"),
        parameters=("feasible", "reward", "transition", "beta", "V", "x"),
        planner_metadata={},
        definitions=("policy_function",),
        definition_signatures=(
            "{S A : Type*} (feasible : S → Set A) (reward : S → A → ℝ) "
            "(transition : S → A → S) (beta : ℝ) (V : S → ℝ) (x : S) : Set A",
        ),
    )
)
_register(
    PreambleEntry(
        name="transition_law",
        lean_module="LeanEcon.Preamble.Dynamic.TransitionLaw",
        description="State transition law from current state and action to next state",
        keywords=("transition law", "state transition", "law of motion", "dynamic"),
        parameters=("f",),
        planner_metadata={},
        definitions=("transition_law",),
        definition_signatures=(
            "{S A : Type*} (f : S → A → S) : S × A → S",
        ),
    )
)
_register(
    PreambleEntry(
        name="recursive_competitive_eq",
        lean_module="LeanEcon.Preamble.Dynamic.RecursiveCompetitiveEq",
        description=(
            "Recursive competitive equilibrium packaged as value, policy, and "
            "market-clearing objects"
        ),
        keywords=(
            "recursive competitive equilibrium",
            "recursive equilibrium",
            "policy",
            "value function",
        ),
        parameters=("S", "A", "M"),
        planner_metadata={},
        definitions=("recursive_competitive_eq",),
        definition_signatures=(
            "(S A M : Type*) : Type",
        ),
    )
)
_register(
    PreambleEntry(
        name="upper_hemicontinuous",
        lean_module="LeanEcon.Preamble.Analysis.UpperHemicontinuous",
        description="Upper hemicontinuity wrapper for set-valued correspondences",
        keywords=("upper hemicontinuous", "upper hemicontinuity", "correspondence", "analysis"),
        parameters=("F",),
        planner_metadata={},
        definitions=("upper_hemicontinuous",),
        definition_signatures=(
            "{α : Type*} {β : Type*} [TopologicalSpace α] [TopologicalSpace β] "
            "(F : α → Set β) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="normal_form_game",
        lean_module="LeanEcon.Preamble.GameTheory.NormalFormGame",
        description="Normal-form game with strategy spaces and payoff functions",
        keywords=("normal form game", "normal-form game", "strategic form", "game theory"),
        parameters=("ι",),
        planner_metadata={},
        definitions=("normal_form_game",),
        definition_signatures=(
            "(ι : Type*) : Type",
        ),
    )
)
_register(
    PreambleEntry(
        name="best_response",
        lean_module="LeanEcon.Preamble.GameTheory.BestResponse",
        description="Best-response correspondence in a normal-form game",
        keywords=("best response", "best-response", "argmax", "game theory"),
        parameters=("G", "player", "profile"),
        planner_metadata={},
        definitions=("best_response",),
        definition_signatures=(
            "{ι : Type*} [DecidableEq ι] (G : normal_form_game ι) "
            "(player : ι) (profile : (i : ι) → G.Strategy i) : Set (G.Strategy player)",
        ),
    )
)
_register(
    PreambleEntry(
        name="nash_equilibrium_pure",
        lean_module="LeanEcon.Preamble.GameTheory.NashEquilibriumPure",
        description="Pure-strategy Nash equilibrium",
        keywords=("nash equilibrium", "pure strategy nash", "equilibrium", "game theory"),
        parameters=("G", "profile"),
        planner_metadata={},
        definitions=("nash_equilibrium_pure",),
        definition_signatures=(
            "{ι : Type*} [DecidableEq ι] (G : normal_form_game ι) "
            "(profile : (i : ι) → G.Strategy i) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="dominant_strategy",
        lean_module="LeanEcon.Preamble.GameTheory.DominantStrategy",
        description="Dominant pure strategy in a normal-form game",
        keywords=("dominant strategy", "dominant-strategy", "game theory", "mechanism design"),
        parameters=("G", "player", "s"),
        planner_metadata={},
        definitions=("dominant_strategy",),
        definition_signatures=(
            "{ι : Type*} [DecidableEq ι] (G : normal_form_game ι) "
            "(player : ι) (s : G.Strategy player) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="berge_maximum",
        lean_module="LeanEcon.Preamble.Analysis.BergeMaximum",
        description="Berge maximum theorem setup with upper hemicontinuity and continuity data",
        keywords=("berge maximum", "berge's maximum theorem", "argmax continuity", "analysis"),
        parameters=("X", "Y"),
        planner_metadata={},
        definitions=("berge_maximum",),
        definition_signatures=(
            "{X Y : Type*} [TopologicalSpace X] [TopologicalSpace Y] : Type",
        ),
    )
)
_register(
    PreambleEntry(
        name="single_crossing",
        lean_module="LeanEcon.Preamble.Analysis.SingleCrossing",
        description="Single-crossing property on ordered real decisions and parameters",
        keywords=("single crossing", "single-crossing", "monotone comparative statics", "analysis"),
        parameters=("f",),
        planner_metadata={},
        definitions=("single_crossing",),
        definition_signatures=(
            "(f : ℝ → ℝ → ℝ) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="supermodular_function",
        lean_module="LeanEcon.Preamble.Analysis.SupermodularFunction",
        description="Supermodularity on a distributive lattice",
        keywords=("supermodular", "supermodular function", "lattice", "comparative statics"),
        parameters=("f",),
        planner_metadata={},
        definitions=("supermodular_function",),
        definition_signatures=(
            "{α : Type*} [DistribLattice α] (f : α → ℝ) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="extensive_form_game",
        lean_module="LeanEcon.Preamble.GameTheory.ExtensiveFormGame",
        description="Extensive-form game with nodes, moves, transitions, and terminal payoffs",
        keywords=("extensive form game", "extensive-form game", "game tree", "sequential game"),
        parameters=("ι",),
        planner_metadata={},
        definitions=("extensive_form_game",),
        definition_signatures=(
            "(ι : Type*) : Type",
        ),
    )
)
_register(
    PreambleEntry(
        name="bayesian_game",
        lean_module="LeanEcon.Preamble.GameTheory.BayesianGame",
        description="Bayesian game with type spaces, prior, actions, and payoffs",
        keywords=("bayesian game", "incomplete information", "types", "game theory"),
        parameters=("ι",),
        planner_metadata={},
        definitions=("bayesian_game",),
        definition_signatures=(
            "(ι : Type*) : Type",
        ),
    )
)
_register(
    PreambleEntry(
        name="signaling_game",
        lean_module="LeanEcon.Preamble.GameTheory.SignalingGame",
        description="Signaling game with sender types, messages, receiver actions, and payoffs",
        keywords=("signaling game", "sender receiver", "message game", "information economics"),
        parameters=(),
        planner_metadata={},
        definitions=("signaling_game",),
        definition_signatures=(
            ": Type",
        ),
    )
)
_register(
    PreambleEntry(
        name="subgame_perfect_equilibrium",
        lean_module="LeanEcon.Preamble.GameTheory.SubgamePerfectEquilibrium",
        description="Subgame-perfect equilibrium predicate for extensive-form games",
        keywords=("subgame perfect equilibrium", "spe", "equilibrium refinement", "game theory"),
        parameters=("G", "Strategy", "subgame_root", "continuation_equilibrium", "σ"),
        planner_metadata={},
        definitions=("subgame_perfect_equilibrium",),
        definition_signatures=(
            "{ι : Type*} (G : extensive_form_game ι) (Strategy : ι → Type*) "
            "(subgame_root : G.Node → Prop) "
            "(continuation_equilibrium : G.Node → ((i : ι) → Strategy i) → Prop) "
            "(σ : (i : ι) → Strategy i) : Prop",
        ),
    )
)
_register(
    PreambleEntry(
        name="bayesian_nash_equilibrium",
        lean_module="LeanEcon.Preamble.GameTheory.BayesianNashEquilibrium",
        description="Bayesian Nash equilibrium predicate for Bayesian games",
        keywords=("bayesian nash equilibrium", "bne", "bayesian equilibrium", "game theory"),
        parameters=("G", "Strategy", "no_profitable_deviation", "σ"),
        planner_metadata={},
        definitions=("bayesian_nash_equilibrium",),
        definition_signatures=(
            "{ι : Type*} (G : bayesian_game ι) (Strategy : ι → Type*) "
            "(no_profitable_deviation : ((i : ι) → Strategy i) → ι → Prop) "
            "(σ : (i : ι) → Strategy i) : Prop",
        ),
    )
)


def _strip_lean_header(lean_code: str) -> str:
    """Drop leading import/open lines before using Lean source as prompt context."""
    lines = lean_code.splitlines()
    index = 0

    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines) and lines[index].strip().startswith(("import ", "open ")):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1

    return "\n".join(lines[index:]).strip()


def read_preamble_source(entry: PreambleEntry, *, strip_header: bool = True) -> str:
    """Read the Lean source backing a preamble entry."""
    source = entry.lean_path.read_text(encoding="utf-8")
    return _strip_lean_header(source) if strip_header else source


def _keyword_weight(keyword: str) -> int:
    cleaned = keyword.replace("-", " ").strip()
    if " " in cleaned:
        return 3
    return 1


def rank_matching_preambles(
    claim_text: str,
    *,
    auto: bool = False,
) -> list[tuple[PreambleEntry, int]]:
    """Return preamble matches ordered by weighted keyword relevance."""
    normalized = claim_text.lower()
    ranked: list[tuple[PreambleEntry, int]] = []
    for entry in PREAMBLE_LIBRARY.values():
        keywords = entry.auto_keywords if auto and entry.auto_keywords else entry.keywords
        score = sum(_keyword_weight(keyword) for keyword in keywords if keyword in normalized)
        if score > 0:
            ranked.append((entry, score))
    return sorted(
        ranked,
        key=lambda item: (-item[1], item[0].name),
    )


def find_matching_preambles(claim_text: str) -> list[PreambleEntry]:
    """Return all preamble entries whose keywords match the claim text."""
    return [entry for entry, _score in rank_matching_preambles(claim_text)]


def build_preamble_block(entries: list[PreambleEntry]) -> str:
    """Concatenate raw Lean source snippets for prompt-time preamble context."""
    if not entries:
        return ""

    seen_modules: set[str] = set()
    parts: list[str] = []
    for entry in entries:
        if entry.lean_module in seen_modules:
            continue
        seen_modules.add(entry.lean_module)
        source = read_preamble_source(entry)
        if source:
            parts.append(source)
    return "\n\n".join(parts) + ("\n" if parts else "")


def build_preamble_imports(entries: list[PreambleEntry]) -> list[str]:
    """Build deduplicated Lean module names for the selected entries."""
    imports: list[str] = []
    seen_modules: set[str] = set()
    for entry in entries:
        if entry.lean_module in seen_modules:
            continue
        seen_modules.add(entry.lean_module)
        imports.append(entry.lean_module)
    return imports


def get_preamble_entries(names: list[str]) -> list[PreambleEntry]:
    """Look up preamble entries by name. Silently skips unknown names."""
    entries: list[PreambleEntry] = []
    seen_names: set[str] = set()
    for name in names:
        if name in seen_names:
            continue
        entry = PREAMBLE_LIBRARY.get(name)
        if entry is None:
            continue
        entries.append(entry)
        seen_names.add(name)
    return entries


def build_preamble_catalog_summary() -> str:
    """Compact text listing of all preamble modules for LLM context."""
    return "\n".join(f"- {entry.name}: {entry.description}" for entry in PREAMBLE_LIBRARY.values())
