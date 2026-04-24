"""Aggregate completed benchmark runs into append-only history rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.common import STANDARD_BENCHMARK_CLAIM_SETS
from src.config import BENCHMARK_BASELINE_DIR

HISTORY_FILENAME = "benchmark_history.jsonl"
CANONICAL_HISTORY_PATH = BENCHMARK_BASELINE_DIR / HISTORY_FILENAME


def benchmark_history_path(output_dir: Path | None = None) -> Path:
    if output_dir is None:
        return CANONICAL_HISTORY_PATH
    resolved_output_dir = output_dir.resolve()
    if resolved_output_dir == BENCHMARK_BASELINE_DIR or resolved_output_dir.parent == BENCHMARK_BASELINE_DIR:
        return CANONICAL_HISTORY_PATH
    return resolved_output_dir / HISTORY_FILENAME


def load_history_rows(history_path: Path | None = None) -> list[dict[str, Any]]:
    path = history_path or CANONICAL_HISTORY_PATH
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_history_row(
    run_summary: dict[str, Any],
    *,
    row_id: str | None = None,
) -> dict[str, Any]:
    claim_set_summaries = _claim_set_summaries(run_summary)
    all_results = [result for summary in claim_set_summaries for result in summary.get("results", [])]
    selected_claim_sets = [str(summary.get("claim_set") or "") for summary in claim_set_summaries if summary.get("claim_set")]
    bucket_breakdown = {
        claim_set: _claim_set_metrics(_find_claim_set_summary(claim_set_summaries, claim_set))
        for claim_set in STANDARD_BENCHMARK_CLAIM_SETS
    }
    for summary in claim_set_summaries:
        claim_set = str(summary.get("claim_set") or "")
        if claim_set and claim_set not in bucket_breakdown:
            bucket_breakdown[claim_set] = _claim_set_metrics(summary)

    return {
        "row_id": row_id,
        "timestamp": str(run_summary.get("generated_at") or ""),
        "mode": str(run_summary.get("mode") or ""),
        "benchmark_mode": bool(run_summary.get("benchmark_mode")),
        "selected_claim_sets": selected_claim_sets,
        "claims_total": int(run_summary.get("claims_total") or 0),
        "claims_passed": int(run_summary.get("claims_passed") or 0),
        "claims_failed": int(run_summary.get("claims_failed") or 0),
        "overall_pass_rate": round(float(run_summary.get("pass_at_1") or 0.0), 6),
        "total_cost_usd": _total_cost(run_summary),
        "avg_latency_total": _average_latency_total(all_results),
        "direct_close_rate": _rate(_direct_close_count(all_results), len(all_results)),
        "direct_close_count": _direct_close_count(all_results),
        "avg_tool_calls": _average_metric(all_results, "tool_calls"),
        "avg_decomposition_depth": _average_metric(all_results, "decomposition_depth"),
        "no_progress_stall_count": _no_progress_stall_count(all_results),
        "schema_invalid_rate": _rate(_schema_invalid_count(all_results), len(all_results)),
        "schema_invalid_count": _schema_invalid_count(all_results),
        "claim_type_mix": _claim_type_mix(claim_set_summaries),
        "bucket_breakdown": bucket_breakdown,
    }


def append_history_row(
    run_summary: dict[str, Any],
    *,
    history_path: Path | None = None,
) -> dict[str, Any]:
    path = history_path or CANONICAL_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = load_history_rows(path)
    row = build_history_row(run_summary, row_id=f"run_{len(existing_rows) + 1:06d}")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def _claim_set_summaries(run_summary: dict[str, Any]) -> list[dict[str, Any]]:
    claim_sets = run_summary.get("claim_sets")
    if isinstance(claim_sets, list):
        return [dict(item) for item in claim_sets]
    return [dict(run_summary)]


def _find_claim_set_summary(claim_set_summaries: list[dict[str, Any]], claim_set: str) -> dict[str, Any] | None:
    for summary in claim_set_summaries:
        if str(summary.get("claim_set") or "") == claim_set:
            return summary
    return None


def _claim_set_metrics(summary: dict[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {
            "present": False,
            "claims_total": 0,
            "claims_passed": 0,
            "claims_failed": 0,
            "pass_rate": 0.0,
            "total_cost_usd": 0.0,
            "avg_latency_total": 0.0,
            "direct_close_rate": 0.0,
            "direct_close_count": 0,
            "avg_tool_calls": 0.0,
            "avg_decomposition_depth": 0.0,
            "no_progress_stall_count": 0,
            "schema_invalid_rate": 0.0,
            "schema_invalid_count": 0,
            "claim_type_mix": {"preamble_definable": 0, "mathlib_native": 0},
        }
    results = [dict(item) for item in summary.get("results", [])]
    claims_total = int(summary.get("claims_total") or len(results))
    return {
        "present": True,
        "claims_total": claims_total,
        "claims_passed": int(summary.get("claims_passed") or 0),
        "claims_failed": int(summary.get("claims_failed") or 0),
        "pass_rate": round(float(summary.get("pass_at_1") or 0.0), 6),
        "total_cost_usd": _total_cost(summary),
        "avg_latency_total": _average_latency_total(results),
        "direct_close_rate": _rate(_direct_close_count(results), claims_total),
        "direct_close_count": _direct_close_count(results),
        "avg_tool_calls": _average_metric(results, "tool_calls"),
        "avg_decomposition_depth": _average_metric(results, "decomposition_depth"),
        "no_progress_stall_count": _no_progress_stall_count(results),
        "schema_invalid_rate": _rate(_schema_invalid_count(results), claims_total),
        "schema_invalid_count": _schema_invalid_count(results),
        "claim_type_mix": _claim_type_mix([summary]),
    }


def _average_latency_total(results: list[dict[str, Any]]) -> float:
    if not results:
        return 0.0
    total = sum(float((result.get("timing_breakdown") or {}).get("total_ms") or 0.0) for result in results)
    return round(total / len(results), 3)


def _average_metric(results: list[dict[str, Any]], key: str) -> float:
    if not results:
        return 0.0
    total = sum(float(result.get(key) or 0.0) for result in results)
    return round(total / len(results), 3)


def _total_cost(summary: dict[str, Any]) -> float:
    return round(sum(float(value or 0.0) for value in (summary.get("cost_by_stage") or {}).values()), 8)


def _direct_close_count(results: list[dict[str, Any]]) -> int:
    return sum(1 for result in results if _is_direct_close(result))


def _is_direct_close(result: dict[str, Any]) -> bool:
    if str(result.get("verified_via") or "") == "trivial_shortcut":
        return True
    for event in result.get("progress_events", []):
        if str(event.get("message") or "").strip() == "Closed via direct definable closure.":
            return True
    return False


def _no_progress_stall_count(results: list[dict[str, Any]]) -> int:
    return sum(1 for result in results if str(result.get("termination_reason") or "") == "no_progress_stall")


def _schema_invalid_count(results: list[dict[str, Any]]) -> int:
    total = 0
    for result in results:
        planner_usage = (result.get("usage_by_stage") or {}).get("planner") or {}
        if result.get("raw_planner_response") is not None or planner_usage.get("error_code") == "schema_invalid":
            total += 1
            continue
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            if bool(metadata.get("schema_invalid_repaired")):
                total += 1
                break
    return total


def _claim_type_mix(claim_set_summaries: list[dict[str, Any]]) -> dict[str, int]:
    mix = {"preamble_definable": 0, "mathlib_native": 0}
    for summary in claim_set_summaries:
        manifest = summary.get("claim_set_manifest") or {}
        bucket_counts = manifest.get("bucket_counts") or {}
        if bucket_counts:
            mix["preamble_definable"] += int(bucket_counts.get("preamble_definable") or 0)
            mix["mathlib_native"] += int(bucket_counts.get("mathlib_native") or 0)
            continue
        for result in summary.get("results", []):
            bucket = str(result.get("benchmark_bucket") or "")
            if bucket in mix:
                mix[bucket] += 1
    return mix


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total, 6)
