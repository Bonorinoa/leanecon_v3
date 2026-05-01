from __future__ import annotations

from src.evals.metrics_aggregator import append_history_row, build_history_row, load_history_rows


def test_build_history_row_includes_required_rollups(tmp_path) -> None:
    run_summary = {
        "claim_set": "local_gate",
        "mode": "benchmark_pipeline",
        "benchmark_mode": True,
        "generated_at": "2026-04-23T23:10:00+00:00",
        "claims_total": 3,
        "claims_passed": 2,
        "claims_failed": 1,
        "pass_at_1": 2 / 3,
        "cost_by_stage": {"planner": 0.15, "formalizer": 0.0, "prover": 0.0},
        "synthesis_events": [
            {
                "event_type": "SynthesisEvent",
                "tactic": "exact tendsto_atTop_ciSup hmono hbdd",
                "referenced_premises": ["tendsto_atTop_ciSup"],
                "top3_match": True,
                "success": True,
                "decomposition_depth": 2,
            },
            {
                "event_type": "SynthesisEvent",
                "tactic": "exact hallucinated_name h",
                "referenced_premises": [],
                "top3_match": False,
                "success": False,
                "decomposition_depth": 2,
            },
        ],
        "claim_sets": [
            {
                "claim_set": "tier1_core_preamble_definable",
                "claims_total": 2,
                "claims_passed": 2,
                "claims_failed": 0,
                "pass_at_1": 1.0,
                "cost_by_stage": {"planner": 0.1},
                "claim_set_manifest": {
                    "bucket_counts": {
                        "mathlib_native": 0,
                        "planner_formalizer": 0,
                        "preamble_definable": 2,
                        "prover_search": 0,
                        "regression": 0,
                    }
                },
                "results": [
                    {
                        "id": "tier1_a",
                        "benchmark_bucket": "preamble_definable",
                        "status": "verified",
                        "verified_via": "full_pipeline",
                        "termination_reason": "verified",
                        "tool_calls": 1,
                        "lsp_tool_calls": 0,
                        "native_search_attempts": 0,
                        "mathlib_native_mode_usage": 0,
                        "decomposition_depth": 0,
                        "timing_breakdown": {"total_ms": 3000.0},
                        "progress_events": [{"message": "Closed via direct definable closure.", "metadata": {}}],
                        "usage_by_stage": {"planner": {"error_code": None}},
                    },
                    {
                        "id": "tier1_b",
                        "benchmark_bucket": "preamble_definable",
                        "status": "verified",
                        "verified_via": "full_pipeline",
                        "termination_reason": "verified",
                        "tool_calls": 2,
                        "lsp_tool_calls": 1,
                        "native_search_attempts": 0,
                        "mathlib_native_mode_usage": 0,
                        "decomposition_depth": 1,
                        "timing_breakdown": {"total_ms": 5000.0},
                        "progress_events": [],
                        "raw_planner_response": "{\"broken\":true}",
                        "usage_by_stage": {"planner": {"error_code": "schema_invalid"}},
                    },
                ],
            },
            {
                "claim_set": "tier2_frontier_mathlib_native",
                "claims_total": 1,
                "claims_passed": 0,
                "claims_failed": 1,
                "pass_at_1": 0.0,
                "cost_by_stage": {"planner": 0.05},
                "synthesis_events": [
                    {
                        "event_type": "SynthesisEvent",
                        "tactic": "exact tendsto_atTop_ciSup hmono hbdd",
                        "referenced_premises": ["tendsto_atTop_ciSup"],
                        "top3_match": True,
                        "success": True,
                        "decomposition_depth": 2,
                    },
                    {
                        "event_type": "SynthesisEvent",
                        "tactic": "exact hallucinated_name h",
                        "referenced_premises": [],
                        "top3_match": False,
                        "success": False,
                        "decomposition_depth": 2,
                    },
                ],
                "claim_set_manifest": {
                    "bucket_counts": {
                        "mathlib_native": 1,
                        "planner_formalizer": 0,
                        "preamble_definable": 0,
                        "prover_search": 0,
                        "regression": 0,
                    }
                },
                "results": [
                    {
                        "id": "tier2_a",
                        "benchmark_bucket": "mathlib_native",
                        "status": "failed",
                        "verified_via": "full_pipeline",
                        "termination_reason": "no_progress_stall",
                        "tool_calls": 3,
                        "lsp_tool_calls": 5,
                        "native_search_attempts": 2,
                        "mathlib_native_mode_usage": 1,
                        "decomposition_depth": 2,
                        "timing_breakdown": {"total_ms": 7000.0},
                        "progress_events": [],
                        "usage_by_stage": {"planner": {"error_code": None}},
                    }
                ],
            },
        ],
    }

    row = build_history_row(run_summary, row_id="run_000123")
    history_path = tmp_path / "benchmark_history.jsonl"
    appended = append_history_row(run_summary, history_path=history_path)

    assert row["row_id"] == "run_000123"
    assert row["overall_pass_rate"] == 0.666667
    assert row["total_cost_usd"] == 0.15
    assert row["avg_latency_total"] == 5000.0
    assert row["direct_close_rate"] == 0.333333
    assert row["avg_tool_calls"] == 2.0
    assert row["avg_lsp_tool_calls"] == 2.0
    assert row["total_lsp_tool_calls"] == 6
    assert row["avg_native_search_attempts"] == 0.667
    assert row["total_native_search_attempts"] == 2
    assert row["mathlib_native_mode_usage"] == 1
    assert row["avg_decomposition_depth"] == 1.0
    assert row["synthesis_efficiency"] == 0.5
    assert row["premise_match_rate@3"] == 0.5
    assert row["avg_decomposition_depth_mathlib"] == 2.0
    assert row["no_progress_stall_count"] == 1
    assert row["schema_invalid_rate"] == 0.333333
    assert row["claim_type_mix"] == {"preamble_definable": 2, "mathlib_native": 1}
    assert row["bucket_breakdown"]["tier1_core_preamble_definable"]["direct_close_rate"] == 0.5
    assert row["bucket_breakdown"]["tier2_frontier_mathlib_native"]["total_lsp_tool_calls"] == 5
    assert row["bucket_breakdown"]["tier2_frontier_mathlib_native"]["mathlib_native_mode_usage"] == 1
    assert row["bucket_breakdown"]["tier2_frontier_mathlib_native"]["synthesis_efficiency"] == 0.5
    assert row["bucket_breakdown"]["tier2_frontier_preamble_definable"]["present"] is False
    assert row["bucket_breakdown"]["tier2_frontier_mathlib_native"]["no_progress_stall_count"] == 1
    assert appended["row_id"] == "run_000001"
    assert load_history_rows(history_path) == [appended]
