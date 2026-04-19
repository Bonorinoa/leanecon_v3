# ruff: noqa: E501
"""Structured Lean compiler error classification."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from src.preamble_library import PREAMBLE_LIBRARY


@dataclass(frozen=True)
class ClassifiedError:
    """One Lean compiler error mapped into an actionable repair bucket."""

    error_class: str
    raw_message: str
    offending_identifier: str | None
    line_number: int | None
    suggested_action: str
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_UNKNOWN_IDENTIFIER_RE = re.compile(
    r"unknown (?:identifier|constant) '([^']+)'",
    re.IGNORECASE,
)
_UNKNOWN_MODULE_RE = re.compile(
    r"unknown (?:module prefix|package) '([^']+)'",
    re.IGNORECASE,
)
_LINE_NUMBER_RE = re.compile(r":(?P<line>\d+):\d+(?::|\b)")
_FAILED_INSTANCE_RE = re.compile(
    r"failed to synthesize(?:\s+instance)?\s*(?:for\s*)?(.+)", re.IGNORECASE
)
_UNKNOWN_TACTIC_RE = re.compile(r"unknown tactic '([^']+)'", re.IGNORECASE)

_SUGGESTED_ACTIONS: dict[str, str] = {
    "unknown_import_module": "Replace with correct Mathlib.X.Y.Z or LeanEcon.Preamble.X.Y path",
    "unknown_identifier": "Search Mathlib for the correct identifier name using lean_local_search",
    "missing_leanecon_import": "Add import LeanEcon.Preamble.<Module> for the used identifier",
    "typeclass_instance": "Add explicit typeclass hypotheses: [NormedSpace ℝ X], [MetricSpace X], etc.",
    "syntax_notation": "Fix Lean 4 syntax: check theorem header, := by, tactic names",
    "type_mismatch": "Types don't match — check function signatures, coercions, universe levels",
    "unsolved_goals": "Proof is incomplete — additional tactics needed to close remaining goals",
    "sorry_present": "Proof contains sorry placeholders — replace with actual proof terms",
    "timeout_deterministic": "Proof search exceeded heartbeat limit — simplify or break into sub-lemmas",
    "unknown": "Unclassified Lean error — include full error in repair context",
}

_LEANECON_IDENTIFIER_TO_MODULE: dict[str, str] = {}
_LEANECON_IDENTIFIER_TO_ENTRY: dict[str, str] = {}
for entry in PREAMBLE_LIBRARY.values():
    for identifier in entry.definitions + entry.planner_proven_lemmas:
        _LEANECON_IDENTIFIER_TO_MODULE.setdefault(identifier, entry.lean_module)
        _LEANECON_IDENTIFIER_TO_ENTRY.setdefault(identifier, entry.name)

_LEANECON_IDENTIFIER_TO_MODULE.update(
    {
        "absolute_risk_aversion": "LeanEcon.Preamble.Risk.ArrowPrattARA",
        "relative_risk_aversion": "LeanEcon.Preamble.Risk.ArrowPrattRRA",
        "cara_ara_simplified": "LeanEcon.Preamble.Consumer.CARAUtility",
        "crra_rra_simplified": "LeanEcon.Preamble.Consumer.CRRAUtility",
        "marshallian_demand_good1": "LeanEcon.Preamble.Consumer.MarshallianDemand",
        "marshallian_demand_good2": "LeanEcon.Preamble.Consumer.MarshallianDemand",
        "indirect_utility_cd": "LeanEcon.Preamble.Consumer.IndirectUtility",
        "price_elasticity": "LeanEcon.Preamble.Consumer.PriceElasticity",
        "income_elasticity": "LeanEcon.Preamble.Consumer.IncomeElasticity",
        "in_budget_set": "LeanEcon.Preamble.Consumer.BudgetSet",
        "expected_payoff_2x2": "LeanEcon.Preamble.GameTheory.ExpectedPayoff",
        "pareto_dominates": "LeanEcon.Preamble.Welfare.ParetoEfficiency",
        "pareto_efficient": "LeanEcon.Preamble.Welfare.ParetoEfficiency",
        "utilitarian_swf": "LeanEcon.Preamble.Welfare.SocialWelfareFunction",
        "ag_walras_law": "LeanEcon.Preamble.GeneralEquilibrium.WalrasLaw",
        "walras_law": "LeanEcon.Preamble.GeneralEquilibrium.WalrasLaw",
        "excess_demand": "LeanEcon.Preamble.GeneralEquilibrium.ExcessDemand",
        "solow_investment": "LeanEcon.Preamble.Macro.SolowSteadyState",
        "solow_depreciation": "LeanEcon.Preamble.Macro.SolowSteadyState",
        "bellman_rhs": "LeanEcon.Preamble.Dynamic.BellmanEquation",
        "bellman_operator": "LeanEcon.Preamble.Dynamic.BellmanOperator",
        "value_function": "LeanEcon.Preamble.Dynamic.ValueFunction",
        "policy_function": "LeanEcon.Preamble.Dynamic.PolicyFunction",
        "transition_law": "LeanEcon.Preamble.Dynamic.TransitionLaw",
        "recursive_competitive_eq": "LeanEcon.Preamble.Dynamic.RecursiveCompetitiveEq",
        "present_value_constant": "LeanEcon.Preamble.Dynamic.DiscountFactor",
        "discount_factor": "LeanEcon.Preamble.Dynamic.DiscountFactor",
        "geometric_partial_sum": "LeanEcon.Preamble.Dynamic.GeometricSeries",
        "continuous_attains_max_on_compact": "LeanEcon.Preamble.Optimization.ExtremeValueTheorem",
        "continuous_attains_min_on_compact": "LeanEcon.Preamble.Optimization.ExtremeValueTheorem",
        "upper_hemicontinuous": "LeanEcon.Preamble.Analysis.UpperHemicontinuous",
        "nkpc": "LeanEcon.Preamble.Macro.PhillipsCurve",
    }
)
_LEANECON_IDENTIFIER_TO_ENTRY.update(
    {
        "absolute_risk_aversion": "arrow_pratt_ara",
        "relative_risk_aversion": "arrow_pratt_rra",
        "cara_ara_simplified": "cara_utility",
        "crra_rra_simplified": "crra_utility",
        "marshallian_demand_good1": "marshallian_demand",
        "marshallian_demand_good2": "marshallian_demand",
        "indirect_utility_cd": "indirect_utility",
        "price_elasticity": "price_elasticity",
        "income_elasticity": "income_elasticity",
        "in_budget_set": "budget_set",
        "expected_payoff_2x2": "expected_payoff",
        "pareto_dominates": "pareto_efficiency",
        "pareto_efficient": "pareto_efficiency",
        "utilitarian_swf": "social_welfare_function",
        "ag_walras_law": "general_equilibrium_walras",
        "walras_law": "walras_law",
        "excess_demand": "excess_demand",
        "solow_investment": "solow_steady_state",
        "solow_depreciation": "solow_steady_state",
        "bellman_rhs": "bellman_equation",
        "bellman_operator": "bellman_operator",
        "value_function": "value_function",
        "policy_function": "policy_function",
        "transition_law": "transition_law",
        "recursive_competitive_eq": "recursive_competitive_eq",
        "present_value_constant": "discount_factor",
        "discount_factor": "discount_factor",
        "geometric_partial_sum": "geometric_series",
        "continuous_attains_max_on_compact": "extreme_value_theorem",
        "continuous_attains_min_on_compact": "extreme_value_theorem",
        "upper_hemicontinuous": "upper_hemicontinuous",
        "nkpc": "phillips_curve",
    }
)


def suggested_import_for_identifier(identifier: str | None) -> str | None:
    """Return the Lean module that likely defines the offending LeanEcon identifier."""

    if not identifier:
        return None
    return _LEANECON_IDENTIFIER_TO_MODULE.get(identifier)


def suggested_preamble_entry_for_identifier(identifier: str | None) -> str | None:
    """Return the preamble entry name that likely defines the LeanEcon identifier."""

    if not identifier:
        return None
    return _LEANECON_IDENTIFIER_TO_ENTRY.get(identifier)


def _extract_identifier(message: str) -> str | None:
    for pattern in (_UNKNOWN_IDENTIFIER_RE, _UNKNOWN_MODULE_RE, _UNKNOWN_TACTIC_RE):
        match = pattern.search(message)
        if match:
            return match.group(1).rsplit(".", 1)[-1].strip() or None

    match = _FAILED_INSTANCE_RE.search(message)
    if match:
        snippet = match.group(1).strip()
        if snippet:
            return snippet.splitlines()[0][:120]
    return None


def _extract_line_number(message: str) -> int | None:
    match = _LINE_NUMBER_RE.search(message)
    if match is None:
        return None
    try:
        return int(match.group("line"))
    except (TypeError, ValueError):
        return None


def _classify_message(message: str, compiler_output: str) -> tuple[str, float]:
    lowered = message.lower()
    combined = f"{compiler_output}\n{message}".lower()

    if any(
        marker in lowered for marker in ("unknown module prefix", "unknown package", "bad import")
    ):
        return "unknown_import_module", 0.99

    if any(marker in lowered for marker in ("unknown identifier", "unknown constant")):
        identifier = _extract_identifier(message)
        if suggested_import_for_identifier(identifier):
            return "missing_leanecon_import", 0.96
        return "unknown_identifier", 0.98

    if "failed to synthesize instance" in lowered or "typeclass" in lowered:
        return "typeclass_instance", 0.96

    if "type mismatch" in lowered or "is expected to have type" in lowered:
        return "type_mismatch", 0.97

    if "unsolved goals" in combined or "left goals" in lowered or "goals remain" in lowered:
        return "unsolved_goals", 0.94

    if "declaration uses 'sorry'" in lowered or "declaration uses sorry" in lowered:
        return "sorry_present", 0.99

    if "(deterministic) timeout" in lowered or "maxheartbeats" in lowered:
        return "timeout_deterministic", 0.98

    if any(
        marker in lowered
        for marker in (
            "expected token",
            "unexpected token",
            "unknown tactic",
            "invalid syntax",
            "unexpected end of input",
            "macro expected",
        )
    ):
        return "syntax_notation", 0.93

    return "unknown", 0.4


def classify_lean_errors(compiler_output: str, errors: list[str]) -> list[ClassifiedError]:
    """Classify raw Lean 4 compiler errors into actionable categories."""

    raw_errors = [error.strip() for error in errors if isinstance(error, str) and error.strip()]
    if not raw_errors and compiler_output.strip():
        raw_errors = [compiler_output.strip()]

    classified: list[ClassifiedError] = []
    for raw_message in raw_errors:
        error_class, confidence = _classify_message(raw_message, compiler_output)
        identifier = _extract_identifier(raw_message)
        suggested_action = _SUGGESTED_ACTIONS[error_class]
        if error_class == "missing_leanecon_import":
            module_name = suggested_import_for_identifier(identifier)
            if module_name:
                suggested_action = f"Add import {module_name} for the used identifier"
        classified.append(
            ClassifiedError(
                error_class=error_class,
                raw_message=raw_message,
                offending_identifier=identifier,
                line_number=_extract_line_number(raw_message),
                suggested_action=suggested_action,
                confidence=confidence,
            )
        )

    return classified
