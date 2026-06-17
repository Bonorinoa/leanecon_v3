"""Live local-gate benchmark runner."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from evals.benchmark_manifest import build_claim_set_manifest, classify_claim
from evals.common import (
    FRONTIER_BENCHMARK_CLAIM_SETS,
    LOCAL_GATE_DEFAULT_CLAIM_SETS,
    append_progress_event,
    load_claims,
    reset_progress_log,
    write_progress_log,
    write_summary,
)
from src.budget_profiles import (
    BudgetProfile,
    clamp_target_timeouts,
    evaluate_provider_guardrail,
    resolve_budget_profile,
)
from src.claim_scope import (
    FRONTIER_COLLECT,
    FRONTIER_RECORD_SCHEMA_VERSION,
    OUT_OF_SCOPE,
    RELEASE_RELIABLE,
    SUPPORTED_ATTEMPT,
    ScopeClassification,
    build_frontier_record,
    classify_claim_scope,
    classify_failure,
    metrics_by_scope,
    scope_counts,
)
from src.backend_capabilities import get_backend_capability
from src.config import BENCHMARK_BASELINE_DIR, BENCHMARK_REQUIRE_PRICING, LEAN_WORKSPACE, PROVER_PROVIDER
from src.evals.metrics_aggregator import append_history_row, benchmark_history_path
from src.formalizer import DEFAULT_FORMALIZER, FormalizerService
from src.lean import compile_check, lean_workspace_probe
from src.observability import (
    StageExecutionError,
    build_progress_event,
    classify_exception,
    lookup_pricing,
)
from src.planner import PlannerService
from src.providers import normalize_huggingface_provider
from src.prover import DEFAULT_PROVER, Prover, ProverTargetTimeouts
from src.prover.models import ProverResult
from src.prover.prover import _replace_named_theorem_body
from src.prover.tactics import direct_hypothesis_name

CLAIM_SETS = LOCAL_GATE_DEFAULT_CLAIM_SETS
LIVE_TARGET_TIMEOUTS = ProverTargetTimeouts(theorem_body=300, subgoal=180, apollo_lemma=120)
BENCHMARK_TARGET_TIMEOUTS = ProverTargetTimeouts(theorem_body=120, subgoal=120, apollo_lemma=120)
DEFAULT_SAMPLE_SEED = 17
HEARTBEAT_SECONDS = max(5.0, float(os.getenv("LEANECON_LOCAL_GATE_HEARTBEAT_SECONDS", "30")))
FOCUSED_SAMPLE_IDS_BY_SET: dict[str, tuple[str, ...]] = {
    "tier2_frontier_mathlib_native": (
        "t2_contraction_mapping_fixed_point",
        "t2_extreme_value_repair",
        "t2_monotone_sequence_converges",
    ),
    "tier2_frontier_preamble_definable": (
        "t2_pareto_dominance_transitive",
        "t2_utilitarian_swf_pareto_monotone",
        "t2_bellman_monotone_value_function",
        "t2_expected_payoff_convex_mixture",
        "t2_ces_crs",
        "t2_stone_geary_monotone_alpha",
        "t2_bellman_contraction",
        "t2_phillips_curve_stagflation",
        "t2_indirect_utility_roys_identity",
    ),
}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _progress_event(
    event: str,
    *,
    claim_id: str,
    stage: str,
    status: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_progress_event(
        event,
        claim_id=claim_id,
        stage=stage,
        status=status,
        message=message,
        metadata=metadata,
    )


def _supports_color(stream: Any) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)()) and os.environ.get("NO_COLOR") is None


def _style(text: str, code: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _format_duration_ms(value: float | int | None) -> str:
    ms = float(value or 0.0)
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms:.0f}ms"


def _format_usd(value: float | int | None) -> str:
    return f"${float(value or 0.0):.4f}"


def _format_ratio(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0% (0/0)"
    return f"{(numerator / denominator) * 100:.1f}% ({numerator}/{denominator})"


def _truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    border = "+-" + "-+-".join("-" * width for width in widths) + "-+"
    header_row = (
        "| "
        + " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
        + " |"
    )
    body = [
        "| " + " | ".join(value.ljust(widths[index]) for index, value in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([border, header_row, border, *body, border])


def _summary_average_latency_ms(summary: dict[str, Any], stage: str) -> float:
    executed = summary.get("executed", True)
    results = summary.get("results", [])
    if not executed or not results:
        return 0.0
    total = sum(
        float((result.get("timing_breakdown") or {}).get(stage) or 0.0) for result in results
    )
    return round(total / len(results), 3)


def _summary_total_cost(summary: dict[str, Any]) -> float:
    return round(sum(float(value or 0.0) for value in summary.get("cost_by_stage", {}).values()), 8)


def _target_timeouts_for_profile(*, benchmark_mode: bool, profile: BudgetProfile) -> ProverTargetTimeouts:
    if profile.name == "release":
        base = BENCHMARK_TARGET_TIMEOUTS if benchmark_mode else LIVE_TARGET_TIMEOUTS
        requested = base.model_dump(mode="json")
    else:
        requested = profile.target_timeout_caps
    return ProverTargetTimeouts.model_validate(clamp_target_timeouts(requested, profile))


def _provider_stage_config(
    planner_service: PlannerService,
    formalizer_service: FormalizerService,
    prover_instance: Prover,
) -> dict[str, dict[str, str]]:
    fallback_backend = getattr(prover_instance, "fallback_backend", None)
    stages = {
        "planner": {
            "provider": str(planner_service.backend.provider),
            "model": str(planner_service.backend.model),
        },
        "formalizer": {
            "provider": str(formalizer_service.backend.provider),
            "model": str(formalizer_service.backend.model),
        },
        "prover": {
            "provider": str(prover_instance.primary_backend.provider),
            "model": str(prover_instance.primary_backend.model),
        },
    }
    if fallback_backend is not None:
        stages["prover_fallback"] = {
            "provider": str(getattr(fallback_backend, "provider", "")),
            "model": str(getattr(fallback_backend, "model", "")),
            "backend": str(getattr(fallback_backend, "name", "")),
        }
    return stages


def _profile_claim_set_blockers(
    *,
    profile: BudgetProfile,
    claim_set: str,
    claim_set_manifest: dict[str, Any],
) -> list[str]:
    blockers: list[str] = []
    bucket_counts = claim_set_manifest.get("bucket_counts") or {}
    if claim_set in FRONTIER_BENCHMARK_CLAIM_SETS and not profile.allow_frontier_claims:
        blockers.append("budget_profile_disallows_frontier_claim_set")
    if int(bucket_counts.get("mathlib_native") or 0) > 0 and not profile.allow_mathlib_native:
        blockers.append("budget_profile_disallows_mathlib_native")
    return blockers


def _release_metrics_for_profile(profile: BudgetProfile, results: list[dict[str, Any]]) -> dict[str, Any]:
    scoped = metrics_by_scope(results)
    if profile.release_metrics_eligible:
        return scoped[RELEASE_RELIABLE]
    return {
        "claims_total": 0,
        "claims_passed": 0,
        "claims_failed": 0,
        "pass_at_1": 0.0,
    }


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _lean_workspace_preflight() -> dict[str, Any]:
    """Probe Lean and optionally hydrate Mathlib cache before a benchmark run."""

    initial_probe = lean_workspace_probe()
    cache_get_requested = _env_flag_enabled("LEANECON_PREBUILD_LEAN")
    should_run_cache_get = cache_get_requested or not bool(initial_probe.get("available"))
    cache_get: dict[str, Any] = {
        "executed": False,
        "requested": cache_get_requested,
        "reason": "explicit_request" if cache_get_requested else "probe_failed",
    }

    if should_run_cache_get and shutil.which("lake") is not None and LEAN_WORKSPACE.exists():
        timeout = int(os.getenv("LEANECON_PREBUILD_LEAN_TIMEOUT", "180"))
        started = time.perf_counter()
        try:
            result = subprocess.run(
                ["lake", "exe", "cache", "get"],
                cwd=str(LEAN_WORKSPACE),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            cache_get.update(
                {
                    "executed": True,
                    "success": result.returncode == 0,
                    "exit_code": result.returncode,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "stdout_tail": (result.stdout or "")[-2000:],
                    "stderr_tail": (result.stderr or "")[-2000:],
                }
            )
        except subprocess.TimeoutExpired as exc:
            cache_get.update(
                {
                    "executed": True,
                    "success": False,
                    "exit_code": -1,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "stderr_tail": f"lake exe cache get timed out after {timeout}s",
                    "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
                }
            )
        except FileNotFoundError:
            cache_get.update(
                {
                    "executed": False,
                    "success": False,
                    "exit_code": None,
                    "stderr_tail": "lake executable not found on PATH.",
                }
            )

    final_probe = lean_workspace_probe()
    return {
        "available": bool(final_probe.get("available")),
        "initial_probe": initial_probe,
        "final_probe": final_probe,
        "cache_get": cache_get,
    }


def _accumulate_usage_dimension(
    usage: dict[str, Any] | None,
    *,
    key: str,
    bucket_map: dict[str, dict[str, Any]],
) -> None:
    if not usage:
        return
    bucket = bucket_map.setdefault(
        key,
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "records": 0,
        },
    )
    bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    bucket["estimated_cost_usd"] = round(
        float(bucket["estimated_cost_usd"]) + float(usage.get("estimated_cost_usd") or 0.0),
        8,
    )
    bucket["records"] += 1


def _budget_exhaustion_reason(
    *,
    failure_code: str | None,
    termination_reason: str | None,
    profile: BudgetProfile,
    tool_budget: dict[str, Any],
    max_turns: int,
    timeout: int,
) -> dict[str, Any] | None:
    reason = str(failure_code or termination_reason or "")
    if not reason:
        return None
    if reason == "max_turns_exhausted":
        return {
            "reason": reason,
            "budget_profile": profile.name,
            "cap": "max_prover_turns",
            "cap_value": profile.max_prover_turns,
            "effective_value": max_turns,
        }
    if reason in {"tool_budget_exhausted", "budget_exhausted"}:
        return {
            "reason": reason,
            "budget_profile": profile.name,
            "cap": "max_total_tool_calls",
            "cap_value": int(tool_budget.get("max_total_tool_calls") or profile.max_total_tool_calls),
            "used": int(tool_budget.get("total_tool_calls") or 0),
        }
    if "search_exhausted" in reason:
        return {
            "reason": reason,
            "budget_profile": profile.name,
            "cap": "max_search_tool_calls",
            "cap_value": int(tool_budget.get("max_search_tool_calls") or profile.max_search_tool_calls),
            "used": int(tool_budget.get("search_tool_calls") or 0),
        }
    if reason == "timeout" or reason.endswith("_timeout"):
        return {
            "reason": reason,
            "budget_profile": profile.name,
            "cap": "max_timeout_seconds",
            "cap_value": profile.max_timeout_seconds,
            "effective_value": timeout,
        }
    return None


def _budget_exhaustion_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    by_profile: dict[str, int] = {}
    total = 0
    for result in results:
        payload = result.get("budget_exhaustion")
        if not isinstance(payload, dict):
            continue
        reason = str(payload.get("reason") or "")
        profile = str(payload.get("budget_profile") or result.get("budget_profile") or "unknown")
        if not reason:
            continue
        total += 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_profile[profile] = by_profile.get(profile, 0) + 1
    return {"total": total, "by_reason": by_reason, "by_profile": by_profile}


class _TerminalReporter:
    def __init__(self, stream: Any = None) -> None:
        self.stream = stream or sys.stdout
        self.color = _supports_color(self.stream)
        self.width = max(shutil.get_terminal_size((100, 20)).columns, 80)
        self._io_lock = threading.Lock()

    def _emit(self, line: str = "") -> None:
        with self._io_lock:
            print(line, file=self.stream)

    def section(self, title: str) -> None:
        label = _style(title, "1;36", enabled=self.color)
        self._emit()
        self._emit(label)

    def claim_set_started(
        self,
        claim_set: str,
        *,
        claims_total: int,
        benchmark_mode: bool,
        budget_profile: BudgetProfile,
        selection_info: dict[str, Any],
        readiness: dict[str, Any],
    ) -> None:
        self.section(f"[{claim_set}] starting")
        mode = "benchmark" if benchmark_mode else "live"
        selection = str(selection_info.get("sampling_mode") or "full")
        if selection_info.get("sample_seed") is not None:
            selection = f"{selection} (seed={selection_info['sample_seed']})"
        rows = [
            ["Mode", mode],
            ["Budget profile", budget_profile.name],
            ["Claims", str(claims_total)],
            ["Sampling", selection],
            ["Readiness", "ready" if readiness.get("ready") else "blocked"],
        ]
        self._emit(_render_table(["Field", "Value"], rows))
        blockers = readiness.get("blockers") or []
        if blockers:
            self._emit(f"Blockers: {', '.join(str(blocker) for blocker in blockers)}")

    def claim_finished(self, index: int, total: int, result: dict[str, Any]) -> None:
        status = str(result.get("status") or "unknown")
        failure_code = result.get("failure_code")
        status_label = status
        if status == "verified":
            status_label = _style("verified", "32", enabled=self.color)
        elif failure_code:
            status_label = _style("failed", "31", enabled=self.color)
        else:
            status_label = _style(status, "33", enabled=self.color)
        progress_width = min(18, max(10, self.width // 8))
        complete = int(round((index / total) * progress_width)) if total else 0
        bar = f"[{'#' * complete}{'-' * (progress_width - complete)}]"
        claim_id = _truncate(str(result.get("id") or ""), 30)
        total_ms = float((result.get("timing_breakdown") or {}).get("total_ms") or 0.0)
        detail = str(result.get("verified_via") or result.get("termination_reason") or "")
        if failure_code:
            detail = str(failure_code)
        self._emit(
            f"{bar} {index:>2}/{total:<2} {status_label:<8} {claim_id:<30} {_format_duration_ms(total_ms):>7} {detail}"
        )

    def claim_started(
        self, claim_set: str, index: int, total: int, claim_id: str, bucket: str
    ) -> None:
        bucket_label = bucket.replace("_", "-")
        self._emit(f"[claim {index:>2}/{total:<2}] {claim_set} {claim_id} bucket={bucket_label}")

    def claim_heartbeat(
        self,
        *,
        claim_set: str,
        index: int,
        total: int,
        claim_id: str,
        stage: str,
        elapsed_s: float,
        message: str | None,
    ) -> None:
        elapsed_label = _format_duration_ms(round(elapsed_s * 1000.0, 3))
        detail = _truncate((message or "").strip(), 48)
        suffix = f" {detail}" if detail else ""
        self._emit(
            f"[heartbeat {index:>2}/{total:<2}] {claim_set} {claim_id} stage={stage} elapsed={elapsed_label}{suffix}"
        )

    def skipped_claim_set(self, claim_set: str, blockers: list[str]) -> None:
        label = _style("skipped", "33", enabled=self.color)
        self._emit(f"{label}: {claim_set} blocked by {', '.join(blockers)}")

    def claim_set_completed(self, summary: dict[str, Any], output_path: Path) -> None:
        self.section(f"[{summary['claim_set']}] summary")
        rows = [
            [
                "Pass@1",
                _format_ratio(
                    int(summary.get("claims_passed") or 0), int(summary.get("claims_total") or 0)
                ),
            ],
            ["Failures", str(int(summary.get("claims_failed") or 0))],
            [
                "Avg total latency",
                _format_duration_ms(_summary_average_latency_ms(summary, "total_ms")),
            ],
            ["Total cost", _format_usd(_summary_total_cost(summary))],
            ["Output", str(output_path)],
        ]
        self._emit(_render_table(["Metric", "Value"], rows))

        latency_rows = [
            ["planner", _format_duration_ms(_summary_average_latency_ms(summary, "planner_ms"))],
            [
                "formalizer",
                _format_duration_ms(_summary_average_latency_ms(summary, "formalizer_ms")),
            ],
            ["prover", _format_duration_ms(_summary_average_latency_ms(summary, "prover_ms"))],
            ["total", _format_duration_ms(_summary_average_latency_ms(summary, "total_ms"))],
        ]
        self._emit(_render_table(["Stage", "Avg latency"], latency_rows))

        failure_counts = summary.get("failure_counts", {})
        if failure_counts:
            failure_rows = [
                [str(code), str(count)]
                for code, count in sorted(
                    failure_counts.items(), key=lambda item: (-int(item[1]), str(item[0]))
                )
            ]
            self._emit(_render_table(["Failure code", "Count"], failure_rows))
        else:
            self._emit("Failures: none")
        scoped = summary.get("metrics_by_scope") or {}
        if scoped:
            scope_rows = [
                [
                    scope_name,
                    _format_ratio(
                        int((payload or {}).get("claims_passed") or 0),
                        int((payload or {}).get("claims_total") or 0),
                    ),
                ]
                for scope_name, payload in scoped.items()
            ]
            self._emit(_render_table(["Scope", "Pass@1"], scope_rows))
        frontier_path = summary.get("frontier_queue_path")
        if frontier_path:
            self._emit(f"Frontier queue: {frontier_path}")

    def combined_completed(self, summary: dict[str, Any], output_path: Path) -> None:
        self.section("[local_gate] combined")
        rows = [
            [
                str(item["claim_set"]),
                _format_ratio(
                    int(item.get("claims_passed") or 0), int(item.get("claims_total") or 0)
                ),
            ]
            for item in summary.get("claim_sets", [])
        ]
        rows.append(
            [
                "local_gate",
                _format_ratio(
                    int(summary.get("claims_passed") or 0), int(summary.get("claims_total") or 0)
                ),
            ]
        )
        self._emit(_render_table(["Claim set", "Pass@1"], rows))
        self._emit(f"Output: {output_path}")

    def history_updated(self, row_id: str, history_path: Path) -> None:
        self._emit(f"History updated: {row_id} ({history_path})")


class _ClaimHeartbeatMonitor:
    def __init__(
        self,
        *,
        claim_set: str,
        claim_id: str,
        claim_index: int,
        claims_total: int,
        reporter: _TerminalReporter | None,
        progress_sink: Callable[[dict[str, Any]], None] | None,
        interval_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        self.claim_set = claim_set
        self.claim_id = claim_id
        self.claim_index = claim_index
        self.claims_total = claims_total
        self.reporter = reporter
        self.progress_sink = progress_sink
        self.interval_seconds = interval_seconds
        self.started_at = time.monotonic()
        self._stage = "claim"
        self._status = "running"
        self._message = "Claim started."
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def update(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._stage = str(event.get("stage") or self._stage)
            self._status = str(event.get("status") or self._status)
            self._message = str(event.get("message") or self._message)

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            with self._lock:
                stage = self._stage
                status = self._status
                message = self._message
            elapsed_s = time.monotonic() - self.started_at
            if self.reporter is not None:
                self.reporter.claim_heartbeat(
                    claim_set=self.claim_set,
                    index=self.claim_index,
                    total=self.claims_total,
                    claim_id=self.claim_id,
                    stage=stage,
                    elapsed_s=elapsed_s,
                    message=message,
                )
            if self.progress_sink is not None:
                self.progress_sink(
                    _progress_event(
                        "claim_heartbeat",
                        claim_id=self.claim_id,
                        stage=stage,
                        status=status,
                        message=f"Heartbeat after {round(elapsed_s, 1)}s.",
                        metadata={
                            "claim_set": self.claim_set,
                            "claim_index": self.claim_index,
                            "claims_total": self.claims_total,
                            "latest_message": message,
                        },
                    )
                )


def _sanitize_job_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_") or "benchmark_claim"


def _usage_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return None


def _planner_raw_response(plan_result: Any) -> tuple[bool, str | None]:
    usage = _usage_dict(getattr(plan_result, "usage", None)) or {}
    if usage.get("error_code") != "schema_invalid":
        return False, None
    for event in getattr(plan_result, "audit_events", []):
        raw_response = getattr(event, "raw_planner_response", None)
        if raw_response is not None:
            return True, raw_response
        metadata = getattr(event, "metadata", {})
        if isinstance(metadata, dict) and metadata.get("raw_planner_response") is not None:
            return True, str(metadata["raw_planner_response"])
    return True, None


def _audit_raw_response(events: list[Any]) -> str | None:
    for event in events:
        if getattr(event, "error_code", None) != "schema_invalid":
            continue
        raw_response = getattr(event, "raw_planner_response", None)
        if raw_response is not None:
            return raw_response
        metadata = getattr(event, "metadata", {})
        if isinstance(metadata, dict) and metadata.get("raw_planner_response") is not None:
            return str(metadata["raw_planner_response"])
    return None


def _accumulate_usage(
    usage: dict[str, Any] | None,
    *,
    tokens_by_stage: dict[str, dict[str, int]],
    cost_by_stage: dict[str, float],
    cost_by_model: dict[str, dict[str, Any]],
    cost_by_claim_type: dict[str, dict[str, Any]] | None = None,
    cost_by_claim_scope: dict[str, dict[str, Any]] | None = None,
    token_usage_sources: dict[str, dict[str, Any]] | None = None,
    latency_by_stage: dict[str, dict[str, Any]] | None = None,
    claim_type: str | None = None,
    claim_scope: str | None = None,
) -> None:
    if not usage:
        return
    stage = str(usage.get("stage") or "unknown")
    provider = str(usage.get("provider") or "unknown")
    model = str(usage.get("model") or "unknown")
    stage_bucket = tokens_by_stage.setdefault(stage, {"input_tokens": 0, "output_tokens": 0})
    stage_bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    stage_bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    cost_by_stage[stage] = round(
        cost_by_stage.get(stage, 0.0) + float(usage.get("estimated_cost_usd") or 0.0), 8
    )
    model_key = f"{provider}:{model}"
    model_bucket = cost_by_model.setdefault(
        model_key,
        {"provider": provider, "model": model, "estimated_cost_usd": 0.0},
    )
    model_bucket["estimated_cost_usd"] = round(
        float(model_bucket["estimated_cost_usd"]) + float(usage.get("estimated_cost_usd") or 0.0),
        8,
    )
    if cost_by_claim_type is not None:
        _accumulate_usage_dimension(
            usage,
            key=str(claim_type or "unknown"),
            bucket_map=cost_by_claim_type,
        )
    if cost_by_claim_scope is not None:
        _accumulate_usage_dimension(
            usage,
            key=str(claim_scope or "unknown"),
            bucket_map=cost_by_claim_scope,
        )
    if token_usage_sources is not None:
        _accumulate_usage_dimension(
            usage,
            key=str(usage.get("usage_source") or "unknown"),
            bucket_map=token_usage_sources,
        )
    if latency_by_stage is not None:
        latency_bucket = latency_by_stage.setdefault(
            stage,
            {"latency_ms_sum": 0.0, "records": 0, "latency_ms_avg": 0.0},
        )
        latency_bucket["latency_ms_sum"] = round(
            float(latency_bucket["latency_ms_sum"]) + float(usage.get("latency_ms") or 0.0),
            3,
        )
        latency_bucket["records"] += 1
        latency_bucket["latency_ms_avg"] = round(
            float(latency_bucket["latency_ms_sum"]) / int(latency_bucket["records"]),
            3,
        )


_THEOREM_NAME_RE = re.compile(r"(?m)^\s*(?:theorem|lemma)\s+([A-Za-z0-9_']+)")


def _extract_theorem_name(theorem_stub: str) -> str | None:
    match = _THEOREM_NAME_RE.search(theorem_stub)
    return match.group(1) if match else None


def _try_claim_trivial_shortcut(theorem_stub: str | None) -> dict[str, Any] | None:
    if not theorem_stub:
        return None
    theorem_name = _extract_theorem_name(theorem_stub)
    hypothesis = direct_hypothesis_name(theorem_stub)
    if not theorem_name or not hypothesis:
        return None
    tactic = f"exact {hypothesis}"
    try:
        candidate_code = _replace_named_theorem_body(theorem_stub, theorem_name, tactic)
    except ValueError:
        return None
    try:
        result = compile_check(candidate_code, timeout=60)
    except Exception:
        return None
    if not result.get("success"):
        return None
    return {
        "theorem_name": theorem_name,
        "hypothesis": hypothesis,
        "tactic": tactic,
        "verified_code": candidate_code,
    }


def _accumulate_failure(error_code: str | None, failure_counts: dict[str, int]) -> None:
    if not error_code:
        return
    failure_counts[error_code] = failure_counts.get(error_code, 0) + 1


def _classify_gate_claim(
    *,
    claim_set: str,
    raw_claim: str,
    claim_bucket: str,
    preamble_names: list[str],
    theorem_stub: str | None,
) -> ScopeClassification:
    claim_type = claim_bucket if claim_bucket in {"preamble_definable", "mathlib_native"} else None
    scope = classify_claim_scope(
        raw_claim=raw_claim,
        claim_type=claim_type,
        selected_preamble_entries=preamble_names,
        theorem_stub_present=bool(theorem_stub and theorem_stub.strip()),
    )
    if claim_set in FRONTIER_BENCHMARK_CLAIM_SETS and scope.scope == RELEASE_RELIABLE:
        return ScopeClassification(
            scope=SUPPORTED_ATTEMPT if claim_type == "preamble_definable" else FRONTIER_COLLECT,
            claim_type=scope.claim_type,
            selected_preamble_entries=scope.selected_preamble_entries,
            required_primitives=scope.required_primitives,
            theorem_shape_recommendation=scope.theorem_shape_recommendation,
            assumption_audit=scope.assumption_audit,
            reason="Claim belongs to a frontier benchmark set and is excluded from release-reliable metrics.",
        )
    return scope


def _parse_success_from_result(result: dict[str, Any]) -> bool | None:
    parse_check = result.get("parse_check")
    if isinstance(parse_check, dict) and "success" in parse_check:
        return bool(parse_check.get("success"))
    return None


def _frontier_records_from_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for result in results:
        scope = str(result.get("claim_scope") or "")
        if result.get("status") == "verified" and scope not in {FRONTIER_COLLECT, OUT_OF_SCOPE}:
            continue
        failure = {
            "failure_class": result.get("failure_class"),
            "next_action": result.get("recommended_next_action"),
            "reason": result.get("failure_reason"),
        }
        records.append(
            build_frontier_record(
                raw_claim=str(result.get("raw_claim") or ""),
                claim_id=str(result.get("id") or ""),
                scope={
                    "scope": scope,
                    "claim_type": result.get("claim_type"),
                    "selected_preamble_entries": result.get("selected_preamble") or [],
                    "required_primitives": result.get("required_primitives") or [],
                    "theorem_shape_recommendation": result.get("theorem_shape_recommendation"),
                    "assumption_audit": result.get("assumption_audit") or [],
                    "reason": result.get("scope_reason"),
                },
                claim_type=result.get("claim_type"),
                status=str(result.get("status") or ""),
                lean_statement=result.get("theorem_stub_reference") or result.get("lean_statement"),
                parse_success=_parse_success_from_result(result),
                proof_result=str(result.get("termination_reason") or result.get("status") or ""),
                failure=failure,
                budget_profile=result.get("budget_profile"),
                failure_code=result.get("failure_code"),
                termination_reason=result.get("termination_reason"),
                timing_breakdown=result.get("timing_breakdown") or {},
                usage_by_stage=result.get("usage_by_stage") or {},
                tool_budget=result.get("tool_budget") or {},
                budget_exhaustion=result.get("budget_exhaustion"),
                synthesis_event_count=len(result.get("synthesis_events") or []),
                candidate_attempt_count=int(
                    result.get("candidate_attempt_count")
                    or _candidate_attempt_count([result])
                    or 0
                ),
                retrieval_event_count=len(result.get("retrieval_events") or []),
            )
        )
    return records


def _write_frontier_queue(name: str, records: list[dict[str, Any]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.frontier_queue.jsonl"
    lines = [json.dumps(record, sort_keys=True) for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def _trace_events_from_result(prove_result: ProverResult) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for attr, event_type in (
        ("retrieval_events", "RetrievalEvent"),
        ("tool_usage_traces", "ToolUsageTrace"),
        ("state_transitions", "StateTransition"),
        ("prover_state_transitions", "ProverStateTransition"),
        ("progress_deltas", "ProgressDelta"),
        ("synthesis_events", "SynthesisEvent"),
    ):
        for payload in getattr(prove_result, attr, []) or []:
            if isinstance(payload, dict):
                events.append({"event_type": event_type, "payload": payload})
    for step in prove_result.trace:
        for key in (
            "RetrievalEvent",
            "ToolUsageTrace",
            "StateTransition",
            "ProgressDelta",
            "ProverStateTransition",
            "SynthesisEvent",
            "PremiseResolutionEvent",
            "CandidateTacticEvent",
        ):
            payload = step.tool_arguments.get(key)
            if isinstance(payload, dict):
                events.append(
                    {
                        "event_type": key,
                        "turn": step.turn,
                        "target_name": step.target_name,
                        "tool_name": step.tool_name,
                        "payload": payload,
                    }
                )
    for event in (prove_result.audit_summary or {}).get("events", []):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        if event_type in {
            "RetrievalEvent",
            "ToolUsageTrace",
            "StateTransition",
            "ProgressDelta",
            "ProverStateTransition",
            "SynthesisEvent",
            "PremiseResolutionEvent",
            "CandidateTacticEvent",
        }:
            events.append(
                {
                    "event_type": event_type,
                    "turn": (event.get("metadata") or {}).get("turn"),
                    "target_name": (event.get("metadata") or {}).get("target_name"),
                    "payload": event.get("metadata") or {},
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        key = json.dumps(event, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _synthesis_event_payloads(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "SynthesisEvent" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            payload = metadata.get("SynthesisEvent")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        key = json.dumps(payload, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(payload)
    return deduped


def _progress_delta_payloads(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "ProgressDelta" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            payload = metadata.get("ProgressDelta")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    return payloads


def _prover_state_transition_payloads(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in results:
        for payload in result.get("prover_state_transitions", []):
            if isinstance(payload, dict):
                payloads.append(dict(payload))
        for event in result.get("trace_events", []):
            if event.get("event_type") == "ProverStateTransition" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            payload = metadata.get("ProverStateTransition")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        key = json.dumps(payload, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(payload)
    return deduped


def _retrieval_hit_rate_at_5(results: list[dict[str, Any]]) -> float:
    retrieval_events: list[dict[str, Any]] = []
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "RetrievalEvent" and isinstance(
                event.get("payload"), dict
            ):
                retrieval_events.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            payload = metadata.get("RetrievalEvent")
            if isinstance(payload, dict):
                retrieval_events.append(dict(payload))
    if not retrieval_events:
        return 0.0
    hits = sum(
        1
        for event in retrieval_events
        if bool(event.get("hit")) or int(event.get("retrieved_count") or 0) > 0
    )
    return round(hits / len(retrieval_events), 6)


def _avg_tool_calls_mathlib(results: list[dict[str, Any]]) -> float:
    mathlib_results = [
        result for result in results if result.get("benchmark_bucket") == "mathlib_native"
    ]
    if not mathlib_results:
        return 0.0
    return round(
        sum(int(result.get("tool_calls") or 0) for result in mathlib_results)
        / len(mathlib_results),
        3,
    )


def _synthesis_efficiency(results: list[dict[str, Any]]) -> float:
    events = _synthesis_event_payloads(results)
    if not events:
        return 0.0
    matched = sum(1 for event in events if event.get("referenced_premises"))
    return round(matched / len(events), 6)


def _premise_match_rate_at_3(results: list[dict[str, Any]]) -> float:
    events = _synthesis_event_payloads(results)
    if not events:
        return 0.0
    matched = sum(1 for event in events if bool(event.get("top3_match")))
    return round(matched / len(events), 6)


def _synthesis_event_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    events = _synthesis_event_payloads(results)
    return {
        "synthesis_event_count": len(events),
        "premise_matched_synthesis_event_count": sum(
            1 for event in events if event.get("referenced_premises")
        ),
        "premise_top3_synthesis_event_count": sum(
            1 for event in events if bool(event.get("top3_match"))
        ),
    }


def _synthesis_candidate_used_count(results: list[dict[str, Any]]) -> int:
    return sum(int(result.get("synthesis_candidate_used_count") or 0) for result in results)


def _premise_resolution_event_payloads(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "PremiseResolutionEvent" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            payload = metadata.get("PremiseResolutionEvent")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    return _dedupe_payloads(payloads)


def _candidate_tactic_event_payloads(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for result in results:
        for event in result.get("trace_events", []):
            if event.get("event_type") == "CandidateTacticEvent" and isinstance(
                event.get("payload"), dict
            ):
                payloads.append(dict(event["payload"]))
        for event in result.get("progress_events", []):
            metadata = event.get("metadata") or {}
            payload = metadata.get("CandidateTacticEvent")
            if isinstance(payload, dict):
                payloads.append(dict(payload))
    return _dedupe_payloads(payloads)


def _dedupe_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for payload in payloads:
        key = json.dumps(payload, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(payload)
    return deduped


def _resolved_premise_rate(results: list[dict[str, Any]]) -> float:
    events = _premise_resolution_event_payloads(results)
    if not events:
        return 0.0
    resolved = sum(1 for event in events if bool(event.get("resolved")))
    return round(resolved / len(events), 6)


def _candidate_attempt_count(results: list[dict[str, Any]]) -> int:
    return len(_candidate_tactic_event_payloads(results))


def _candidate_success_rate(results: list[dict[str, Any]]) -> float:
    events = _candidate_tactic_event_payloads(results)
    if not events:
        return 0.0
    successes = sum(1 for event in events if bool(event.get("success")))
    return round(successes / len(events), 6)


def _provider_fallback_rate(results: list[dict[str, Any]]) -> float:
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


def _repl_compile_disagreement_count(results: list[dict[str, Any]]) -> int:
    return sum(
        1
        for result in results
        if result.get("failure_code") == "repl_compile_disagreement"
        or any(
            event.get("event") == "repl_compile_disagreement"
            for event in result.get("progress_events", [])
        )
    )


def _avg_decomposition_depth_mathlib(results: list[dict[str, Any]]) -> float:
    mathlib_results = [
        result for result in results if result.get("benchmark_bucket") == "mathlib_native"
    ]
    if not mathlib_results:
        return 0.0
    return round(
        sum(int(result.get("decomposition_depth") or 0) for result in mathlib_results)
        / len(mathlib_results),
        3,
    )


def _preflight(
    planner_service: PlannerService,
    formalizer_service: FormalizerService,
    prover_instance: Prover,
    *,
    budget_profile: BudgetProfile,
    claim_set: str,
    claim_set_manifest: dict[str, Any],
) -> dict[str, Any]:
    planner_backend = planner_service.backend
    formalizer_backend = formalizer_service.backend
    prover_backend = prover_instance.primary_backend
    planner_provider = planner_backend.provider
    planner_platform = (
        "ollama"
        if planner_backend.name == "ollama-cloud"
        else "mistral"
        if planner_backend.name == "mistral-structured"
        else "huggingface"
    )
    prover_provider = (
        PROVER_PROVIDER if prover_backend.provider == "huggingface" else prover_backend.provider
    )
    lean_preflight = _lean_workspace_preflight()
    planner_endpoint_reachable, planner_endpoint_message = planner_service.connectivity_check()
    checks = {
        "planner_provider_configured": (
            bool(planner_provider.strip())
            if planner_platform in {"ollama", "mistral"}
            else normalize_huggingface_provider(planner_provider)
            in {"auto", planner_provider.strip()}
        ),
        "planner_endpoint_reachable": planner_endpoint_reachable,
        "prover_provider_configured": prover_backend.provider != "huggingface"
        or normalize_huggingface_provider(prover_provider) in {"auto", prover_provider.strip()},
        "lean_workspace_available": bool(lean_preflight.get("available")),
        "planner_price_known": True,
        "formalizer_price_known": True,
        "prover_price_known": True,
    }
    if BENCHMARK_REQUIRE_PRICING:
        checks["planner_price_known"] = (
            lookup_pricing(planner_platform, planner_backend.model) is not None
        )
        checks["formalizer_price_known"] = (
            lookup_pricing(formalizer_backend.provider, formalizer_backend.model) is not None
        )
        checks["prover_price_known"] = (
            lookup_pricing(
                "huggingface" if prover_backend.provider == "huggingface" else prover_provider,
                prover_backend.model,
            )
            is not None
        )
    provider_guardrail = evaluate_provider_guardrail(
        budget_profile,
        _provider_stage_config(planner_service, formalizer_service, prover_instance),
    )
    profile_blockers = _profile_claim_set_blockers(
        profile=budget_profile,
        claim_set=claim_set,
        claim_set_manifest=claim_set_manifest,
    )
    checks["provider_guardrail_release_compliant"] = bool(
        provider_guardrail.get("release_compliant")
    )
    for blocker in profile_blockers:
        checks[blocker] = False
    ready = all(checks.values())
    blockers = [name for name, status in checks.items() if not status]
    details = {"lean_workspace": lean_preflight}
    if planner_endpoint_message:
        details["planner_endpoint_reachable"] = planner_endpoint_message
    return {
        "ready": ready,
        "checks": checks,
        "blockers": blockers,
        "details": details,
        "budget_profile": budget_profile.public_dict(),
        "provider_guardrail": provider_guardrail,
        "capabilities": {
            "planner": get_backend_capability("planner", planner_backend.name),
            "formalizer": get_backend_capability("formalizer", formalizer_backend.name),
            "prover": get_backend_capability("prover", prover_backend.name),
        },
    }


def _select_claims(
    claims: list[dict[str, Any]],
    *,
    claim_set: str,
    limit: int | None,
    stratified: bool,
    sample_seed: int | None = None,
    focused_sample: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if focused_sample:
        focused_ids = FOCUSED_SAMPLE_IDS_BY_SET.get(claim_set, ())
        by_id = {str(claim["id"]): claim for claim in claims}
        selected = [by_id[claim_id] for claim_id in focused_ids if claim_id in by_id]
        return selected, {
            "sampling_mode": "focused_sample",
            "sample_seed": sample_seed,
            "selected_ids": [str(claim["id"]) for claim in selected],
        }
    if limit is None or limit >= len(claims):
        return claims, {
            "sampling_mode": "full",
            "sample_seed": sample_seed,
            "selected_ids": [str(claim["id"]) for claim in claims],
        }
    if sample_seed is not None:
        selected = random.Random(sample_seed).sample(claims, limit)
        return selected, {
            "sampling_mode": "seeded_random",
            "sample_seed": sample_seed,
            "selected_ids": [str(claim["id"]) for claim in selected],
        }
    if not stratified:
        selected = claims[:limit]
        return selected, {
            "sampling_mode": "head",
            "sample_seed": sample_seed,
            "selected_ids": [str(claim["id"]) for claim in selected],
        }
    buckets: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        preambles = claim.get("preamble_names") or []
        key = str(preambles[0]) if preambles else ""
        buckets.setdefault(key, []).append(claim)
    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        progressed = False
        for key in sorted(buckets):
            bucket = buckets[key]
            if not bucket:
                continue
            selected.append(bucket.pop(0))
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected, {
        "sampling_mode": "stratified",
        "sample_seed": sample_seed,
        "selected_ids": [str(claim["id"]) for claim in selected],
    }


async def _run_claim_set_async(
    claim_set: str,
    *,
    planner_service: PlannerService,
    formalizer_service: FormalizerService,
    prover_instance: Prover,
    budget_profile: BudgetProfile,
    enforce_readiness: bool,
    benchmark_mode: bool,
    limit: int | None,
    stratified: bool,
    sample_seed: int | None,
    focused_sample: bool,
    reporter: _TerminalReporter | None,
    progress_sink: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    claims, selection_info = _select_claims(
        load_claims(claim_set),
        claim_set=claim_set,
        limit=limit,
        stratified=stratified,
        sample_seed=sample_seed,
        focused_sample=focused_sample,
    )
    claim_set_manifest = build_claim_set_manifest(claim_set)
    target_timeouts = _target_timeouts_for_profile(
        benchmark_mode=benchmark_mode,
        profile=budget_profile,
    )
    readiness = _preflight(
        planner_service,
        formalizer_service,
        prover_instance,
        budget_profile=budget_profile,
        claim_set=claim_set,
        claim_set_manifest=claim_set_manifest,
    )
    if reporter is not None:
        reporter.claim_set_started(
            claim_set,
            claims_total=len(claims),
            benchmark_mode=benchmark_mode,
            budget_profile=budget_profile,
            selection_info=selection_info,
            readiness=readiness,
        )
    hard_profile_blockers = [
        blocker
        for blocker in readiness.get("blockers", [])
        if str(blocker).startswith("budget_profile_disallows_")
    ]
    if hard_profile_blockers or (enforce_readiness and not readiness["ready"]):
        if reporter is not None:
            reporter.skipped_claim_set(
                claim_set, [str(blocker) for blocker in readiness["blockers"]]
            )
        return {
            "claim_set": claim_set,
            "artifact_schema_version": FRONTIER_RECORD_SCHEMA_VERSION,
            "mode": "benchmark_pipeline" if benchmark_mode else "live_pipeline",
            "benchmark_mode": benchmark_mode,
            "budget_profile": budget_profile.name,
            "budget_caps": budget_profile.public_dict(),
            "release_metrics_eligible": False,
            "target_timeouts": target_timeouts.model_dump(mode="json"),
            "generated_at": _timestamp(),
            "claim_set_manifest": claim_set_manifest,
            "claims_total": len(claims),
            "claims_passed": 0,
            "claims_failed": len(claims),
            "pass_at_1": 0.0,
            "executed": False,
            **selection_info,
            "readiness": readiness,
            "tokens_by_stage": {},
            "cost_by_stage": {},
            "cost_by_model": {},
            "cost_by_claim_type": {},
            "cost_by_claim_scope": {},
            "token_usage_sources": {},
            "latency_by_stage": {},
            "budget_exhaustion": {"total": 0, "by_reason": {}, "by_profile": {}},
            "failure_counts": {blocker: 1 for blocker in readiness["blockers"]},
            "results": [],
        }

    tokens_by_stage: dict[str, dict[str, int]] = {}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, dict[str, Any]] = {}
    cost_by_claim_type: dict[str, dict[str, Any]] = {}
    cost_by_claim_scope: dict[str, dict[str, Any]] = {}
    token_usage_sources: dict[str, dict[str, Any]] = {}
    latency_by_stage: dict[str, dict[str, Any]] = {}
    failure_counts: dict[str, int] = {}
    results: list[dict[str, Any]] = []

    for claim in claims:
        claim_id = str(claim["id"])
        raw_claim = str(claim["raw_claim"])
        claim_bucket = classify_claim(claim)
        claim_index = len(results) + 1
        theorem_stub = claim.get("theorem_stub")
        preamble_names_raw = claim.get("preamble_names") or []
        preamble_names = [str(name) for name in preamble_names_raw if str(name).strip()]
        scope = _classify_gate_claim(
            claim_set=claim_set,
            raw_claim=raw_claim,
            claim_bucket=claim_bucket,
            preamble_names=preamble_names,
            theorem_stub=theorem_stub,
        )
        claim_type = scope.claim_type
        planner_usage: dict[str, Any] | None = None
        formalizer_usage: dict[str, Any] | None = None
        prover_usage: dict[str, Any] | None = None
        formalize_result: Any | None = None
        planner_schema_invalid = False
        raw_planner_response: str | None = None
        failure_code: str | None = None
        termination_reason: str | None = None
        stage_timings = {"planner_ms": 0.0, "formalizer_ms": 0.0, "prover_ms": 0.0, "total_ms": 0.0}
        result_status = "failed"
        theorem_name: str | None = None
        verified_via = "full_pipeline"
        tool_budget: dict[str, Any] = {}
        tool_calls = 0
        lsp_tool_calls = 0
        native_search_attempts = 0
        mathlib_native_mode_usage = 0
        decomposition_steps = 0
        decomposition_depth = 0
        synthesis_candidate_used_count = 0
        provider_fallback_count = 0
        trace_events: list[dict[str, Any]] = []
        progress_events: list[dict[str, Any]] = []
        retrieval_events: list[dict[str, Any]] = []
        tool_usage_traces: list[dict[str, Any]] = []
        state_transitions: list[dict[str, Any]] = []
        prover_state_transitions: list[dict[str, Any]] = []
        progress_deltas: list[dict[str, Any]] = []
        synthesis_events: list[dict[str, Any]] = []
        heartbeat = (
            _ClaimHeartbeatMonitor(
                claim_set=claim_set,
                claim_id=claim_id,
                claim_index=claim_index,
                claims_total=len(claims),
                reporter=reporter,
                progress_sink=progress_sink,
            )
            if reporter is not None or progress_sink is not None
            else None
        )

        def record_progress(event: dict[str, Any]) -> None:
            progress_events.append(event)
            if heartbeat is not None:
                heartbeat.update(event)
            if progress_sink is not None:
                progress_sink(event)

        if reporter is not None:
            reporter.claim_started(claim_set, claim_index, len(claims), claim_id, claim_bucket)
        if heartbeat is not None:
            heartbeat.start()

        try:
            shortcut = None if benchmark_mode else _try_claim_trivial_shortcut(theorem_stub)
            if shortcut is not None:
                theorem_name = shortcut["theorem_name"]
                result_status = "verified"
                termination_reason = "trivial_shortcut"
                verified_via = "trivial_shortcut"
                failure_code = None
                record_progress(
                    _progress_event(
                        "prover_tool",
                        claim_id=claim_id,
                        stage="prover",
                        status="completed",
                        message="Claim closed via trivial shortcut.",
                        metadata={"tool_name": "compile_check", "shortcut": shortcut["tactic"]},
                    )
                )
                _accumulate_failure(failure_code, failure_counts)
                results.append(
                    {
                        "id": claim_id,
                        "benchmark_bucket": claim_bucket,
                        "claim_scope": scope.scope,
                        "claim_type": claim_type,
                        "selected_preamble": list(scope.selected_preamble_entries),
                        "required_primitives": list(scope.required_primitives),
                        "theorem_shape_recommendation": scope.theorem_shape_recommendation,
                        "assumption_audit": list(scope.assumption_audit),
                        "scope_reason": scope.reason,
                        "failure_class": None,
                        "recommended_next_action": None,
                        "failure_reason": None,
                        "status": result_status,
                        "termination_reason": termination_reason,
                        "failure_code": failure_code,
                        "theorem_name": theorem_name,
                        "raw_claim": raw_claim,
                        "benchmark_mode": benchmark_mode,
                        "budget_profile": budget_profile.name,
                        "budget_caps": budget_profile.public_dict(),
                        "release_metrics_eligible": budget_profile.release_metrics_eligible,
                        "verified_via": "trivial_shortcut",
                        "target_timeouts": target_timeouts.model_dump(mode="json"),
                        "theorem_stub_reference": theorem_stub,
                        "timing_breakdown": stage_timings,
                        "tool_budget": {},
                        "budget_exhaustion": None,
                        "tool_calls": tool_calls,
                        "lsp_tool_calls": lsp_tool_calls,
                        "native_search_attempts": native_search_attempts,
                        "mathlib_native_mode_usage": mathlib_native_mode_usage,
                        "usage_by_stage": {},
                        "progress_events": progress_events,
                        "trace_events": trace_events,
                        "retrieval_events": retrieval_events,
                        "tool_usage_traces": tool_usage_traces,
                        "state_transitions": state_transitions,
                        "prover_state_transitions": prover_state_transitions,
                        "progress_deltas": progress_deltas,
                        "synthesis_events": synthesis_events,
                        "trivial_shortcut": {
                            "hypothesis": shortcut["hypothesis"],
                            "tactic": shortcut["tactic"],
                        },
                    }
                )
                if reporter is not None:
                    reporter.claim_finished(len(results), len(claims), results[-1])
                continue

            record_progress(
                _progress_event(
                    "planner_started",
                    claim_id=claim_id,
                    stage="planner",
                    status="running",
                    message="Planner started.",
                    metadata={"benchmark_mode": benchmark_mode},
                )
            )
            plan_result = planner_service.build_plan_with_telemetry(
                raw_claim,
                theorem_stub=theorem_stub,
                preamble_names=preamble_names,
                benchmark_mode=benchmark_mode,
            )
            planner_usage = _usage_dict(plan_result.usage)
            planner_schema_invalid, raw_planner_response = _planner_raw_response(plan_result)
            stage_timings["planner_ms"] = float(plan_result.usage.latency_ms or 0.0)
            record_progress(
                _progress_event(
                    "planner_completed",
                    claim_id=claim_id,
                    stage="planner",
                    status="completed",
                    message="Planner completed.",
                    metadata={"schema_invalid_repaired": planner_schema_invalid},
                )
            )

            record_progress(
                _progress_event(
                    "formalizer_started",
                    claim_id=claim_id,
                    stage="formalizer",
                    status="running",
                    message="Formalizer started.",
                    metadata={"benchmark_mode": benchmark_mode},
                )
            )
            formalize_result = formalizer_service.formalize_with_telemetry(
                raw_claim,
                planner_packet=plan_result.payload.model_dump(mode="json"),
                theorem_stub=theorem_stub,
                preamble_names=preamble_names,
                benchmark_mode=benchmark_mode,
            )
            formalizer_usage = _usage_dict(formalize_result.usage)
            stage_timings["formalizer_ms"] = float(formalize_result.usage.latency_ms or 0.0)
            record_progress(
                _progress_event(
                    "formalizer_completed",
                    claim_id=claim_id,
                    stage="formalizer",
                    status="completed",
                    message="Formalizer completed.",
                    metadata={"review_state": formalize_result.payload.review_state},
                )
            )

            record_progress(
                _progress_event(
                    "prover_started",
                    claim_id=claim_id,
                    stage="prover",
                    status="running",
                    message="Prover started.",
                    metadata={
                        "benchmark_mode": benchmark_mode,
                        "budget_profile": budget_profile.name,
                        "budget_caps": budget_profile.public_dict(),
                        "claim_scope": scope.scope,
                        "claim_type": claim_type,
                        "mathlib_native_mode": claim_type == "mathlib_native",
                    },
                )
            )
            prover_packet = formalize_result.payload.model_copy(
                update={
                    "claim_type": claim_type,
                    "claim_scope": scope.scope,
                    "required_primitives": list(scope.required_primitives),
                    "theorem_shape_recommendation": scope.theorem_shape_recommendation,
                    "assumption_audit": list(scope.assumption_audit),
                    "scope_reason": scope.reason,
                    "planner_plan_paragraph": plan_result.payload.plan_paragraph,
                    "planner_textbook_defaults": list(
                        plan_result.payload.textbook_defaults
                    ),
                    "planner_subgoals": list(plan_result.payload.subgoals),
                }
            )
            prove_result = await prover_instance.prove(
                prover_packet,
                f"local_gate_{_sanitize_job_id(claim_id)}",
                max_turns=budget_profile.max_prover_turns,
                timeout=budget_profile.max_timeout_seconds,
                target_timeouts=target_timeouts,
                allow_decomposition=True,
                benchmark_mode=benchmark_mode,
                budget_profile=budget_profile,
                on_progress=lambda event, payload: record_progress({**payload, "event": event}),
            )
            theorem_name = prove_result.theorem_name
            termination_reason = prove_result.termination_reason
            verified_via = prove_result.verified_via
            stage_timings["prover_ms"] = float(
                prove_result.timing_breakdown.get("prover_ms") or 0.0
            )
            stage_timings["total_ms"] = (
                stage_timings["planner_ms"]
                + stage_timings["formalizer_ms"]
                + stage_timings["prover_ms"]
            )
            prover_usage = _usage_dict(prove_result.usage_by_stage.get("prover"))
            if prove_result.failure is not None:
                failure_code = prove_result.failure.error_code or prove_result.failure.reason
            result_status = prove_result.status
            tool_budget = prove_result.tool_budget or {}
            tool_calls = int(tool_budget.get("total_tool_calls") or 0)
            lsp_tool_calls = int(tool_budget.get("lsp_tool_calls") or 0)
            native_search_attempts = int(tool_budget.get("native_search_attempts") or 0)
            mathlib_native_mode_usage = int(tool_budget.get("mathlib_native_mode_uses") or 0)
            decomposition_steps = sum(
                1 for step in prove_result.trace if step.action_type == "decompose"
            )
            decomposition_depth = max(
                (target.recursion_depth for target in prove_result.targets), default=0
            )
            synthesis_candidate_used_count = sum(
                1
                for step in prove_result.trace
                if bool((step.tool_arguments or {}).get("synthesis_candidate_used"))
            )
            provider_fallback_count = sum(
                1
                for step in prove_result.trace
                if step.action_type == "mathlib_native_harness_loop"
            )
            retrieval_events = list(prove_result.retrieval_events or [])
            tool_usage_traces = list(prove_result.tool_usage_traces or [])
            state_transitions = list(prove_result.state_transitions or [])
            prover_state_transitions = list(prove_result.prover_state_transitions or [])
            progress_deltas = list(prove_result.progress_deltas or [])
            synthesis_events = list(prove_result.synthesis_events or [])
            trace_events = _trace_events_from_result(prove_result) if benchmark_mode else []
            record_progress(
                _progress_event(
                    "prover_verified" if prove_result.status == "verified" else "prover_failed",
                    claim_id=claim_id,
                    stage="prover",
                    status=prove_result.status,
                    message=f"Prover finished with status `{prove_result.status}`.",
                    metadata={
                        "termination_reason": prove_result.termination_reason,
                        "budget_profile": budget_profile.name,
                        "tool_calls": tool_calls,
                        "lsp_tool_calls": lsp_tool_calls,
                        "native_search_attempts": native_search_attempts,
                        "mathlib_native_mode_usage": mathlib_native_mode_usage,
                    },
                )
            )
        except StageExecutionError as exc:
            usage = _usage_dict(exc.usage)
            if exc.stage == "planner":
                planner_usage = usage
                stage_timings["planner_ms"] = float((usage or {}).get("latency_ms") or 0.0)
                planner_schema_invalid = exc.error_code == "schema_invalid"
                raw_planner_response = _audit_raw_response(exc.audit_events)
            elif exc.stage == "formalizer":
                formalizer_usage = usage
                stage_timings["formalizer_ms"] = float((usage or {}).get("latency_ms") or 0.0)
            failure_code = exc.error_code
            termination_reason = exc.stage
            record_progress(
                _progress_event(
                    f"{exc.stage}_failed",
                    claim_id=claim_id,
                    stage=exc.stage,
                    status="failed",
                    message=exc.message,
                    metadata={"error_code": exc.error_code},
                )
            )
        except Exception as exc:
            failure_code = classify_exception(exc)
            termination_reason = "exception"
            record_progress(
                _progress_event(
                    "prover_failed",
                    claim_id=claim_id,
                    stage="prover",
                    status="failed",
                    message=str(exc),
                    metadata={"error_code": failure_code},
                )
            )
        finally:
            if heartbeat is not None:
                heartbeat.stop()

        stage_timings["total_ms"] = round(
            stage_timings["planner_ms"]
            + stage_timings["formalizer_ms"]
            + stage_timings["prover_ms"],
            3,
        )
        _accumulate_usage(
            planner_usage,
            tokens_by_stage=tokens_by_stage,
            cost_by_stage=cost_by_stage,
            cost_by_model=cost_by_model,
            cost_by_claim_type=cost_by_claim_type,
            cost_by_claim_scope=cost_by_claim_scope,
            token_usage_sources=token_usage_sources,
            latency_by_stage=latency_by_stage,
            claim_type=claim_type,
            claim_scope=scope.scope,
        )
        _accumulate_usage(
            formalizer_usage,
            tokens_by_stage=tokens_by_stage,
            cost_by_stage=cost_by_stage,
            cost_by_model=cost_by_model,
            cost_by_claim_type=cost_by_claim_type,
            cost_by_claim_scope=cost_by_claim_scope,
            token_usage_sources=token_usage_sources,
            latency_by_stage=latency_by_stage,
            claim_type=claim_type,
            claim_scope=scope.scope,
        )
        _accumulate_usage(
            prover_usage,
            tokens_by_stage=tokens_by_stage,
            cost_by_stage=cost_by_stage,
            cost_by_model=cost_by_model,
            cost_by_claim_type=cost_by_claim_type,
            cost_by_claim_scope=cost_by_claim_scope,
            token_usage_sources=token_usage_sources,
            latency_by_stage=latency_by_stage,
            claim_type=claim_type,
            claim_scope=scope.scope,
        )
        _accumulate_failure(failure_code, failure_counts)
        parse_check_payload = None
        formalization_source = None
        lean_statement = None
        if formalize_result is not None:
            payload = getattr(formalize_result, "payload", None)
            if payload is not None:
                parse_check_payload = payload.parse_check.model_dump(mode="json")
                formalization_source = payload.formalization_source
                lean_statement = payload.theorem_with_sorry
        failure = classify_failure(
            scope=scope.scope,
            claim_type=claim_type,
            status=result_status,
            failure_code=failure_code,
            termination_reason=termination_reason,
            selected_preamble_entries=scope.selected_preamble_entries,
            parse_success=(
                bool(parse_check_payload.get("success"))
                if isinstance(parse_check_payload, dict)
                else None
            ),
            synthesis_event_count=len(synthesis_events),
            candidate_attempt_count=synthesis_candidate_used_count,
            retrieval_event_count=len(retrieval_events),
        )
        budget_exhaustion = _budget_exhaustion_reason(
            failure_code=failure_code,
            termination_reason=termination_reason,
            profile=budget_profile,
            tool_budget=tool_budget,
            max_turns=budget_profile.max_prover_turns,
            timeout=budget_profile.max_timeout_seconds,
        )
        results.append(
            {
                "id": claim_id,
                "benchmark_bucket": claim_bucket,
                "claim_scope": scope.scope,
                "claim_type": claim_type,
                "selected_preamble": list(scope.selected_preamble_entries),
                "required_primitives": list(scope.required_primitives),
                "theorem_shape_recommendation": scope.theorem_shape_recommendation,
                "assumption_audit": list(scope.assumption_audit),
                "scope_reason": scope.reason,
                "failure_class": failure.failure_class,
                "recommended_next_action": failure.next_action,
                "failure_reason": failure.reason,
                "status": result_status,
                "termination_reason": termination_reason,
                "failure_code": failure_code,
                "theorem_name": theorem_name,
                "lean_statement": lean_statement,
                "parse_check": parse_check_payload,
                "formalization_source": formalization_source,
                "raw_claim": raw_claim,
                "benchmark_mode": benchmark_mode,
                "budget_profile": budget_profile.name,
                "budget_caps": budget_profile.public_dict(),
                "release_metrics_eligible": budget_profile.release_metrics_eligible,
                "verified_via": verified_via,
                "target_timeouts": target_timeouts.model_dump(mode="json"),
                "theorem_stub_reference": theorem_stub,
                "timing_breakdown": stage_timings,
                "tool_budget": tool_budget,
                "budget_exhaustion": budget_exhaustion,
                "tool_calls": tool_calls,
                "lsp_tool_calls": lsp_tool_calls,
                "native_search_attempts": native_search_attempts,
                "mathlib_native_mode_usage": mathlib_native_mode_usage,
                "synthesis_candidate_used_count": synthesis_candidate_used_count,
                "candidate_attempt_count": _candidate_attempt_count(
                    [{"trace_events": trace_events, "progress_events": progress_events}]
                ),
                "provider_fallback_count": provider_fallback_count,
                "decomposition_steps": decomposition_steps,
                "decomposition_depth": decomposition_depth,
                "usage_by_stage": {
                    key: value
                    for key, value in {
                        "planner": planner_usage,
                        "formalizer": formalizer_usage,
                        "prover": prover_usage,
                    }.items()
                    if value is not None
                },
                "progress_events": progress_events,
                "trace_events": trace_events,
                "retrieval_events": retrieval_events,
                "tool_usage_traces": tool_usage_traces,
                "state_transitions": state_transitions,
                "prover_state_transitions": prover_state_transitions,
                "progress_deltas": progress_deltas,
                "synthesis_events": synthesis_events,
                **(
                    {"raw_planner_response": raw_planner_response} if planner_schema_invalid else {}
                ),
            }
        )
        if reporter is not None:
            reporter.claim_finished(len(results), len(claims), results[-1])

    claims_passed = sum(1 for item in results if item["status"] == "verified")
    claims_total = len(results)
    average_tool_calls = (
        round(sum(int(item.get("tool_calls") or 0) for item in results) / claims_total, 3)
        if claims_total
        else 0.0
    )
    average_lsp_tool_calls = (
        round(sum(int(item.get("lsp_tool_calls") or 0) for item in results) / claims_total, 3)
        if claims_total
        else 0.0
    )
    average_native_search_attempts = (
        round(
            sum(int(item.get("native_search_attempts") or 0) for item in results) / claims_total, 3
        )
        if claims_total
        else 0.0
    )
    mathlib_native_mode_usage = sum(
        int(item.get("mathlib_native_mode_usage") or 0) for item in results
    )
    average_decomposition_steps = (
        round(
            sum(int(item.get("decomposition_steps") or 0) for item in results) / claims_total,
            3,
        )
        if claims_total
        else 0.0
    )
    average_decomposition_depth = (
        round(
            sum(int(item.get("decomposition_depth") or 0) for item in results) / claims_total,
            3,
        )
        if claims_total
        else 0.0
    )
    progress_deltas = _progress_delta_payloads(results)
    prover_state_transitions = _prover_state_transition_payloads(results)
    synthesis_events = _synthesis_event_payloads(results)
    synthesis_counts = _synthesis_event_counts(results)
    frontier_records = _frontier_records_from_results(results)
    scoped_metrics = metrics_by_scope(results)
    release_reliable_metrics = _release_metrics_for_profile(budget_profile, results)
    return {
        "claim_set": claim_set,
        "artifact_schema_version": FRONTIER_RECORD_SCHEMA_VERSION,
        "mode": "benchmark_pipeline" if benchmark_mode else "live_pipeline",
        "benchmark_mode": benchmark_mode,
        "budget_profile": budget_profile.name,
        "budget_caps": budget_profile.public_dict(),
        "release_metrics_eligible": budget_profile.release_metrics_eligible,
        "target_timeouts": target_timeouts.model_dump(mode="json"),
        "generated_at": _timestamp(),
        "claim_set_manifest": claim_set_manifest,
        **selection_info,
        "claims_total": claims_total,
        "claims_passed": claims_passed,
        "claims_failed": claims_total - claims_passed,
        "pass_at_1": round(claims_passed / claims_total, 6) if claims_total else 0.0,
        "claim_scope_counts": scope_counts(results),
        "metrics_by_scope": scoped_metrics,
        "release_reliable_metrics": release_reliable_metrics,
        "frontier_metrics": {
            scope_name: scoped_metrics[scope_name]
            for scope_name in (SUPPORTED_ATTEMPT, FRONTIER_COLLECT, OUT_OF_SCOPE)
        },
        "average_tool_calls": average_tool_calls,
        "average_lsp_tool_calls": average_lsp_tool_calls,
        "average_native_search_attempts": average_native_search_attempts,
        "mathlib_native_mode_usage": mathlib_native_mode_usage,
        "average_decomposition_steps": average_decomposition_steps,
        "average_decomposition_depth": average_decomposition_depth,
        "retrieval_hit_rate@5": _retrieval_hit_rate_at_5(results),
        "avg_tool_calls_mathlib": _avg_tool_calls_mathlib(results),
        "synthesis_efficiency": _synthesis_efficiency(results),
        "premise_match_rate@3": _premise_match_rate_at_3(results),
        **synthesis_counts,
        "synthesis_candidate_used_count": _synthesis_candidate_used_count(results),
        "resolved_premise_rate": _resolved_premise_rate(results),
        "candidate_attempt_count": _candidate_attempt_count(results),
        "candidate_success_rate": _candidate_success_rate(results),
        "provider_fallback_rate": _provider_fallback_rate(results),
        "repl_compile_disagreement_count": _repl_compile_disagreement_count(results),
        "avg_decomposition_depth_mathlib": _avg_decomposition_depth_mathlib(results),
        "progress_deltas": progress_deltas,
        "prover_state_transitions": prover_state_transitions,
        "synthesis_events": synthesis_events,
        "executed": True,
        "readiness": readiness,
        "tokens_by_stage": tokens_by_stage,
        "cost_by_stage": cost_by_stage,
        "cost_by_model": cost_by_model,
        "cost_by_claim_type": cost_by_claim_type,
        "cost_by_claim_scope": cost_by_claim_scope,
        "token_usage_sources": token_usage_sources,
        "latency_by_stage": latency_by_stage,
        "budget_exhaustion": _budget_exhaustion_summary(results),
        "failure_counts": failure_counts,
        "frontier_records": frontier_records,
        "results": results,
    }


def run_claim_set(
    claim_set: str,
    *,
    planner_service: PlannerService | None = None,
    formalizer_service: FormalizerService | None = None,
    prover_instance: Prover | None = None,
    budget_profile: str | BudgetProfile | None = None,
    enforce_readiness: bool = True,
    benchmark_mode: bool = False,
    limit: int | None = None,
    stratified: bool = False,
    sample_seed: int | None = None,
    focused_sample: bool = False,
    reporter: _TerminalReporter | None = None,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    profile = (
        budget_profile
        if isinstance(budget_profile, BudgetProfile)
        else resolve_budget_profile(budget_profile)
    )
    return asyncio.run(
        _run_claim_set_async(
            claim_set,
            planner_service=planner_service or PlannerService(),
            formalizer_service=formalizer_service or DEFAULT_FORMALIZER,
            prover_instance=prover_instance or DEFAULT_PROVER,
            budget_profile=profile,
            enforce_readiness=enforce_readiness,
            benchmark_mode=benchmark_mode,
            limit=limit,
            stratified=stratified,
            sample_seed=sample_seed,
            focused_sample=focused_sample,
            reporter=reporter,
            progress_sink=progress_sink,
        )
    )


def _combine_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    tokens_by_stage: dict[str, dict[str, int]] = {}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, dict[str, Any]] = {}
    cost_by_claim_type: dict[str, dict[str, Any]] = {}
    cost_by_claim_scope: dict[str, dict[str, Any]] = {}
    token_usage_sources: dict[str, dict[str, Any]] = {}
    latency_by_stage: dict[str, dict[str, Any]] = {}
    budget_exhaustion = {"total": 0, "by_reason": {}, "by_profile": {}}
    failure_counts: dict[str, int] = {}
    benchmark_category_mix: dict[str, int] = {}
    for summary in summaries:
        for stage, payload in summary.get("tokens_by_stage", {}).items():
            bucket = tokens_by_stage.setdefault(stage, {"input_tokens": 0, "output_tokens": 0})
            bucket["input_tokens"] += int(payload.get("input_tokens") or 0)
            bucket["output_tokens"] += int(payload.get("output_tokens") or 0)
        for stage, cost in summary.get("cost_by_stage", {}).items():
            cost_by_stage[stage] = round(cost_by_stage.get(stage, 0.0) + float(cost), 8)
        for model_key, payload in summary.get("cost_by_model", {}).items():
            bucket = cost_by_model.setdefault(
                model_key,
                {
                    "provider": payload.get("provider"),
                    "model": payload.get("model"),
                    "estimated_cost_usd": 0.0,
                },
            )
            bucket["estimated_cost_usd"] = round(
                float(bucket["estimated_cost_usd"])
                + float(payload.get("estimated_cost_usd") or 0.0),
                8,
            )
        for source, target in (
            ("cost_by_claim_type", cost_by_claim_type),
            ("cost_by_claim_scope", cost_by_claim_scope),
            ("token_usage_sources", token_usage_sources),
        ):
            for key, payload in summary.get(source, {}).items():
                bucket = target.setdefault(
                    key,
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "estimated_cost_usd": 0.0,
                        "records": 0,
                    },
                )
                bucket["input_tokens"] += int(payload.get("input_tokens") or 0)
                bucket["output_tokens"] += int(payload.get("output_tokens") or 0)
                bucket["estimated_cost_usd"] = round(
                    float(bucket["estimated_cost_usd"])
                    + float(payload.get("estimated_cost_usd") or 0.0),
                    8,
                )
                bucket["records"] += int(payload.get("records") or 0)
        for stage, payload in summary.get("latency_by_stage", {}).items():
            bucket = latency_by_stage.setdefault(
                stage,
                {"latency_ms_sum": 0.0, "records": 0, "latency_ms_avg": 0.0},
            )
            bucket["latency_ms_sum"] = round(
                float(bucket["latency_ms_sum"]) + float(payload.get("latency_ms_sum") or 0.0),
                3,
            )
            bucket["records"] += int(payload.get("records") or 0)
        for error_code, count in summary.get("failure_counts", {}).items():
            failure_counts[error_code] = failure_counts.get(error_code, 0) + int(count)
        summary_budget = summary.get("budget_exhaustion") or {}
        budget_exhaustion["total"] += int(summary_budget.get("total") or 0)
        for reason, count in (summary_budget.get("by_reason") or {}).items():
            budget_exhaustion["by_reason"][reason] = budget_exhaustion["by_reason"].get(reason, 0) + int(count)
        for profile_name, count in (summary_budget.get("by_profile") or {}).items():
            budget_exhaustion["by_profile"][profile_name] = budget_exhaustion["by_profile"].get(profile_name, 0) + int(count)
        manifest = summary.get("claim_set_manifest", {})
        for bucket, count in manifest.get("bucket_counts", {}).items():
            benchmark_category_mix[bucket] = benchmark_category_mix.get(bucket, 0) + int(count)
    claims_total = sum(int(summary.get("claims_total") or 0) for summary in summaries)
    claims_passed = sum(int(summary.get("claims_passed") or 0) for summary in summaries)
    benchmark_mode = any(bool(summary.get("benchmark_mode")) for summary in summaries)
    profile_names = sorted({str(summary.get("budget_profile") or "unknown") for summary in summaries})
    combined_profile_name = profile_names[0] if len(profile_names) == 1 else "mixed"
    first_profile = resolve_budget_profile(profile_names[0]) if len(profile_names) == 1 and profile_names[0] != "unknown" else None
    target_timeouts = (
        _target_timeouts_for_profile(benchmark_mode=benchmark_mode, profile=first_profile)
        if first_profile is not None
        else (BENCHMARK_TARGET_TIMEOUTS if benchmark_mode else LIVE_TARGET_TIMEOUTS)
    )
    all_results = [result for summary in summaries for result in summary.get("results", [])]
    combined_scope_metrics = metrics_by_scope(all_results)
    release_metrics_eligible = bool(first_profile and first_profile.release_metrics_eligible)
    average_tool_calls = (
        round(sum(int(item.get("tool_calls") or 0) for item in all_results) / len(all_results), 3)
        if all_results
        else 0.0
    )
    average_lsp_tool_calls = (
        round(
            sum(int(item.get("lsp_tool_calls") or 0) for item in all_results) / len(all_results), 3
        )
        if all_results
        else 0.0
    )
    average_native_search_attempts = (
        round(
            sum(int(item.get("native_search_attempts") or 0) for item in all_results)
            / len(all_results),
            3,
        )
        if all_results
        else 0.0
    )
    mathlib_native_mode_usage = sum(
        int(item.get("mathlib_native_mode_usage") or 0) for item in all_results
    )
    progress_deltas = _progress_delta_payloads(all_results)
    prover_state_transitions = _prover_state_transition_payloads(all_results)
    synthesis_events = _synthesis_event_payloads(all_results)
    synthesis_counts = _synthesis_event_counts(all_results)
    for bucket in latency_by_stage.values():
        records = int(bucket.get("records") or 0)
        bucket["latency_ms_avg"] = round(float(bucket["latency_ms_sum"]) / records, 3) if records else 0.0
    return {
        "claim_set": "local_gate",
        "artifact_schema_version": FRONTIER_RECORD_SCHEMA_VERSION,
        "mode": "benchmark_pipeline" if benchmark_mode else "live_pipeline",
        "benchmark_mode": benchmark_mode,
        "budget_profile": combined_profile_name,
        "budget_profiles": profile_names,
        "budget_caps": first_profile.public_dict() if first_profile is not None else {},
        "release_metrics_eligible": release_metrics_eligible,
        "target_timeouts": target_timeouts.model_dump(mode="json"),
        "generated_at": _timestamp(),
        "claims_total": claims_total,
        "claims_passed": claims_passed,
        "claims_failed": claims_total - claims_passed,
        "pass_at_1": round(claims_passed / claims_total, 6) if claims_total else 0.0,
        "claim_scope_counts": scope_counts(all_results),
        "metrics_by_scope": combined_scope_metrics,
        "release_reliable_metrics": (
            combined_scope_metrics[RELEASE_RELIABLE]
            if release_metrics_eligible
            else {"claims_total": 0, "claims_passed": 0, "claims_failed": 0, "pass_at_1": 0.0}
        ),
        "frontier_metrics": {
            scope_name: combined_scope_metrics[scope_name]
            for scope_name in (SUPPORTED_ATTEMPT, FRONTIER_COLLECT, OUT_OF_SCOPE)
        },
        "average_tool_calls": average_tool_calls,
        "average_lsp_tool_calls": average_lsp_tool_calls,
        "average_native_search_attempts": average_native_search_attempts,
        "mathlib_native_mode_usage": mathlib_native_mode_usage,
        "retrieval_hit_rate@5": _retrieval_hit_rate_at_5(all_results),
        "avg_tool_calls_mathlib": _avg_tool_calls_mathlib(all_results),
        "synthesis_efficiency": _synthesis_efficiency(all_results),
        "premise_match_rate@3": _premise_match_rate_at_3(all_results),
        **synthesis_counts,
        "synthesis_candidate_used_count": _synthesis_candidate_used_count(all_results),
        "resolved_premise_rate": _resolved_premise_rate(all_results),
        "candidate_attempt_count": _candidate_attempt_count(all_results),
        "candidate_success_rate": _candidate_success_rate(all_results),
        "provider_fallback_rate": _provider_fallback_rate(all_results),
        "repl_compile_disagreement_count": _repl_compile_disagreement_count(all_results),
        "avg_decomposition_depth_mathlib": _avg_decomposition_depth_mathlib(all_results),
        "progress_deltas": progress_deltas,
        "prover_state_transitions": prover_state_transitions,
        "synthesis_events": synthesis_events,
        "readiness": {
            "ready": all(bool(summary.get("readiness", {}).get("ready")) for summary in summaries),
            "claim_sets": {
                summary["claim_set"]: summary.get("readiness", {}) for summary in summaries
            },
        },
        "tokens_by_stage": tokens_by_stage,
        "cost_by_stage": cost_by_stage,
        "cost_by_model": cost_by_model,
        "cost_by_claim_type": cost_by_claim_type,
        "cost_by_claim_scope": cost_by_claim_scope,
        "token_usage_sources": token_usage_sources,
        "latency_by_stage": latency_by_stage,
        "budget_exhaustion": budget_exhaustion,
        "failure_counts": failure_counts,
        "benchmark_category_mix": benchmark_category_mix,
        "frontier_records": _frontier_records_from_results(all_results),
        "claim_sets": summaries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-set", action="append")
    parser.add_argument("--claim-sets", type=str, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--allow-unready", action="store_true")
    parser.add_argument("--benchmark-mode", action="store_true")
    parser.add_argument(
        "--budget-profile",
        choices=("release", "frontier", "research"),
        default=None,
        help="Budget profile to enforce; defaults to LEANECON_BUDGET_PROFILE or release.",
    )
    parser.add_argument("--save-history", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--stratified", action="store_true")
    parser.add_argument(
        "--sample-seed", "--seed", dest="sample_seed", type=int, default=DEFAULT_SAMPLE_SEED
    )
    parser.add_argument("--focused-sample", action="store_true")
    args = parser.parse_args(argv)

    selected_from_csv = tuple(
        item.strip() for item in (args.claim_sets or "").split(",") if item.strip()
    )
    selected = tuple(args.claim_set or selected_from_csv or CLAIM_SETS)
    if args.output_dir is not None:
        output_dir = args.output_dir
    elif args.limit == 0:
        output_dir = Path(tempfile.gettempdir()) / "leanecon-local-gate-scaffold"
    else:
        output_dir = BENCHMARK_BASELINE_DIR / (
            "benchmark_mode" if args.benchmark_mode else "live_pipeline"
        )
    reporter = _TerminalReporter()
    selected_profile = resolve_budget_profile(args.budget_profile)
    summaries: list[dict[str, Any]] = []
    for claim_set in selected:
        reset_progress_log(claim_set, output_dir)
        summaries.append(
            run_claim_set(
                claim_set,
                budget_profile=selected_profile,
                enforce_readiness=not args.allow_unready,
                benchmark_mode=args.benchmark_mode,
                limit=args.limit,
                stratified=args.stratified,
                sample_seed=args.sample_seed
                if args.limit is not None or args.focused_sample
                else None,
                focused_sample=args.focused_sample,
                reporter=reporter,
                progress_sink=lambda event, claim_set=claim_set: append_progress_event(
                    claim_set, event, output_dir
                ),
            )
        )
    for summary in summaries:
        progress_events = [
            event
            for result in summary.get("results", [])
            for event in result.get("progress_events", [])
        ]
        progress_path = write_progress_log(summary["claim_set"], progress_events, output_dir)
        summary["progress_log_path"] = str(progress_path)
        frontier_path = _write_frontier_queue(
            str(summary["claim_set"]),
            list(summary.get("frontier_records") or []),
            output_dir,
        )
        summary["frontier_queue_path"] = str(frontier_path)
        path = write_summary(summary["claim_set"], summary, output_dir)
        reporter.claim_set_completed(summary, path)
    combined = _combine_summaries(summaries)
    combined_frontier_path = _write_frontier_queue(
        "local_gate",
        list(combined.get("frontier_records") or []),
        output_dir,
    )
    combined["frontier_queue_path"] = str(combined_frontier_path)
    combined_path = write_summary("local_gate", combined, output_dir)
    combined_progress_events = [
        event
        for summary in summaries
        for result in summary.get("results", [])
        for event in result.get("progress_events", [])
    ]
    write_progress_log("local_gate", combined_progress_events, output_dir)
    reporter.combined_completed(combined, combined_path)
    if args.save_history:
        history_path = benchmark_history_path(output_dir)
        row = append_history_row(combined, history_path=history_path)
        reporter.history_updated(str(row["row_id"]), history_path)
    if not args.allow_unready and not combined["readiness"]["ready"]:
        return 1
    return 0 if combined["claims_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
