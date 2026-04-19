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
        "economic_measure": "LeanEcon.Preamble.Foundations.Primitives.Measure",
        "commodity_topology":
            "LeanEcon.Preamble.Foundations.Primitives.TopologicalSpace",
        "continuous_preference":
            "LeanEcon.Preamble.Foundations.Preferences.ContinuousPreference",
        "convex_preference":
            "LeanEcon.Preamble.Foundations.Preferences.ConvexPreference",
        "constrained_optimization":
            "LeanEcon.Preamble.Foundations.Optimization.ConstrainedOptimization",
        "kuhn_tucker":
            "LeanEcon.Preamble.Foundations.Optimization.KuhnTucker",
        "fixed_point_theorem":
            "LeanEcon.Preamble.Foundations.Equilibrium.FixedPointTheorem",
        "nash_existence":
            "LeanEcon.Preamble.Foundations.Equilibrium.NashExistence",
        "bellman_operator":
            "LeanEcon.Preamble.Foundations.DynamicProgramming.BellmanOperator",
        "contraction_mapping":
            "LeanEcon.Preamble.Foundations.DynamicProgramming.ContractionMapping",
        "value_function":
            "LeanEcon.Preamble.Foundations.DynamicProgramming.ValueFunction",
        "policy_iteration":
            "LeanEcon.Preamble.Foundations.DynamicProgramming.PolicyIteration",
    }
)
_LEANECON_IDENTIFIER_TO_ENTRY.update(
    {
        "economic_measure": "measure",
        "commodity_topology": "topological_space",
        "continuous_preference": "continuous_preference",
        "convex_preference": "convex_preference",
        "constrained_optimization": "constrained_optimization",
        "kuhn_tucker": "kuhn_tucker",
        "fixed_point_theorem": "fixed_point_theorem",
        "nash_existence": "nash_existence",
        "bellman_operator": "bellman_operator",
        "value_function": "value_function",
        "contraction_mapping": "contraction_mapping",
        "policy_iteration": "policy_iteration",
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
