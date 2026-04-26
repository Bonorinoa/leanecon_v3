from __future__ import annotations

import json
from pathlib import Path

from evals.aggregate_benchmarks import build_markdown_report, load_benchmark_summaries, main


def _write_summary(path: Path, *, claim_set: str, passed: int, total: int, failures: dict[str, int]) -> None:
    path.write_text(
        json.dumps(
            {
                "claim_set": claim_set,
                "generated_at": "2026-04-22T12:00:00+00:00",
                "pass_at_1": passed / total,
                "claims_passed": passed,
                "claims_failed": total - passed,
                "claims_total": total,
                "executed": True,
                "cost_by_stage": {"planner": 0.01, "formalizer": 0.02, "prover": 0.03},
                "failure_counts": failures,
                "results": [
                    {
                        "timing_breakdown": {
                            "planner_ms": 1000.0,
                            "formalizer_ms": 2000.0,
                            "prover_ms": 3000.0,
                            "total_ms": 6000.0,
                        }
                    }
                    for _ in range(total)
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_build_markdown_report_includes_overview_latency_and_failures(tmp_path) -> None:
    _write_summary(
        tmp_path / "tier1_core_preamble_definable.json",
        claim_set="tier1_core_preamble_definable",
        passed=1,
        total=2,
        failures={"max_turns_exhausted": 1},
    )
    _write_summary(
        tmp_path / "tier2_frontier_mathlib_native.json",
        claim_set="tier2_frontier_mathlib_native",
        passed=0,
        total=1,
        failures={"schema_invalid": 1},
    )
    _write_summary(
        tmp_path / "tier2_frontier_preamble_definable.json",
        claim_set="tier2_frontier_preamble_definable",
        passed=0,
        total=1,
        failures={"schema_invalid": 1},
    )

    items = load_benchmark_summaries(output_dir=tmp_path)
    report = build_markdown_report(items, source_dir=tmp_path)

    assert "# Benchmark Summary" in report
    assert "## Overview" in report
    assert "## Average Latency By Stage" in report
    assert "## Failure Breakdown" in report
    assert "| Claim Set | Generated At | Pass@1 | Passed | Failed | Cost USD | File |" in report
    assert (
        "| Failure Code | tier1_core_preamble_definable | tier2_frontier_mathlib_native | "
        "tier2_frontier_preamble_definable | Total |"
    ) in report
    assert "max_turns_exhausted" in report
    assert "schema_invalid" in report
    assert "overall" in report


def _write_progress_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps({"metadata": {"RetrievalEvent": event}}) for event in events) + "\n",
        encoding="utf-8",
    )


def test_compute_retrieval_metrics_includes_sprint23_fields(tmp_path) -> None:
    """Sprint 23 Task 4: aggregator emits second_retrieval_rate + enriched_leansearch_hit_rate."""
    from evals.aggregate_benchmarks import _compute_retrieval_metrics

    progress = tmp_path / "x.progress.jsonl"
    _write_progress_jsonl(
        progress,
        [
            # local RAG event (no enrichment, retrieval_pass=1)
            {"source": "mathlib_rag", "hit": True, "latency_ms": 5.0, "retrieval_pass": 1},
            # leansearch turn-1 (first pass, 2 of 3 enriched)
            {
                "source": "lean_leansearch",
                "hit": True,
                "latency_ms": 80.0,
                "retrieval_pass": 1,
                "enriched_count": 2,
                "retrieved_count": 3,
            },
            # leansearch turn-2 second pass (1 of 2 enriched)
            {
                "source": "lean_leansearch",
                "hit": True,
                "latency_ms": 90.0,
                "retrieval_pass": 2,
                "enriched_count": 1,
                "retrieved_count": 2,
            },
        ],
    )
    metrics = _compute_retrieval_metrics(progress)
    assert metrics["second_retrieval_rate"] == 0.5  # 1 of 2 leansearch events was pass=2
    # 2+1 enriched out of 3+2 retrieved = 3/5 = 0.6
    assert metrics["enriched_leansearch_hit_rate"] == 0.6


def test_aggregate_benchmarks_main_reads_standard_files(tmp_path, capsys) -> None:
    _write_summary(
        tmp_path / "tier1_core_preamble_definable.json",
        claim_set="tier1_core_preamble_definable",
        passed=1,
        total=1,
        failures={},
    )
    _write_summary(
        tmp_path / "tier2_frontier_mathlib_native.json",
        claim_set="tier2_frontier_mathlib_native",
        passed=1,
        total=1,
        failures={},
    )
    _write_summary(
        tmp_path / "tier2_frontier_preamble_definable.json",
        claim_set="tier2_frontier_preamble_definable",
        passed=1,
        total=1,
        failures={},
    )

    exit_code = main(["--output-dir", str(tmp_path)])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Source directory:" in output
    assert "tier1_core_preamble_definable" in output
    assert "tier2_frontier_mathlib_native" in output
    assert "tier2_frontier_preamble_definable" in output
    assert "No failures recorded in the loaded summaries." in output
