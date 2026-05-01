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
        "avg_lsp_tool_calls": _average_metric(all_results, "lsp_tool_calls"),
        "total_lsp_tool_calls": _sum_metric(all_results, "lsp_tool_calls"),
        "avg_native_search_attempts": _average_metric(all_results, "native_search_attempts"),
        "total_native_search_attempts": _sum_metric(all_results, "native_search_attempts"),
        "mathlib_native_mode_usage": _sum_metric(all_results, "mathlib_native_mode_usage"),
        "avg_decomposition_depth": _average_metric(all_results, "decomposition_depth"),
        "synthesis_efficiency": _synthesis_efficiency(all_results, run_summary),
        "premise_match_rate@3": _premise_match_rate_at_3(all_results, run_summary),
        "synthesis_event_count": _synthesis_event_count(all_results, run_summary),
        "premise_matched_synthesis_event_count": _premise_matched_synthesis_event_count(
            all_results,
            run_summary,
        ),
        "premise_top3_synthesis_event_count": _premise_top3_synthesis_event_count(
            all_results,
            run_summary,
        ),
        "synthesis_candidate_used_count": _synthesis_candidate_used_count(
            all_results,
            run_summary,
        ),
        "resolved_premise_rate": _resolved_premise_rate(all_results, run_summary),
        "candidate_attempt_count": _candidate_attempt_count(all_results, run_summary),
        "candidate_success_rate": _candidate_success_rate(all_results, run_summary),
        "provider_fallback_rate": _provider_fallback_rate(all_results, run_summary),
        "repl_compile_disagreement_count": _repl_compile_disagreement_count(
            all_results,
            run_summary,
        ),
        "avg_decomposition_depth_mathlib": _avg_decomposition_depth_mathlib(
            all_results,
            run_summary,
        ),
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
            "avg_lsp_tool_calls": 0.0,
            "total_lsp_tool_calls": 0,
            "avg_native_search_attempts": 0.0,
            "total_native_search_attempts": 0,
            "mathlib_native_mode_usage": 0,
            "avg_decomposition_depth": 0.0,
            "synthesis_efficiency": 0.0,
            "premise_match_rate@3": 0.0,
            "synthesis_event_count": 0,
            "premise_matched_synthesis_event_count": 0,
            "premise_top3_synthesis_event_count": 0,
            "synthesis_candidate_used_count": 0,
            "resolved_premise_rate": 0.0,
            "candidate_attempt_count": 0,
            "candidate_success_rate": 0.0,
            "provider_fallback_rate": 0.0,
            "repl_compile_disagreement_count": 0,
            "avg_decomposition_depth_mathlib": 0.0,
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
        "avg_lsp_tool_calls": _average_metric(results, "lsp_tool_calls"),
        "total_lsp_tool_calls": _sum_metric(results, "lsp_tool_calls"),
        "avg_native_search_attempts": _average_metric(results, "native_search_attempts"),
        "total_native_search_attempts": _sum_metric(results, "native_search_attempts"),
        "mathlib_native_mode_usage": _sum_metric(results, "mathlib_native_mode_usage"),
        "avg_decomposition_depth": _average_metric(results, "decomposition_depth"),
        "synthesis_efficiency": _synthesis_efficiency(results, summary),
        "premise_match_rate@3": _premise_match_rate_at_3(results, summary),
        "synthesis_event_count": _synthesis_event_count(results, summary),
        "premise_matched_synthesis_event_count": _premise_matched_synthesis_event_count(
            results,
            summary,
        ),
        "premise_top3_synthesis_event_count": _premise_top3_synthesis_event_count(
            results,
            summary,
        ),
        "synthesis_candidate_used_count": _synthesis_candidate_used_count(results, summary),
        "resolved_premise_rate": _resolved_premise_rate(results, summary),
        "candidate_attempt_count": _candidate_attempt_count(results, summary),
        "candidate_success_rate": _candidate_success_rate(results, summary),
        "provider_fallback_rate": _provider_fallback_rate(results, summary),
        "repl_compile_disagreement_count": _repl_compile_disagreement_count(results, summary),
        "avg_decomposition_depth_mathlib": _avg_decomposition_depth_mathlib(results, summary),
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


def _sum_metric(results: list[dict[str, Any]], key: str) -> int:
    return sum(int(result.get(key) or 0) for result in results)


def _synthesis_event_payloads(results: list[dict[str, Any]], summary: dict[str, Any]) -> list[dict[str, Any]]:
    payloads = [
        dict(event)
        for event in summary.get("synthesis_events", [])
        if isinstance(event, dict)
    ]
    if payloads:
        return payloads
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "SynthesisEvent" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("synthesis_events", []):
            if isinstance(event, dict):
                payloads.append(dict(event))
    return payloads


def _synthesis_efficiency(results: list[dict[str, Any]], summary: dict[str, Any]) -> float:
    if "synthesis_efficiency" in summary:
        return round(float(summary.get("synthesis_efficiency") or 0.0), 6)
    events = _synthesis_event_payloads(results, summary)
    if not events:
        return 0.0
    matched = sum(1 for event in events if event.get("referenced_premises"))
    return round(matched / len(events), 6)


def _premise_match_rate_at_3(results: list[dict[str, Any]], summary: dict[str, Any]) -> float:
    if "premise_match_rate@3" in summary:
        return round(float(summary.get("premise_match_rate@3") or 0.0), 6)
    events = _synthesis_event_payloads(results, summary)
    if not events:
        return 0.0
    matched = sum(1 for event in events if bool(event.get("top3_match")))
    return round(matched / len(events), 6)


def _synthesis_event_count(results: list[dict[str, Any]], summary: dict[str, Any]) -> int:
    if "synthesis_event_count" in summary:
        return int(summary.get("synthesis_event_count") or 0)
    return len(_synthesis_event_payloads(results, summary))


def _premise_matched_synthesis_event_count(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> int:
    if "premise_matched_synthesis_event_count" in summary:
        return int(summary.get("premise_matched_synthesis_event_count") or 0)
    return sum(
        1
        for event in _synthesis_event_payloads(results, summary)
        if event.get("referenced_premises")
    )


def _premise_top3_synthesis_event_count(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> int:
    if "premise_top3_synthesis_event_count" in summary:
        return int(summary.get("premise_top3_synthesis_event_count") or 0)
    return sum(
        1
        for event in _synthesis_event_payloads(results, summary)
        if bool(event.get("top3_match"))
    )


def _synthesis_candidate_used_count(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> int:
    if "synthesis_candidate_used_count" in summary:
        return int(summary.get("synthesis_candidate_used_count") or 0)
    return _sum_metric(results, "synthesis_candidate_used_count")


def _premise_resolution_event_payloads(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    payloads = [
        dict(event)
        for event in summary.get("premise_resolution_events", [])
        if isinstance(event, dict)
    ]
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "PremiseResolutionEvent" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            payload = (event.get("metadata") or {}).get("PremiseResolutionEvent")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    return payloads


def _candidate_tactic_event_payloads(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    payloads = [
        dict(event)
        for event in summary.get("candidate_tactic_events", [])
        if isinstance(event, dict)
    ]
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "CandidateTacticEvent" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            payload = (event.get("metadata") or {}).get("CandidateTacticEvent")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    return payloads


def _resolved_premise_rate(results: list[dict[str, Any]], summary: dict[str, Any]) -> float:
    if "resolved_premise_rate" in summary:
        return round(float(summary.get("resolved_premise_rate") or 0.0), 6)
    events = _premise_resolution_event_payloads(results, summary)
    if not events:
        return 0.0
    resolved = sum(1 for event in events if bool(event.get("resolved")))
    return round(resolved / len(events), 6)


def _candidate_attempt_count(results: list[dict[str, Any]], summary: dict[str, Any]) -> int:
    if "candidate_attempt_count" in summary:
        return int(summary.get("candidate_attempt_count") or 0)
    return len(_candidate_tactic_event_payloads(results, summary))


def _candidate_success_rate(results: list[dict[str, Any]], summary: dict[str, Any]) -> float:
    if "candidate_success_rate" in summary:
        return round(float(summary.get("candidate_success_rate") or 0.0), 6)
    events = _candidate_tactic_event_payloads(results, summary)
    if not events:
        return 0.0
    successes = sum(1 for event in events if bool(event.get("success")))
    return round(successes / len(events), 6)


def _provider_fallback_rate(results: list[dict[str, Any]], summary: dict[str, Any]) -> float:
    if "provider_fallback_rate" in summary:
        return round(float(summary.get("provider_fallback_rate") or 0.0), 6)
    mathlib_results = [
        result for result in results if result.get("benchmark_bucket") == "mathlib_native"
    ]
    if not mathlib_results:
        return 0.0
    fallbacks = sum(
        1
        for result in mathlib_results
        if int(result.get("provider_fallback_count") or 0) > 0
    )
    return round(fallbacks / len(mathlib_results), 6)


def _repl_compile_disagreement_count(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> int:
    if "repl_compile_disagreement_count" in summary:
        return int(summary.get("repl_compile_disagreement_count") or 0)
    return sum(1 for result in results if result.get("failure_code") == "repl_compile_disagreement")


def _avg_decomposition_depth_mathlib(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
) -> float:
    if "avg_decomposition_depth_mathlib" in summary:
        return round(float(summary.get("avg_decomposition_depth_mathlib") or 0.0), 3)
    mathlib_results = [
        result for result in results if result.get("benchmark_bucket") == "mathlib_native"
    ]
    if not mathlib_results:
        return 0.0
    return round(
        sum(float(result.get("decomposition_depth") or 0.0) for result in mathlib_results)
        / len(mathlib_results),
        3,
    )


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
