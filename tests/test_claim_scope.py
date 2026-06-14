from __future__ import annotations

from src.claim_scope import (
    ALPHA_RELEASE_RELIABLE_PREAMBLE_ENTRIES,
    build_frontier_record,
    classify_claim_scope,
    classify_failure,
    metrics_by_scope,
)


def test_scope_classifier_separates_release_reliable_from_mathlib_frontier() -> None:
    reliable = classify_claim_scope(
        raw_claim="A monotone real sequence bounded above converges.",
        claim_type="preamble_definable",
        selected_preamble_entries=["monotone_sequence"],
        theorem_stub_present=True,
    )
    frontier = classify_claim_scope(
        raw_claim="A compact continuous function attains a maximum by the extreme value theorem.",
        claim_type="mathlib_native",
        selected_preamble_entries=[],
    )

    assert reliable.scope == "release_reliable"
    assert reliable.claim_type == "preamble_definable"
    assert reliable.required_primitives == ("monotone_sequence",)
    assert frontier.scope == "frontier_collect"
    assert frontier.claim_type == "mathlib_native"


def test_scope_classifier_requires_frozen_release_surface_and_stub() -> None:
    supported_without_stub = classify_claim_scope(
        raw_claim="A constrained maximizer is feasible.",
        claim_type="preamble_definable",
        selected_preamble_entries=["constrained_optimization"],
        theorem_stub_present=False,
    )
    supported_unknown_entry = classify_claim_scope(
        raw_claim="A new economics primitive satisfies a useful property.",
        claim_type="preamble_definable",
        selected_preamble_entries=["new_frontier_primitive"],
        theorem_stub_present=True,
    )

    assert supported_without_stub.scope == "supported_attempt"
    assert supported_without_stub.theorem_shape_recommendation == "theorem_stub_required_for_release"
    assert "authoritative theorem stub" in supported_without_stub.reason
    assert supported_unknown_entry.scope == "supported_attempt"
    assert "outside the frozen alpha" in supported_unknown_entry.reason


def test_scope_classifier_marks_broad_missing_surface_out_of_scope() -> None:
    classified = classify_claim_scope(
        raw_claim="Prove the full Arrow-Debreu existence theorem.",
        claim_type="mathlib_native",
    )

    assert classified.scope == "out_of_scope"
    assert "outside" in classified.reason


def test_failure_classifier_recommends_next_actions() -> None:
    missing_definition = classify_failure(
        scope="frontier_collect",
        claim_type="preamble_definable",
        status="failed",
        failure_code="unknown_identifier",
    )
    proof_search = classify_failure(
        scope="release_reliable",
        claim_type="preamble_definable",
        status="failed",
        failure_code="no_progress_stall",
        selected_preamble_entries=["constrained_optimization"],
    )

    assert missing_definition.failure_class == "missing_preamble_definition"
    assert missing_definition.next_action == "add_preamble_definition"
    assert proof_search.failure_class == "proof_search_failure"
    assert proof_search.next_action == "improve_proof_search"


def test_failure_classifier_separates_mathlib_synthesis_gap() -> None:
    synthesis_gap = classify_failure(
        scope="frontier_collect",
        claim_type="mathlib_native",
        status="failed",
        failure_code="max_turns_exhausted",
        synthesis_event_count=3,
        candidate_attempt_count=2,
        retrieval_event_count=4,
    )

    assert synthesis_gap.failure_class == "synthesis_tactic_assembly_gap"
    assert synthesis_gap.next_action == "improve_synthesis_tactic_assembly"


def test_frontier_record_contains_contract_fields() -> None:
    scope = classify_claim_scope(
        raw_claim="A monotone real sequence bounded above converges.",
        claim_type="mathlib_native",
    )
    failure = classify_failure(
        scope=scope.scope,
        claim_type=scope.claim_type,
        status="failed",
        failure_code="retrieval_empty",
    )
    record = build_frontier_record(
        raw_claim="A monotone real sequence bounded above converges.",
        claim_id="demo",
        scope=scope,
        claim_type=scope.claim_type,
        lean_statement="theorem demo : True := by sorry",
        parse_success=True,
        proof_result="failed",
        status="failed",
        failure=failure,
        budget_profile="frontier",
        failure_code="retrieval_empty",
        termination_reason="prover_failed",
        timing_breakdown={"total_ms": 12.5},
        usage_by_stage={"prover": {"input_tokens": 42}},
        tool_budget={"total_tool_calls": 3},
        budget_exhaustion=None,
        synthesis_event_count=1,
        candidate_attempt_count=2,
        retrieval_event_count=3,
    )

    assert record["raw_claim"].startswith("A monotone")
    assert record["schema_version"] == 1
    assert record["scope_classification"] == "frontier_collect"
    assert record["claim_type"] == "mathlib_native"
    assert record["parse_result"] == {"success": True}
    assert record["failure_class"] == "retrieval_premise_gap"
    assert record["recommended_next_action"] == "add_retrieval_premise"
    assert record["budget_profile"] == "frontier"
    assert record["failure_code"] == "retrieval_empty"
    assert record["termination_reason"] == "prover_failed"
    assert record["timing_breakdown"]["total_ms"] == 12.5
    assert record["usage_by_stage"]["prover"]["input_tokens"] == 42
    assert record["tool_budget"]["total_tool_calls"] == 3
    assert record["synthesis_event_count"] == 1
    assert record["candidate_attempt_count"] == 2
    assert record["retrieval_event_count"] == 3


def test_failure_classifier_marks_provider_and_budget_issues() -> None:
    blocked = classify_failure(
        scope="frontier_collect",
        claim_type="mathlib_native",
        status="failed",
        failure_code="lsp_unavailable",
    )

    assert blocked.failure_class == "provider_or_tooling_failure"
    assert blocked.next_action == "fix_provider_or_budget_issue"


def test_metrics_by_scope_separates_reliable_and_frontier() -> None:
    metrics = metrics_by_scope(
        [
            {"claim_scope": "release_reliable", "status": "verified"},
            {"claim_scope": "release_reliable", "status": "failed"},
            {"claim_scope": "frontier_collect", "status": "failed"},
        ]
    )

    assert metrics["release_reliable"]["claims_total"] == 2
    assert metrics["release_reliable"]["pass_at_1"] == 0.5
    assert metrics["frontier_collect"]["claims_total"] == 1


def test_alpha_release_reliable_entry_set_is_frozen() -> None:
    assert ALPHA_RELEASE_RELIABLE_PREAMBLE_ENTRIES == frozenset(
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
