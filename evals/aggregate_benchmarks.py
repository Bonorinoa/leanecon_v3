"""Aggregate benchmark JSON summaries into a markdown report."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from evals.common import (
    FRONTIER_BENCHMARK_CLAIM_SETS,
    RELEASE_RELIABLE_CLAIM_SETS,
    STANDARD_BENCHMARK_CLAIM_SETS,
    load_summary,
)
from src.claim_scope import metrics_by_scope


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_usd(value: float) -> str:
    return f"${value:.4f}"


def _format_duration_ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{value:.0f}ms"


def _render_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "| " + " | ".join("---" for _ in headers) + " |"
    body_rows = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header_row, separator_row, *body_rows])


def _average_stage_latency_ms(summary: dict[str, Any], stage: str) -> float:
    results = summary.get("results", [])
    if not summary.get("executed", True) or not results:
        return 0.0
    total = sum(float((result.get("timing_breakdown") or {}).get(stage) or 0.0) for result in results)
    return round(total / len(results), 3)


def _total_cost(summary: dict[str, Any]) -> float:
    return round(sum(float(value or 0.0) for value in summary.get("cost_by_stage", {}).values()), 8)


def _compute_retrieval_metrics(progress_jsonl: Path) -> dict[str, float]:
    """Parse a progress JSONL and return hybrid retrieval metrics."""
    local_hits = local_total = ls_hits = ls_total = 0
    ls_latencies: list[float] = []
    # Sprint 23 Task 4: second-retrieval + enrichment metrics.
    second_pass_calls = 0
    enriched_total = 0
    retrieved_total = 0
    if not progress_jsonl.exists():
        return {}
    with open(progress_jsonl) as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            re_payload = (event.get("metadata") or {}).get("RetrievalEvent") or {}
            if not re_payload:
                continue
            source = re_payload.get("source", "mathlib_rag")
            hit = bool(re_payload.get("hit"))
            latency = float(re_payload.get("latency_ms") or 0.0)
            if source == "lean_leansearch":
                ls_total += 1
                ls_latencies.append(latency)
                if hit:
                    ls_hits += 1
                if int(re_payload.get("retrieval_pass", 1)) >= 2:
                    second_pass_calls += 1
                enriched_total += int(re_payload.get("enriched_count", 0))
                retrieved_total += int(re_payload.get("retrieved_count", 0))
            else:
                local_total += 1
                if hit:
                    local_hits += 1
    return {
        "local_rag_hit_rate": round(local_hits / local_total, 4) if local_total else 0.0,
        "leansearch_hit_rate_at_5": round(ls_hits / ls_total, 4) if ls_total else 0.0,
        "hybrid_retrieval_latency_ms": round(sum(ls_latencies) / len(ls_latencies), 1) if ls_latencies else 0.0,
        "leansearch_call_count": float(ls_total),
        "second_retrieval_rate": round(second_pass_calls / ls_total, 4) if ls_total else 0.0,
        "enriched_leansearch_hit_rate": round(enriched_total / retrieved_total, 4) if retrieved_total else 0.0,
    }


def _empty_scope_metrics() -> dict[str, dict[str, Any]]:
    return {
        scope: {"claims_total": 0, "claims_passed": 0, "claims_failed": 0, "pass_at_1": 0.0}
        for scope in ("release_reliable", "supported_attempt", "frontier_collect", "out_of_scope")
    }


def _summary_scope_metrics(claim_set: str, summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    scoped = summary.get("metrics_by_scope")
    if isinstance(scoped, dict) and any(
        int((payload or {}).get("claims_total") or 0) > 0 for payload in scoped.values()
    ):
        return scoped
    results = list(summary.get("results", []))
    if any(result.get("claim_scope") for result in results if isinstance(result, dict)):
        return metrics_by_scope(results)

    fallback = _empty_scope_metrics()
    total = int(summary.get("claims_total") or 0)
    passed = int(summary.get("claims_passed") or 0)
    failed = int(summary.get("claims_failed") or max(total - passed, 0))
    if claim_set in RELEASE_RELIABLE_CLAIM_SETS:
        target_scope = "release_reliable"
    elif claim_set in FRONTIER_BENCHMARK_CLAIM_SETS:
        target_scope = "frontier_collect"
    else:
        target_scope = "supported_attempt"
    fallback[target_scope] = {
        "claims_total": total,
        "claims_passed": passed,
        "claims_failed": failed,
        "pass_at_1": round(passed / total, 6) if total else 0.0,
    }
    return fallback


def load_benchmark_summaries(
    *,
    output_dir: Path | None = None,
    claim_sets: tuple[str, ...] = STANDARD_BENCHMARK_CLAIM_SETS,
) -> list[tuple[str, Path, dict[str, Any]]]:
    loaded: list[tuple[str, Path, dict[str, Any]]] = []
    base_dir = output_dir or Path("benchmark_baselines/v3_alpha/benchmark_mode")
    for claim_set in claim_sets:
        path = base_dir / f"{claim_set}.json"
        summary = load_summary(claim_set, base_dir)
        loaded.append((claim_set, path, summary))
    return loaded


def build_markdown_report(items: list[tuple[str, Path, dict[str, Any]]], *, source_dir: Path) -> str:
    summaries = [summary for _, _, summary in items]
    claims_total = sum(int(summary.get("claims_total") or 0) for summary in summaries)
    claims_passed = sum(int(summary.get("claims_passed") or 0) for summary in summaries)
    total_cost = round(sum(_total_cost(summary) for summary in summaries), 8)

    overview_rows: list[list[str]] = []
    tooling_rows: list[list[str]] = []
    latency_rows: list[list[str]] = []
    retrieval_rows: list[list[str]] = []
    scope_rows: list[list[str]] = []
    overall_scoped_metrics = _empty_scope_metrics()
    failure_counts: Counter[str] = Counter()
    failure_by_tier: dict[str, Counter[str]] = {}

    for claim_set, path, summary in items:
        claims_passed_set = int(summary.get("claims_passed") or 0)
        claims_total_set = int(summary.get("claims_total") or 0)
        failure_counter = Counter({str(code): int(count) for code, count in (summary.get("failure_counts") or {}).items()})
        failure_counts.update(failure_counter)
        failure_by_tier[claim_set] = failure_counter

        overview_rows.append(
            [
                claim_set,
                summary.get("generated_at", "n/a"),
                _format_percent(float(summary.get("pass_at_1") or 0.0)),
                f"{claims_passed_set}/{claims_total_set}",
                str(int(summary.get("claims_failed") or 0)),
                _format_usd(_total_cost(summary)),
                path.name,
            ]
        )
        latency_rows.append(
            [
                claim_set,
                _format_duration_ms(_average_stage_latency_ms(summary, "planner_ms")),
                _format_duration_ms(_average_stage_latency_ms(summary, "formalizer_ms")),
                _format_duration_ms(_average_stage_latency_ms(summary, "prover_ms")),
                _format_duration_ms(_average_stage_latency_ms(summary, "total_ms")),
            ]
        )
        tooling_rows.append(
            [
                claim_set,
                str(summary.get("average_tool_calls", "0.0")),
                str(summary.get("average_lsp_tool_calls", "0.0")),
                str(summary.get("average_native_search_attempts", "0.0")),
                str(summary.get("mathlib_native_mode_usage", "0")),
            ]
        )
        progress_jsonl = path.with_suffix(".progress.jsonl")
        rm = _compute_retrieval_metrics(progress_jsonl)
        retrieval_rows.append(
            [
                claim_set,
                _format_percent(rm.get("local_rag_hit_rate", 0.0)),
                _format_percent(rm.get("leansearch_hit_rate_at_5", 0.0)),
                _format_duration_ms(rm.get("hybrid_retrieval_latency_ms", 0.0)),
                str(int(rm.get("leansearch_call_count", 0))),
                _format_percent(rm.get("second_retrieval_rate", 0.0)),
                _format_percent(rm.get("enriched_leansearch_hit_rate", 0.0)),
            ]
        )
        scoped_metrics = _summary_scope_metrics(claim_set, summary)
        for scope_name, payload in scoped_metrics.items():
            overall_payload = overall_scoped_metrics[scope_name]
            overall_payload["claims_total"] += int((payload or {}).get("claims_total") or 0)
            overall_payload["claims_passed"] += int((payload or {}).get("claims_passed") or 0)
            overall_payload["claims_failed"] += int((payload or {}).get("claims_failed") or 0)
        reliable = scoped_metrics.get("release_reliable", {})
        frontier_total = sum(
            int((scoped_metrics.get(scope) or {}).get("claims_total") or 0)
            for scope in ("supported_attempt", "frontier_collect", "out_of_scope")
        )
        frontier_passed = sum(
            int((scoped_metrics.get(scope) or {}).get("claims_passed") or 0)
            for scope in ("supported_attempt", "frontier_collect", "out_of_scope")
        )
        scope_rows.append(
            [
                claim_set,
                _format_percent(float(reliable.get("pass_at_1") or 0.0)),
                f"{int(reliable.get('claims_passed') or 0)}/{int(reliable.get('claims_total') or 0)}",
                _format_percent((frontier_passed / frontier_total) if frontier_total else 0.0),
                f"{frontier_passed}/{frontier_total}",
            ]
        )

    overall_pass = (claims_passed / claims_total) if claims_total else 0.0
    overview_rows.append(
        [
            "overall",
            "-",
            _format_percent(overall_pass),
            f"{claims_passed}/{claims_total}",
            str(claims_total - claims_passed),
            _format_usd(total_cost),
            "-",
        ]
    )
    latency_rows.append(
        [
            "overall",
            _format_duration_ms(
                round(sum(_average_stage_latency_ms(summary, "planner_ms") for summary in summaries) / len(summaries), 3)
                if summaries
                else 0.0
            ),
            _format_duration_ms(
                round(sum(_average_stage_latency_ms(summary, "formalizer_ms") for summary in summaries) / len(summaries), 3)
                if summaries
                else 0.0
            ),
            _format_duration_ms(
                round(sum(_average_stage_latency_ms(summary, "prover_ms") for summary in summaries) / len(summaries), 3)
                if summaries
                else 0.0
            ),
            _format_duration_ms(
                round(sum(_average_stage_latency_ms(summary, "total_ms") for summary in summaries) / len(summaries), 3)
                if summaries
                else 0.0
            ),
        ]
    )
    all_results = [result for summary in summaries for result in summary.get("results", [])]
    for payload in overall_scoped_metrics.values():
        total = int(payload.get("claims_total") or 0)
        passed = int(payload.get("claims_passed") or 0)
        payload["pass_at_1"] = round(passed / total, 6) if total else 0.0
    reliable = overall_scoped_metrics.get("release_reliable", {})
    frontier_total = sum(
        int((overall_scoped_metrics.get(scope) or {}).get("claims_total") or 0)
        for scope in ("supported_attempt", "frontier_collect", "out_of_scope")
    )
    frontier_passed = sum(
        int((overall_scoped_metrics.get(scope) or {}).get("claims_passed") or 0)
        for scope in ("supported_attempt", "frontier_collect", "out_of_scope")
    )
    scope_rows.append(
        [
            "overall",
            _format_percent(float(reliable.get("pass_at_1") or 0.0)),
            f"{int(reliable.get('claims_passed') or 0)}/{int(reliable.get('claims_total') or 0)}",
            _format_percent((frontier_passed / frontier_total) if frontier_total else 0.0),
            f"{frontier_passed}/{frontier_total}",
        ]
    )
    tooling_rows.append(
        [
            "overall",
            str(round(sum(int(item.get("tool_calls") or 0) for item in all_results) / len(all_results), 3) if all_results else 0.0),
            str(round(sum(int(item.get("lsp_tool_calls") or 0) for item in all_results) / len(all_results), 3) if all_results else 0.0),
            str(round(sum(int(item.get("native_search_attempts") or 0) for item in all_results) / len(all_results), 3) if all_results else 0.0),
            str(sum(int(item.get("mathlib_native_mode_usage") or 0) for item in all_results)),
        ]
    )

    lines = [
        "# Benchmark Summary",
        "",
        f"Source directory: `{source_dir}`",
        "",
        f"- Overall pass@1: {_format_percent(overall_pass)} ({claims_passed}/{claims_total})",
        "- Release-reliable pass@1 is computed only from `release_reliable` scoped claims; "
        "`supported_attempt`, `frontier_collect`, and `out_of_scope` claims are excluded from that denominator.",
        f"- Total estimated cost: {_format_usd(total_cost)}",
        "",
        "## Overview",
        "",
        _render_markdown_table(
            ["Claim Set", "Generated At", "Pass@1", "Passed", "Failed", "Cost USD", "File"],
            overview_rows,
        ),
        "",
        "## Average Latency By Stage",
        "",
        _render_markdown_table(["Claim Set", "Planner", "Formalizer", "Prover", "Total"], latency_rows),
        "",
        "## Scope-Separated Metrics",
        "",
        _render_markdown_table(
            [
                "Claim Set",
                "Release-Reliable Pass@1",
                "Release-Reliable Passed",
                "Frontier/Attempt Pass@1",
                "Frontier/Attempt Passed",
            ],
            scope_rows,
        ),
        "",
        "## Tooling Observability",
        "",
        _render_markdown_table(
            ["Claim Set", "Avg Tool Calls", "Avg LSP Tool Calls", "Avg Native Search Attempts", "Mathlib Native Mode Uses"],
            tooling_rows,
        ),
        "",
        "## Hybrid Retrieval Metrics",
        "",
        _render_markdown_table(
            [
                "Claim Set",
                "Local RAG Hit Rate",
                "LeanSearch Hit Rate@5",
                "LeanSearch Avg Latency",
                "LeanSearch Calls",
                "2nd-Pass Rate",
                "Enriched Hit Rate",
            ],
            retrieval_rows,
        ),
        "",
        "## Failure Breakdown",
        "",
    ]

    if failure_counts:
        failure_rows = []
        for code, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0])):
            failure_rows.append(
                [
                    code,
                    *(str(failure_by_tier[claim_set].get(code, 0)) for claim_set, _, _ in items),
                    str(count),
                ]
            )
        lines.append(
            _render_markdown_table(
                ["Failure Code", *(claim_set for claim_set, _, _ in items), "Total"],
                failure_rows,
            )
        )
    else:
        lines.append("No failures recorded in the loaded summaries.")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--claim-set", action="append", choices=STANDARD_BENCHMARK_CLAIM_SETS)
    args = parser.parse_args(argv)

    claim_sets = tuple(args.claim_set or STANDARD_BENCHMARK_CLAIM_SETS)
    source_dir = args.output_dir or Path("benchmark_baselines/v3_alpha/benchmark_mode")
    items = load_benchmark_summaries(output_dir=args.output_dir, claim_sets=claim_sets)
    print(build_markdown_report(items, source_dir=source_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
