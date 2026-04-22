"""
Metadata index for LeanEcon's reusable preamble modules.

The Lean source of truth lives under `lean_workspace/LeanEcon/Preamble/`.
This Python module stores the prompt-time lookup metadata for the v3 rebuild.
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
    definitions: tuple[str, ...] = ()
    definition_signatures: tuple[str, ...] = ()
    proven_lemmas: tuple[str, ...] = ()
    theorem_template: str | None = None
    tactic_hint: str | None = None
    skill_ref: str | None = None

    @property
    def lean_path(self) -> Path:
        return LEAN_WORKSPACE / Path(*self.lean_module.split(".")).with_suffix(".lean")

    @property
    def planner_skill_hint(self) -> str | None:
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

    PREAMBLE_LIBRARY[entry.name] = replace(
        entry,
        planner_metadata=planner_metadata,
        proven_lemmas=(),
        theorem_template=None,
        tactic_hint=None,
        skill_ref=entry.skill_ref,
    )


_register(
    PreambleEntry(
        name="measure",
        lean_module="LeanEcon.Preamble.Foundations.Primitives.Measure",
        description="Economic measures and the null-event lemma.",
        keywords=("measure", "probability", "mass", "measurable"),
        auto_keywords=("measure", "probability", "mass"),
        parameters=("α", "μ"),
        definitions=("EconomicMeasure",),
        definition_signatures=("(α : Type*) [MeasurableSpace α] : Type",),
        proven_lemmas=("economicMeasure_empty",),
        tactic_hint="simp",
        planner_metadata={
            "concepts": ["measure", "measurable_space", "economic_measure"],
            "textbook_source": "MWG App. M",
            "status": "proven",
            "tactic_hints": ["exact economicMeasure_empty μ", "simp"],
            "related": ["topological_space", "continuous_preference"],
        },
    )
)
_register(
    PreambleEntry(
        name="topological_space",
        lean_module="LeanEcon.Preamble.Foundations.Primitives.TopologicalSpace",
        description="Commodity-space topology and constant-map continuity.",
        keywords=("topology", "topological", "continuous", "commodity"),
        auto_keywords=("topology", "continuous", "commodity"),
        parameters=("α", "β", "b"),
        definitions=("CommodityTopology",),
        definition_signatures=("(α : Type*) : Type",),
        proven_lemmas=("continuous_const_commodity",),
        tactic_hint="simpa using continuous_const",
        planner_metadata={
            "concepts": ["topological_space", "continuity", "commodity_space"],
            "textbook_source": "MWG Ch. 1",
            "status": "proven",
            "tactic_hints": ["simpa using continuous_const"],
            "related": ["measure", "continuous_preference"],
        },
    )
)
_register(
    PreambleEntry(
        name="continuous_preference",
        lean_module="LeanEcon.Preamble.Foundations.Preferences.ContinuousPreference",
        description="Continuous utility representations over commodity spaces.",
        keywords=("preference", "continuous", "utility", "bundle"),
        auto_keywords=("preference", "continuous", "utility"),
        parameters=("α", "u", "s"),
        definitions=("ContinuousPreference",),
        definition_signatures=(
            "[TopologicalSpace α] [TopologicalSpace ℝ] (u : α → ℝ) : Prop",
        ),
        proven_lemmas=("continuousPreference_continuousOn",),
        tactic_hint="exact hu.continuousOn",
        planner_metadata={
            "concepts": ["continuous_preference", "utility_representation", "continuity"],
            "textbook_source": "MWG Ch. 1",
            "status": "proven",
            "tactic_hints": ["exact hu.continuousOn"],
            "related": ["topological_space", "convex_preference"],
        },
    )
)
_register(
    PreambleEntry(
        name="convex_preference",
        lean_module="LeanEcon.Preamble.Foundations.Preferences.ConvexPreference",
        description="Convex utility representations on the full commodity space.",
        keywords=("convex", "preference", "mixture", "utility"),
        auto_keywords=("convex", "preference", "mixture"),
        parameters=("E", "u"),
        definitions=("ConvexPreference",),
        definition_signatures=("(u : E → ℝ) : Prop",),
        proven_lemmas=("convexPreference_convexOn_univ",),
        tactic_hint="exact hu",
        planner_metadata={
            "concepts": ["convex_preference", "convex_on", "mixtures"],
            "textbook_source": "MWG Ch. 3",
            "status": "proven",
            "tactic_hints": ["exact hu"],
            "related": ["continuous_preference", "constrained_optimization"],
        },
    )
)
_register(
    PreambleEntry(
        name="constrained_optimization",
        lean_module="LeanEcon.Preamble.Foundations.Optimization.ConstrainedOptimization",
        description="Feasible argmax certificates for constrained problems.",
        keywords=("constrained", "optimization", "feasible", "maximum"),
        auto_keywords=("optimization", "feasible", "maximum"),
        parameters=("α", "f", "feasible", "x"),
        definitions=("IsConstrainedMaximum",),
        definition_signatures=("(f : α → ℝ) (feasible : Set α) (x : α) : Prop",),
        proven_lemmas=(
            "IsConstrainedMaximum.feasible",
            "IsConstrainedMaximum.value_le",
        ),
        tactic_hint="exact hx.2 hy",
        planner_metadata={
            "concepts": ["constrained_optimization", "feasible_set", "argmax_certificate"],
            "textbook_source": "MWG Ch. 3",
            "status": "proven",
            "tactic_hints": ["exact hx.1", "exact hx.2 hy"],
            "related": ["convex_preference", "kuhn_tucker"],
        },
    )
)
_register(
    PreambleEntry(
        name="kuhn_tucker",
        lean_module="LeanEcon.Preamble.Foundations.Optimization.KuhnTucker",
        description="Kuhn-Tucker certificates with complementary slackness.",
        keywords=("kuhn", "tucker", "slackness", "multiplier"),
        auto_keywords=("kuhn", "tucker", "slackness"),
        parameters=("x", "g", "μ"),
        definitions=("KuhnTuckerPoint",),
        definition_signatures=("(x : α) (g : α → ι → ℝ) (μ : ι → ℝ) : Prop",),
        proven_lemmas=("KuhnTuckerPoint.complementary_slackness",),
        tactic_hint="exact hkt.slackness i",
        planner_metadata={
            "concepts": ["kuhn_tucker", "complementary_slackness", "shadow_price"],
            "textbook_source": "MWG Ch. 5",
            "status": "proven",
            "tactic_hints": [
                "exact hkt.slackness i",
                "exact KuhnTuckerPoint.complementary_slackness hkt i",
            ],
            "related": ["constrained_optimization", "fixed_point_theorem"],
        },
    )
)
_register(
    PreambleEntry(
        name="fixed_point_theorem",
        lean_module="LeanEcon.Preamble.Foundations.Equilibrium.FixedPointTheorem",
        description="Contraction-based fixed-point existence for equilibrium objects.",
        keywords=("fixed", "point", "contraction", "equilibrium"),
        auto_keywords=("fixed", "point", "contraction"),
        parameters=("α", "f", "hf"),
        proven_lemmas=(
            "exists_fixedPoint_of_contractingWith",
            "fixedPoint_isFixedPt",
        ),
        tactic_hint="exact ContractingWith.fixedPoint_isFixedPt (f := f) hf",
        planner_metadata={
            "concepts": ["fixed_point_theorem", "contraction_mapping", "equilibrium_existence"],
            "textbook_source": "MWG Ch. 17",
            "status": "proven",
            "tactic_hints": [
                "exact exists_fixedPoint_of_contractingWith hf",
                "exact fixedPoint_isFixedPt hf",
            ],
            "related": ["kuhn_tucker", "contraction_mapping", "nash_existence"],
        },
    )
)
_register(
    PreambleEntry(
        name="nash_existence",
        lean_module="LeanEcon.Preamble.Foundations.Equilibrium.NashExistence",
        description="Witness-based Nash equilibrium existence certificates.",
        keywords=("nash", "equilibrium", "game", "witness"),
        auto_keywords=("nash", "equilibrium", "game"),
        parameters=("Profile",),
        definitions=("HasNashEquilibrium",),
        definition_signatures=("(Profile : Type) : Type",),
        proven_lemmas=("nash_exists_of_witness",),
        tactic_hint="exact ⟨h.witness, h.is_nash⟩",
        planner_metadata={
            "concepts": ["nash_existence", "nash_equilibrium", "witness_certificate"],
            "textbook_source": "MWG Ch. 8",
            "status": "proven",
            "tactic_hints": ["exact ⟨h.witness, h.is_nash⟩", "exact nash_exists_of_witness h"],
            "related": ["fixed_point_theorem", "policy_iteration"],
        },
    )
)
_register(
    PreambleEntry(
        name="bellman_operator",
        lean_module="LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator",
        description="Deterministic Bellman operator with monotonicity lemma.",
        keywords=("bellman", "operator", "dynamic", "programming"),
        auto_keywords=("bellman", "operator", "dynamic"),
        parameters=("reward", "transition", "β"),
        definitions=("BellmanOperator",),
        definition_signatures=(
            "(reward : S → ℝ) (transition : S → S) (β : ℝ) : (S → ℝ) → (S → ℝ)",
        ),
        proven_lemmas=("BellmanOperator.monotone",),
        tactic_hint="simpa using add_le_add_left hmul (reward s)",
        planner_metadata={
            "concepts": ["bellman_operator", "dynamic_programming", "monotone_operator"],
            "textbook_source": "SLP Ch. 4",
            "status": "proven",
            "tactic_hints": ["exact BellmanOperator.monotone hβ hvw"],
            "related": ["contraction_mapping", "value_function", "policy_iteration"],
        },
    )
)
_register(
    PreambleEntry(
        name="contraction_mapping",
        lean_module="LeanEcon.Preamble.Foundations.DynamicProgramming.ContractionMapping",
        description="Global contractions and fixed-point existence.",
        keywords=("contraction", "mapping", "fixed", "recursive"),
        auto_keywords=("contraction", "fixed", "recursive"),
        parameters=("α", "f"),
        definitions=("IsContraction",),
        definition_signatures=("(f : α → α) : Prop",),
        proven_lemmas=("contraction_has_fixedPoint",),
        tactic_hint="rcases hf with ⟨K, hK⟩",
        planner_metadata={
            "concepts": ["contraction_mapping", "fixed_point", "recursive_solution"],
            "textbook_source": "SLP Ch. 4",
            "status": "proven",
            "tactic_hints": ["exact contraction_has_fixedPoint hf"],
            "related": ["fixed_point_theorem", "bellman_operator", "value_function"],
        },
    )
)
_register(
    PreambleEntry(
        name="value_function",
        lean_module="LeanEcon.Preamble.Foundations.DynamicProgramming.ValueFunction",
        description="Fixed-point value functions for contracting dynamic problems.",
        keywords=("value", "function", "bellman", "dynamic"),
        auto_keywords=("value", "function", "bellman"),
        parameters=("V", "T", "hT"),
        definitions=("ValueFunction",),
        definition_signatures=("(T : V → V) (hT : ContractingWith K T) : V",),
        proven_lemmas=("valueFunction_isFixedPt",),
        tactic_hint=(
            "simpa [ValueFunction] using ContractingWith.fixedPoint_isFixedPt (f := T) hT"
        ),
        planner_metadata={
            "concepts": ["value_function", "fixed_point", "bellman_solution"],
            "textbook_source": "SLP Ch. 4",
            "status": "proven",
            "tactic_hints": [
                "simpa [ValueFunction] using ContractingWith.fixedPoint_isFixedPt (f := T) hT"
            ],
            "related": ["bellman_operator", "contraction_mapping", "policy_iteration"],
        },
    )
)
_register(
    PreambleEntry(
        name="policy_iteration",
        lean_module="LeanEcon.Preamble.Foundations.DynamicProgramming.PolicyIteration",
        description="Policy-improvement ordering for recursive choice rules.",
        keywords=("policy", "iteration", "improvement", "recursive"),
        auto_keywords=("policy", "iteration", "improvement"),
        parameters=("criterion", "oldPolicy", "newPolicy"),
        definitions=("PolicyImproves",),
        definition_signatures=("(criterion : π → ℝ) (oldPolicy newPolicy : π) : Prop",),
        proven_lemmas=("policyImproves_refl",),
        tactic_hint="exact le_rfl",
        planner_metadata={
            "concepts": ["policy_iteration", "policy_improvement", "recursive_choice"],
            "textbook_source": "SLP Ch. 4",
            "status": "proven",
            "tactic_hints": ["exact policyImproves_refl criterion policy", "exact le_rfl"],
            "related": ["bellman_operator", "value_function", "nash_existence"],
        },
    )
)
_register(
    PreambleEntry(
        name="best_response",
        lean_module="LeanEcon.Preamble.GameTheory.NormalFormGames.BestResponse",
        description="Best-response predicate for fixed opponents in normal-form games.",
        keywords=("best response", "normal form", "game", "strategy"),
        auto_keywords=("best response", "normal form", "strategy"),
        parameters=("payoff", "opponents", "action"),
        definitions=("IsBestResponse",),
        definition_signatures=(
            "(payoff : Opponent → Action → ℝ) (opponents : Opponent) (action : Action) : Prop",
        ),
        proven_lemmas=("IsBestResponse.payoff_le",),
        tactic_hint="exact h alternative",
        planner_metadata={
            "concepts": ["best_response", "normal_form_game", "strategic_optimality"],
            "textbook_source": "MWG Ch. 8",
            "status": "proven",
            "tactic_hints": [
                "exact h alternative",
                "exact IsBestResponse.payoff_le h alternative",
            ],
            "related": ["nash_existence", "truthful_direct_mechanism"],
        },
    )
)
_register(
    PreambleEntry(
        name="truthful_direct_mechanism",
        lean_module="LeanEcon.Preamble.GameTheory.MechanismDesign.TruthfulDirectMechanism",
        description="Truthful direct-revelation mechanisms via dominant truthful reporting.",
        keywords=("truthful", "mechanism", "incentive compatible", "revelation"),
        auto_keywords=("truthful", "mechanism", "incentive compatible"),
        parameters=("utility", "allocation", "trueType", "misreport"),
        definitions=("IsTruthfulDirectMechanism",),
        definition_signatures=(
            "(utility : AgentType → Outcome → ℝ) (allocation : AgentType → Outcome) : Prop",
        ),
        proven_lemmas=("IsTruthfulDirectMechanism.truthful_is_best",),
        tactic_hint="exact h trueType misreport",
        planner_metadata={
            "concepts": ["mechanism_design", "truthful_revelation", "incentive_compatibility"],
            "textbook_source": "MWG Ch. 23",
            "status": "proven",
            "tactic_hints": [
                "exact h trueType misreport",
                "exact IsTruthfulDirectMechanism.truthful_is_best h trueType misreport",
            ],
            "related": ["best_response", "nash_existence"],
        },
    )
)
_register(
    PreambleEntry(
        name="walrasian_equilibrium",
        lean_module="LeanEcon.Preamble.GeneralEquilibrium.ArrowDebreu.WalrasianEquilibrium",
        description="Generic Walrasian equilibrium certificate with feasibility, optimality, and clearing.",
        keywords=("walrasian", "equilibrium", "market clearing", "arrow debreu"),
        auto_keywords=("walrasian", "equilibrium", "market clearing"),
        parameters=("isFeasible", "isOptimal", "excessDemand"),
        definitions=("WalrasianEquilibrium",),
        definition_signatures=(
            "(isFeasible : (Agent → Commodity → ℝ) → Prop) (isOptimal : (Commodity → ℝ) → (Agent → Commodity → ℝ) → Prop) (excessDemand : (Agent → Commodity → ℝ) → Commodity → ℝ) : Prop",
        ),
        proven_lemmas=("WalrasianEquilibrium.marketClearing",),
        tactic_hint="exact h.clears good",
        planner_metadata={
            "concepts": ["walrasian_equilibrium", "market_clearing", "arrow_debreu"],
            "textbook_source": "MWG Ch. 15",
            "status": "proven",
            "tactic_hints": [
                "exact h.clears good",
                "exact WalrasianEquilibrium.marketClearing h good",
            ],
            "related": ["walras_law", "fixed_point_theorem"],
        },
    )
)
_register(
    PreambleEntry(
        name="walras_law",
        lean_module="LeanEcon.Preamble.GeneralEquilibrium.ExistenceUniqueness.WalrasLaw",
        description="Walras' law as a zero-value condition on aggregate excess demand.",
        keywords=("walras law", "excess demand", "general equilibrium", "prices"),
        auto_keywords=("walras law", "excess demand", "general equilibrium"),
        parameters=("price", "excessDemand"),
        definitions=("SatisfiesWalrasLaw",),
        definition_signatures=(
            "[Fintype Commodity] (price excessDemand : Commodity → ℝ) : Prop",
        ),
        proven_lemmas=("satisfiesWalrasLaw_of_marketClearing",),
        tactic_hint="simp [SatisfiesWalrasLaw, hclear]",
        planner_metadata={
            "concepts": ["walras_law", "excess_demand", "equilibrium_existence"],
            "textbook_source": "MWG Ch. 17",
            "status": "proven",
            "tactic_hints": [
                "simp [SatisfiesWalrasLaw, hclear]",
                "exact satisfiesWalrasLaw_of_marketClearing price excessDemand hclear",
            ],
            "related": ["walrasian_equilibrium", "fixed_point_theorem"],
        },
    )
)
_register(
    PreambleEntry(
        name="discounted_lifetime_utility",
        lean_module="LeanEcon.Preamble.Macroeconomics.BusinessCycleModels.DiscountedLifetimeUtility",
        description="Finite-horizon discounted utility sums for intertemporal macro problems.",
        keywords=("discounted utility", "intertemporal", "macro", "business cycle"),
        auto_keywords=("discounted utility", "intertemporal", "business cycle"),
        parameters=("β", "utility", "horizon"),
        definitions=("DiscountedLifetimeUtility",),
        definition_signatures=("(β : ℝ) (utility : ℕ → ℝ) (horizon : ℕ) : ℝ",),
        proven_lemmas=(
            "discountedLifetimeUtility_zero",
            "discountedLifetimeUtility_succ",
        ),
        tactic_hint="simp [DiscountedLifetimeUtility, Finset.sum_range_succ]",
        planner_metadata={
            "concepts": ["discounted_utility", "intertemporal_choice", "business_cycle"],
            "textbook_source": "Ljungqvist-Sargent Ch. 1",
            "status": "proven",
            "tactic_hints": [
                "simp [DiscountedLifetimeUtility]",
                "simp [DiscountedLifetimeUtility, Finset.sum_range_succ]",
            ],
            "related": ["steady_state", "bellman_operator"],
        },
    )
)
_register(
    PreambleEntry(
        name="steady_state",
        lean_module="LeanEcon.Preamble.Macroeconomics.GrowthModels.SteadyState",
        description="Steady states as fixed points of macro laws of motion.",
        keywords=("steady state", "growth", "law of motion", "fixed point"),
        auto_keywords=("steady state", "growth", "fixed point"),
        parameters=("lawOfMotion", "state"),
        definitions=("IsSteadyState",),
        definition_signatures=("(lawOfMotion : State → State) (state : State) : Prop",),
        proven_lemmas=("IsSteadyState.iterate_eq",),
        tactic_hint="exact Function.iterate_fixed h periods",
        planner_metadata={
            "concepts": ["steady_state", "law_of_motion", "growth_model"],
            "textbook_source": "Acemoglu Ch. 8",
            "status": "proven",
            "tactic_hints": [
                "exact Function.iterate_fixed h periods",
                "exact IsSteadyState.iterate_eq h periods",
            ],
            "related": ["discounted_lifetime_utility", "value_function"],
        },
    )
)
_register(
    PreambleEntry(
        name="subgradient_certificate",
        lean_module="LeanEcon.Preamble.Optimization.SubgradientCertificate",
        description="Scalar subgradient certificates and zero-subgradient optimality.",
        keywords=("subgradient", "optimization", "convex", "certificate"),
        auto_keywords=("subgradient", "optimization", "convex"),
        parameters=("objective", "subgradient", "point"),
        definitions=("IsSubgradientAt",),
        definition_signatures=("(objective : ℝ → ℝ) (subgradient point : ℝ) : Prop",),
        proven_lemmas=("IsSubgradientAt.globalMinimizer_of_zero",),
        tactic_hint="simpa [IsSubgradientAt] using h",
        planner_metadata={
            "concepts": ["subgradient", "convex_optimization", "optimality_certificate"],
            "textbook_source": "Boyd-Vandenberghe Ch. 3",
            "status": "proven",
            "tactic_hints": [
                "specialize h candidate",
                "simpa [IsSubgradientAt] using h",
            ],
            "related": ["saddle_point", "constrained_optimization"],
        },
    )
)
_register(
    PreambleEntry(
        name="saddle_point",
        lean_module="LeanEcon.Preamble.Optimization.SaddlePoint",
        description="Primal-dual saddle points for Lagrangian optimality arguments.",
        keywords=("saddle point", "lagrangian", "dual", "optimization"),
        auto_keywords=("saddle point", "lagrangian", "dual"),
        parameters=("lagrangian", "primal", "dual"),
        definitions=("IsSaddlePoint",),
        definition_signatures=(
            "(lagrangian : Primal → Dual → ℝ) (primal : Primal) (dual : Dual) : Prop",
        ),
        proven_lemmas=(
            "IsSaddlePoint.dual_optimal",
            "IsSaddlePoint.primal_optimal",
        ),
        tactic_hint="exact h.1 alternativeDual",
        planner_metadata={
            "concepts": ["saddle_point", "lagrangian_duality", "primal_dual_optimality"],
            "textbook_source": "Boyd-Vandenberghe Ch. 5",
            "status": "proven",
            "tactic_hints": [
                "exact h.1 alternativeDual",
                "exact h.2 alternativePrimal",
            ],
            "related": ["subgradient_certificate", "kuhn_tucker"],
        },
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
    return sorted(ranked, key=lambda item: (-item[1], item[0].name))


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
