"""Claim scope and frontier failure classification for LeanEcon alpha."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ClaimScope = Literal["release_reliable", "supported_attempt", "frontier_collect", "out_of_scope"]
FailureClass = Literal[
    "missing_preamble_definition",
    "missing_preamble_theorem",
    "formalizer_template_gap",
    "planner_assumption_gap",
    "proof_search_failure",
    "retrieval_premise_gap",
    "synthesis_tactic_assembly_gap",
    "provider_or_tooling_failure",
    "frontier_collect",
    "out_of_scope",
]
NextAction = Literal[
    "add_preamble_definition",
    "add_preamble_theorem",
    "improve_formalizer_template",
    "improve_planner_assumptions",
    "improve_proof_search",
    "add_retrieval_premise",
    "improve_synthesis_tactic_assembly",
    "fix_provider_or_budget_issue",
    "collect_for_finetuning",
    "mark_out_of_scope",
]

RELEASE_RELIABLE = "release_reliable"
SUPPORTED_ATTEMPT = "supported_attempt"
FRONTIER_COLLECT = "frontier_collect"
OUT_OF_SCOPE = "out_of_scope"
FRONTIER_RECORD_SCHEMA_VERSION = 1

ALPHA_RELEASE_RELIABLE_PREAMBLE_ENTRIES = frozenset(
    {
        "bellman_operator",
        "constrained_optimization",
        "continuous_preference",
        "contraction_mapping",
        "convex_preference",
        "fixed_point_theorem",
        "kuhn_tucker",
        "measure",
        "monotone_sequence",
        "nash_existence",
        "policy_iteration",
        "topological_space",
        "value_function",
    }
)

_OUT_OF_SCOPE_MARKERS = (
    "arrow-debreu",
    "arrow debreu",
    "kakutani",
    "welfare theorem",
    "roy's identity",
    "roys identity",
    "envelope theorem",
    "comparative statics",
)


@dataclass(frozen=True)
class ScopeClassification:
    scope: ClaimScope
    claim_type: Literal["preamble_definable", "mathlib_native"] | None
    selected_preamble_entries: tuple[str, ...] = ()
    required_primitives: tuple[str, ...] = ()
    theorem_shape_recommendation: str = "direct_preamble_theorem"
    assumption_audit: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "claim_type": self.claim_type,
            "selected_preamble_entries": list(self.selected_preamble_entries),
            "required_primitives": list(self.required_primitives),
            "theorem_shape_recommendation": self.theorem_shape_recommendation,
            "assumption_audit": list(self.assumption_audit),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class FailureClassification:
    failure_class: FailureClass | None
    next_action: NextAction | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_class": self.failure_class,
            "next_action": self.next_action,
            "reason": self.reason,
        }


def classify_claim_scope(
    *,
    raw_claim: str,
    claim_type: str | None = None,
    selected_preamble_entries: list[str] | tuple[str, ...] | None = None,
    theorem_stub_present: bool = False,
    missing_definitions: list[str] | tuple[str, ...] | None = None,
    missing_lemmas: list[str] | tuple[str, ...] | None = None,
) -> ScopeClassification:
    """Classify a claim into the alpha release/frontier surfaces."""

    selected = tuple(str(item) for item in (selected_preamble_entries or ()) if str(item).strip())
    missing_defs = tuple(str(item) for item in (missing_definitions or ()) if str(item).strip())
    missing_thms = tuple(str(item) for item in (missing_lemmas or ()) if str(item).strip())
    normalized_claim_type = (
        claim_type if claim_type in {"preamble_definable", "mathlib_native"} else None
    )
    raw_lower = raw_claim.lower()
    audit = _assumption_audit(raw_claim)
    required = _required_primitives(selected, missing_defs)

    if any(marker in raw_lower for marker in _OUT_OF_SCOPE_MARKERS):
        return ScopeClassification(
            scope=OUT_OF_SCOPE,
            claim_type=normalized_claim_type,
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="requires_new_library_surface",
            assumption_audit=audit,
            reason="Claim names a broad theorem family outside the current LeanEcon release surface.",
        )
    if missing_defs:
        return ScopeClassification(
            scope=FRONTIER_COLLECT,
            claim_type=normalized_claim_type,
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="add_preamble_definition_then_template",
            assumption_audit=audit,
            reason=f"Missing preamble definitions: {', '.join(missing_defs)}.",
        )
    if missing_thms:
        return ScopeClassification(
            scope=FRONTIER_COLLECT,
            claim_type=normalized_claim_type,
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="add_preamble_theorem_then_template",
            assumption_audit=audit,
            reason=f"Missing preamble lemmas: {', '.join(missing_thms)}.",
        )
    unknown_release_entries = tuple(
        entry for entry in selected if entry not in ALPHA_RELEASE_RELIABLE_PREAMBLE_ENTRIES
    )
    if (
        normalized_claim_type == "preamble_definable"
        and selected
        and theorem_stub_present
        and not unknown_release_entries
    ):
        return ScopeClassification(
            scope=RELEASE_RELIABLE,
            claim_type="preamble_definable",
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="theorem_stub_direct_closure",
            assumption_audit=audit,
            reason="Preamble-backed claim uses the frozen alpha release surface with an authoritative theorem stub.",
        )
    if normalized_claim_type == "preamble_definable" and unknown_release_entries:
        return ScopeClassification(
            scope=SUPPORTED_ATTEMPT,
            claim_type="preamble_definable",
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="planner_formalizer_supported_attempt",
            assumption_audit=audit,
            reason=(
                "Preamble-backed claim uses entries outside the frozen alpha "
                f"release-reliable surface: {', '.join(unknown_release_entries)}."
            ),
        )
    if normalized_claim_type == "preamble_definable" and selected:
        return ScopeClassification(
            scope=SUPPORTED_ATTEMPT,
            claim_type="preamble_definable",
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="theorem_stub_required_for_release",
            assumption_audit=audit,
            reason="Preamble-backed claim lacks an authoritative theorem stub for release-reliable routing.",
        )
    if normalized_claim_type == "mathlib_native":
        return ScopeClassification(
            scope=FRONTIER_COLLECT,
            claim_type="mathlib_native",
            selected_preamble_entries=selected,
            required_primitives=required,
            theorem_shape_recommendation="mathlib_native_search_trace",
            assumption_audit=audit,
            reason="Mathlib-native claim is research/frontier data, not release-reliable surface.",
        )
    return ScopeClassification(
        scope=SUPPORTED_ATTEMPT,
        claim_type=normalized_claim_type,
        selected_preamble_entries=selected,
        required_primitives=required,
        theorem_shape_recommendation="planner_formalizer_supported_attempt",
        assumption_audit=audit,
        reason="Claim is attemptable but lacks enough preamble-backed evidence for release-reliable routing.",
    )


def classify_failure(
    *,
    scope: str | None,
    claim_type: str | None,
    status: str | None,
    failure_code: str | None = None,
    termination_reason: str | None = None,
    selected_preamble_entries: list[str] | tuple[str, ...] | None = None,
    parse_success: bool | None = None,
    synthesis_event_count: int = 0,
    candidate_attempt_count: int = 0,
    retrieval_event_count: int = 0,
) -> FailureClassification:
    """Map failed/out-of-scope attempts to a roadmap next action."""

    if status == "verified":
        return FailureClassification(None, None, "Verified claims do not enter a failure queue.")
    selected = tuple(str(item) for item in (selected_preamble_entries or ()) if str(item).strip())
    code = (failure_code or termination_reason or "").lower()
    has_synthesis_evidence = synthesis_event_count > 0 or candidate_attempt_count > 0
    if scope == OUT_OF_SCOPE:
        return FailureClassification("out_of_scope", "mark_out_of_scope", "Scope classifier marked the claim out of scope.")
    if "unknown_identifier" in code or "unknown identifier" in code or "missing_definition" in code:
        return FailureClassification(
            "missing_preamble_definition",
            "add_preamble_definition",
            "Failure indicates an unavailable Lean identifier or primitive.",
        )
    if "no_direct_candidate" in code or "missing_theorem" in code:
        return FailureClassification(
            "missing_preamble_theorem",
            "add_preamble_theorem",
            "Preamble entries were selected but no proven theorem matched the claim shape.",
        )
    if parse_success is False or "parse" in code or "schema_invalid" in code or "formalizer" in code:
        return FailureClassification(
            "formalizer_template_gap",
            "improve_formalizer_template",
            "The claim failed before a usable Lean theorem/proof-search target was available.",
        )
    if "assumption" in code or "vacuous" in code:
        return FailureClassification(
            "planner_assumption_gap",
            "improve_planner_assumptions",
            "The failure points to missing or weak assumptions in the planner contract.",
        )
    if (not has_synthesis_evidence or "max_turns" not in code) and any(
        marker in code
        for marker in (
            "budget",
            "exhaust",
            "timeout",
            "provider",
            "tooling",
            "lsp_unavailable",
            "auth",
        )
    ):
        return FailureClassification(
            "provider_or_tooling_failure",
            "fix_provider_or_budget_issue",
            "The failure points to infrastructure, provider, or budget limits rather than a theorem gap.",
        )
    if claim_type == "mathlib_native":
        if "retrieval" in code or (
            retrieval_event_count == 0 and synthesis_event_count == 0 and candidate_attempt_count == 0
        ):
            return FailureClassification(
                "retrieval_premise_gap",
                "add_retrieval_premise",
                "Mathlib-native proving did not surface usable premise evidence.",
            )
        if (
            has_synthesis_evidence
            or any(marker in code for marker in ("compile", "stall", "max_turns", "unsolved"))
        ):
            return FailureClassification(
                "synthesis_tactic_assembly_gap",
                "improve_synthesis_tactic_assembly",
                "Retrieved or synthesized proof material was present, but tactic assembly did not verify.",
            )
        return FailureClassification(
            "frontier_collect",
            "collect_for_finetuning",
            "Mathlib-native failure is collected as frontier data.",
        )
    if selected:
        return FailureClassification(
            "proof_search_failure",
            "improve_proof_search",
            "Preamble-backed statement reached proof search but did not verify.",
        )
    return FailureClassification(
        "frontier_collect",
        "collect_for_finetuning",
        "Failure lacks a narrower local remediation and should be retained as labeled data.",
    )


def build_frontier_record(
    *,
    raw_claim: str,
    claim_id: str | None,
    scope: ScopeClassification | dict[str, Any],
    status: str,
    claim_type: str | None = None,
    lean_statement: str | None = None,
    parse_success: bool | None = None,
    proof_result: str | None = None,
    failure: FailureClassification | dict[str, Any] | None = None,
    budget_profile: str | None = None,
    failure_code: str | None = None,
    termination_reason: str | None = None,
    timing_breakdown: dict[str, Any] | None = None,
    usage_by_stage: dict[str, Any] | None = None,
    tool_budget: dict[str, Any] | None = None,
    budget_exhaustion: dict[str, Any] | str | None = None,
    synthesis_event_count: int | None = None,
    candidate_attempt_count: int | None = None,
    retrieval_event_count: int | None = None,
) -> dict[str, Any]:
    scope_payload = scope.to_dict() if isinstance(scope, ScopeClassification) else dict(scope)
    failure_payload = (
        failure.to_dict() if isinstance(failure, FailureClassification) else dict(failure or {})
    )
    return {
        "schema_version": FRONTIER_RECORD_SCHEMA_VERSION,
        "claim_id": claim_id,
        "raw_claim": raw_claim,
        "scope_classification": scope_payload.get("scope"),
        "scope_reason": scope_payload.get("reason"),
        "claim_type": claim_type or scope_payload.get("claim_type"),
        "selected_preamble_entries": list(scope_payload.get("selected_preamble_entries") or []),
        "required_primitives": list(scope_payload.get("required_primitives") or []),
        "theorem_shape_recommendation": scope_payload.get("theorem_shape_recommendation"),
        "assumption_audit": list(scope_payload.get("assumption_audit") or []),
        "lean_statement": lean_statement,
        "parse_result": {"success": parse_success} if parse_success is not None else None,
        "proof_result": proof_result or status,
        "budget_profile": budget_profile,
        "failure_code": failure_code,
        "termination_reason": termination_reason,
        "timing_breakdown": dict(timing_breakdown or {}),
        "usage_by_stage": dict(usage_by_stage or {}),
        "tool_budget": dict(tool_budget or {}),
        "budget_exhaustion": budget_exhaustion,
        "synthesis_event_count": synthesis_event_count,
        "candidate_attempt_count": candidate_attempt_count,
        "retrieval_event_count": retrieval_event_count,
        "failure_class": failure_payload.get("failure_class"),
        "recommended_next_action": failure_payload.get("next_action"),
        "failure_reason": failure_payload.get("reason"),
    }


def scope_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        RELEASE_RELIABLE: 0,
        SUPPORTED_ATTEMPT: 0,
        FRONTIER_COLLECT: 0,
        OUT_OF_SCOPE: 0,
    }
    for result in results:
        scope = str(result.get("claim_scope") or "")
        if scope in counts:
            counts[scope] += 1
    return counts


def metrics_by_scope(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for scope, total in scope_counts(results).items():
        scoped = [result for result in results if result.get("claim_scope") == scope]
        passed = sum(1 for result in scoped if result.get("status") == "verified")
        metrics[scope] = {
            "claims_total": total,
            "claims_passed": passed,
            "claims_failed": total - passed,
            "pass_at_1": round(passed / total, 6) if total else 0.0,
        }
    return metrics


def _assumption_audit(raw_claim: str) -> tuple[str, ...]:
    raw_lower = raw_claim.lower()
    audit: list[str] = []
    for marker, note in (
        ("bounded", "boundedness_explicit"),
        ("compact", "compactness_explicit"),
        ("continuous", "continuity_explicit"),
        ("contraction", "contraction_hypothesis_explicit"),
        ("monotone", "monotonicity_explicit"),
        ("feasible", "feasibility_explicit"),
    ):
        if marker in raw_lower:
            audit.append(note)
    return tuple(audit or ["assumptions_not_fully_explicit"])


def _required_primitives(
    selected_preamble_entries: tuple[str, ...],
    missing_definitions: tuple[str, ...],
) -> tuple[str, ...]:
    primitives: list[str] = []
    primitives.extend(selected_preamble_entries)
    primitives.extend(missing_definitions)
    return tuple(dict.fromkeys(primitives))
