from __future__ import annotations

import json

from evals.gap_report import main
from src.evals.gap_detector import append_gaps_jsonl, detect_gaps_from_claim_result, detect_gaps_from_summaries


def _failed_result(
    *,
    claim_id: str,
    raw_claim: str,
    benchmark_bucket: str,
    target_name: str,
    attempts: list[tuple[str, str]],
    tool_patterns: list[tuple[str, bool, str | None]] | None = None,
) -> dict[str, object]:
    progress_events: list[dict[str, object]] = [
        {
            "event": "prover_turn",
            "message": f"Starting target `{target_name}`.",
            "metadata": {"target_name": target_name, "target_kind": "subgoal", "turn": 1},
        }
    ]
    for index, (proof, source) in enumerate(attempts, start=1):
        progress_events.append(
            {
                "event": "prover_tool",
                "message": f"Direct closure attempt {index}/{len(attempts)}.",
                "metadata": {
                    "attempt_index": index,
                    "attempt_limit": len(attempts),
                    "candidate_count": len(attempts),
                    "compile_timeout_seconds": 25,
                    "proof": proof,
                    "source": source,
                    "target_name": target_name,
                    "tool_name": "compile_check",
                },
            }
        )
    for turn, (tool_name, success, error_code) in enumerate(tool_patterns or [], start=1):
        progress_events.append(
            {
                "event": "prover_tool",
                "message": f"Tool `{tool_name}` executed.",
                "metadata": {
                    "tool_name": tool_name,
                    "success": success,
                    "error_code": error_code,
                    "target_name": target_name,
                    "turn": turn,
                },
            }
        )
    progress_events.append(
        {
            "claim_id": claim_id,
            "event": "prover_failed",
            "message": "Prover finished with status `failed`.",
            "metadata": {"termination_reason": "no_progress_stall"},
            "stage": "prover",
            "status": "failed",
        }
    )
    return {
        "id": claim_id,
        "raw_claim": raw_claim,
        "benchmark_bucket": benchmark_bucket,
        "status": "failed",
        "failure_code": "unsolved_goals",
        "termination_reason": "no_progress_stall",
        "progress_events": progress_events,
    }


def test_detect_gaps_from_claim_result_returns_structured_gap() -> None:
    result = _failed_result(
        claim_id="t2_stone_geary_monotone_alpha",
        raw_claim="A continuous utility representation remains continuous when attention is restricted to a feasible subset.",
        benchmark_bucket="preamble_definable",
        target_name="h_continuous_preference",
        attempts=[
            ("exact hu.continuousOn", "continuous_preference"),
            ("exact continuousPreference_continuousOn", "continuous_preference"),
        ],
        tool_patterns=[("apply_tactic", True, None)],
    )

    gaps = detect_gaps_from_claim_result(result, claim_set="tier2_frontier_preamble_definable")

    assert len(gaps) == 1
    gap = gaps[0]
    assert set(gap) >= {"gap_id", "frequency", "affected_claims", "suggested_lean_stub", "priority_score", "rationale"}
    assert gap["gap_id"] == "continuous_preference.restriction_continuity"
    assert gap["frequency"] == 1
    assert gap["affected_claims"] == ["t2_stone_geary_monotone_alpha"]
    assert "continuousPreference_continuousOn_subset" in gap["suggested_lean_stub"]
    assert "unsolved_goals" in gap["rationale"]


def test_detect_gaps_from_summaries_aggregates_multiple_failed_patterns(tmp_path) -> None:
    summaries = [
        {
            "claim_set": "tier2_frontier_preamble_definable",
            "results": [
                _failed_result(
                    claim_id="t2_stone_geary_monotone_alpha",
                    raw_claim="A continuous utility representation remains continuous when attention is restricted to a feasible subset.",
                    benchmark_bucket="preamble_definable",
                    target_name="h_continuous_preference",
                    attempts=[
                        ("exact hu.continuousOn", "continuous_preference"),
                        ("exact continuousPreference_continuousOn", "continuous_preference"),
                    ],
                ),
                _failed_result(
                    claim_id="t2_bellman_contraction",
                    raw_claim="A Bellman-style operator satisfying contraction conditions fits the contraction-mapping template used in recursive economics.",
                    benchmark_bucket="preamble_definable",
                    target_name="h_bellman_contraction",
                    attempts=[
                        ("exact BellmanOperator.monotone", "bellman_operator"),
                        ("exact contraction_has_fixedPoint", "contraction_mapping"),
                    ],
                    tool_patterns=[("apply_tactic", False, "compile_failed")],
                ),
                _failed_result(
                    claim_id="t2_extreme_value_repair",
                    raw_claim="A strictly concave function attains a maximum on a compact set.",
                    benchmark_bucket="mathlib_native",
                    target_name="h_existence",
                    attempts=[
                        ("exact IsConstrainedMaximum.feasible", "constrained_optimization"),
                        ("exact IsConstrainedMaximum.value_le", "constrained_optimization"),
                    ],
                    tool_patterns=[("write_current_code", False, None)],
                ),
            ],
        }
    ]

    gaps = detect_gaps_from_summaries(summaries)
    output_path = append_gaps_jsonl(gaps, output_path=tmp_path / "preamble_gaps.jsonl")

    assert len(gaps) == 3
    assert gaps[0]["gap_id"] == "continuous_preference.restriction_continuity"
    assert output_path.exists()
    lines = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 3


def test_gap_report_main_reads_last_run_and_appends_jsonl(tmp_path, capsys) -> None:
    base_dir = tmp_path
    benchmark_mode_dir = base_dir / "benchmark_mode"
    benchmark_mode_dir.mkdir(parents=True)
    history_path = base_dir / "benchmark_history.jsonl"
    history_path.write_text(
        json.dumps(
            {
                "row_id": "run_000001",
                "selected_claim_sets": [
                    "tier2_frontier_preamble_definable",
                    "tier2_frontier_mathlib_native",
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    (benchmark_mode_dir / "tier2_frontier_preamble_definable.json").write_text(
        json.dumps(
            {
                "claim_set": "tier2_frontier_preamble_definable",
                "results": [
                    _failed_result(
                        claim_id="t2_stone_geary_monotone_alpha",
                        raw_claim="A continuous utility representation remains continuous when attention is restricted to a feasible subset.",
                        benchmark_bucket="preamble_definable",
                        target_name="h_continuous_preference",
                        attempts=[
                            ("exact hu.continuousOn", "continuous_preference"),
                            ("exact continuousPreference_continuousOn", "continuous_preference"),
                        ],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    (benchmark_mode_dir / "tier2_frontier_mathlib_native.json").write_text(
        json.dumps(
            {
                "claim_set": "tier2_frontier_mathlib_native",
                "results": [
                    _failed_result(
                        claim_id="t2_monotone_sequence_converges",
                        raw_claim="A monotone sequence bounded above converges.",
                        benchmark_bucket="mathlib_native",
                        target_name="h_monotone_sequence",
                        attempts=[("exact BellmanOperator.monotone", "bellman_operator")],
                        tool_patterns=[("lean_hover_info", True, None)],
                    )
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["--last-run", "--base-dir", str(base_dir)])
    output = capsys.readouterr().out
    gaps_path = base_dir / "preamble_gaps.jsonl"

    assert exit_code == 0
    assert "Detected 2 gaps" in output
    assert "continuous_preference.restriction_continuity" in output
    assert "analysis.monotone_bounded_sequence_converges" in output
    assert gaps_path.exists()
    lines = [json.loads(line) for line in gaps_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
