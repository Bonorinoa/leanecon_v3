"""Live local-gate benchmark runner."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import os
from pathlib import Path
import random
import re
import shutil
import sys
import threading
import time
from typing import Any

from evals.benchmark_manifest import build_claim_set_manifest, classify_claim
from evals.common import (
    LOCAL_GATE_DEFAULT_CLAIM_SETS,
    append_progress_event,
    load_claims,
    reset_progress_log,
    write_progress_log,
    write_summary,
)
from src.backend_capabilities import get_backend_capability
from src.config import BENCHMARK_BASELINE_DIR, BENCHMARK_REQUIRE_PRICING, PROVER_PROVIDER
from src.evals.metrics_aggregator import append_history_row, benchmark_history_path
from src.formalizer import DEFAULT_FORMALIZER, FormalizerService
from src.lean import compile_check
from src.observability import StageExecutionError, build_progress_event, classify_exception, lookup_pricing
from src.planner import PlannerService
from src.providers import normalize_huggingface_provider
from src.prover import DEFAULT_PROVER, Prover, ProverTargetTimeouts
from src.prover.prover import _replace_named_theorem_body
from src.prover.tactics import direct_hypothesis_name

CLAIM_SETS = LOCAL_GATE_DEFAULT_CLAIM_SETS
LIVE_TARGET_TIMEOUTS = ProverTargetTimeouts(theorem_body=300, subgoal=180, apollo_lemma=120)
BENCHMARK_TARGET_TIMEOUTS = ProverTargetTimeouts(theorem_body=120, subgoal=120, apollo_lemma=120)
DEFAULT_SAMPLE_SEED = 17
HEARTBEAT_SECONDS = max(5.0, float(os.getenv("LEANECON_LOCAL_GATE_HEARTBEAT_SECONDS", "30")))


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
    header_row = "| " + " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers)) + " |"
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
    total = sum(float((result.get("timing_breakdown") or {}).get(stage) or 0.0) for result in results)
    return round(total / len(results), 3)


def _summary_total_cost(summary: dict[str, Any]) -> float:
    return round(sum(float(value or 0.0) for value in summary.get("cost_by_stage", {}).values()), 8)


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
        self._emit(f"{bar} {index:>2}/{total:<2} {status_label:<8} {claim_id:<30} {_format_duration_ms(total_ms):>7} {detail}")

    def claim_started(self, claim_set: str, index: int, total: int, claim_id: str, bucket: str) -> None:
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
            ["Pass@1", _format_ratio(int(summary.get("claims_passed") or 0), int(summary.get("claims_total") or 0))],
            ["Failures", str(int(summary.get("claims_failed") or 0))],
            ["Avg total latency", _format_duration_ms(_summary_average_latency_ms(summary, "total_ms"))],
            ["Total cost", _format_usd(_summary_total_cost(summary))],
            ["Output", str(output_path)],
        ]
        self._emit(_render_table(["Metric", "Value"], rows))

        latency_rows = [
            ["planner", _format_duration_ms(_summary_average_latency_ms(summary, "planner_ms"))],
            ["formalizer", _format_duration_ms(_summary_average_latency_ms(summary, "formalizer_ms"))],
            ["prover", _format_duration_ms(_summary_average_latency_ms(summary, "prover_ms"))],
            ["total", _format_duration_ms(_summary_average_latency_ms(summary, "total_ms"))],
        ]
        self._emit(_render_table(["Stage", "Avg latency"], latency_rows))

        failure_counts = summary.get("failure_counts", {})
        if failure_counts:
            failure_rows = [
                [str(code), str(count)]
                for code, count in sorted(failure_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
            ]
            self._emit(_render_table(["Failure code", "Count"], failure_rows))
        else:
            self._emit("Failures: none")

    def combined_completed(self, summary: dict[str, Any], output_path: Path) -> None:
        self.section("[local_gate] combined")
        rows = [
            [str(item["claim_set"]), _format_ratio(int(item.get("claims_passed") or 0), int(item.get("claims_total") or 0))]
            for item in summary.get("claim_sets", [])
        ]
        rows.append(
            [
                "local_gate",
                _format_ratio(int(summary.get("claims_passed") or 0), int(summary.get("claims_total") or 0)),
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
) -> None:
    if not usage:
        return
    stage = str(usage.get("stage") or "unknown")
    provider = str(usage.get("provider") or "unknown")
    model = str(usage.get("model") or "unknown")
    stage_bucket = tokens_by_stage.setdefault(stage, {"input_tokens": 0, "output_tokens": 0})
    stage_bucket["input_tokens"] += int(usage.get("input_tokens") or 0)
    stage_bucket["output_tokens"] += int(usage.get("output_tokens") or 0)
    cost_by_stage[stage] = round(cost_by_stage.get(stage, 0.0) + float(usage.get("estimated_cost_usd") or 0.0), 8)
    model_key = f"{provider}:{model}"
    model_bucket = cost_by_model.setdefault(
        model_key,
        {"provider": provider, "model": model, "estimated_cost_usd": 0.0},
    )
    model_bucket["estimated_cost_usd"] = round(
        float(model_bucket["estimated_cost_usd"]) + float(usage.get("estimated_cost_usd") or 0.0),
        8,
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


def _preflight(
    planner_service: PlannerService,
    formalizer_service: FormalizerService,
    prover_instance: Prover,
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
    prover_provider = PROVER_PROVIDER if prover_backend.provider == "huggingface" else prover_backend.provider
    planner_endpoint_reachable, planner_endpoint_message = planner_service.connectivity_check()
    checks = {
        "planner_provider_configured": (
            bool(planner_provider.strip())
            if planner_platform in {"ollama", "mistral"}
            else normalize_huggingface_provider(planner_provider) in {"auto", planner_provider.strip()}
        ),
        "planner_endpoint_reachable": planner_endpoint_reachable,
        "prover_provider_configured": prover_backend.provider != "huggingface"
        or normalize_huggingface_provider(prover_provider) in {"auto", prover_provider.strip()},
        "planner_price_known": True,
        "formalizer_price_known": True,
        "prover_price_known": True,
    }
    if BENCHMARK_REQUIRE_PRICING:
        checks["planner_price_known"] = lookup_pricing(planner_platform, planner_backend.model) is not None
        checks["formalizer_price_known"] = lookup_pricing(formalizer_backend.provider, formalizer_backend.model) is not None
        checks["prover_price_known"] = lookup_pricing(
            "huggingface" if prover_backend.provider == "huggingface" else prover_provider,
            prover_backend.model,
        ) is not None
    ready = all(checks.values())
    blockers = [name for name, status in checks.items() if not status]
    details = {"planner_endpoint_reachable": planner_endpoint_message} if planner_endpoint_message else {}
    return {
        "ready": ready,
        "checks": checks,
        "blockers": blockers,
        "details": details,
        "capabilities": {
            "planner": get_backend_capability("planner", planner_backend.name),
            "formalizer": get_backend_capability("formalizer", formalizer_backend.name),
            "prover": get_backend_capability("prover", prover_backend.name),
        },
    }


def _select_claims(
    claims: list[dict[str, Any]],
    *,
    limit: int | None,
    stratified: bool,
    sample_seed: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    enforce_readiness: bool,
    benchmark_mode: bool,
    limit: int | None,
    stratified: bool,
    sample_seed: int | None,
    reporter: _TerminalReporter | None,
    progress_sink: Callable[[dict[str, Any]], None] | None,
) -> dict[str, Any]:
    claims, selection_info = _select_claims(
        load_claims(claim_set),
        limit=limit,
        stratified=stratified,
        sample_seed=sample_seed,
    )
    claim_set_manifest = build_claim_set_manifest(claim_set)
    readiness = _preflight(planner_service, formalizer_service, prover_instance)
    if reporter is not None:
        reporter.claim_set_started(
            claim_set,
            claims_total=len(claims),
            benchmark_mode=benchmark_mode,
            selection_info=selection_info,
            readiness=readiness,
        )
    target_timeouts = BENCHMARK_TARGET_TIMEOUTS if benchmark_mode else LIVE_TARGET_TIMEOUTS
    if enforce_readiness and not readiness["ready"]:
        if reporter is not None:
            reporter.skipped_claim_set(claim_set, [str(blocker) for blocker in readiness["blockers"]])
        return {
            "claim_set": claim_set,
            "mode": "benchmark_pipeline" if benchmark_mode else "live_pipeline",
            "benchmark_mode": benchmark_mode,
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
            "failure_counts": {blocker: 1 for blocker in readiness["blockers"]},
            "results": [],
        }

    tokens_by_stage: dict[str, dict[str, int]] = {}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, dict[str, Any]] = {}
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
        planner_usage: dict[str, Any] | None = None
        formalizer_usage: dict[str, Any] | None = None
        prover_usage: dict[str, Any] | None = None
        planner_schema_invalid = False
        raw_planner_response: str | None = None
        failure_code: str | None = None
        termination_reason: str | None = None
        stage_timings = {"planner_ms": 0.0, "formalizer_ms": 0.0, "prover_ms": 0.0, "total_ms": 0.0}
        result_status = "failed"
        theorem_name: str | None = None
        verified_via = "full_pipeline"
        tool_calls = 0
        lsp_tool_calls = 0
        native_search_attempts = 0
        mathlib_native_mode_usage = 0
        decomposition_steps = 0
        decomposition_depth = 0
        progress_events: list[dict[str, Any]] = []
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
                        "status": result_status,
                        "termination_reason": termination_reason,
                        "failure_code": failure_code,
                        "theorem_name": theorem_name,
                        "raw_claim": raw_claim,
                        "benchmark_mode": benchmark_mode,
                        "verified_via": "trivial_shortcut",
                        "target_timeouts": target_timeouts.model_dump(mode="json"),
                        "theorem_stub_reference": theorem_stub,
                        "timing_breakdown": stage_timings,
                        "tool_calls": tool_calls,
                        "lsp_tool_calls": lsp_tool_calls,
                        "native_search_attempts": native_search_attempts,
                        "mathlib_native_mode_usage": mathlib_native_mode_usage,
                        "usage_by_stage": {},
                        "progress_events": progress_events,
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
                        "claim_type": claim_bucket if claim_bucket in {"preamble_definable", "mathlib_native"} else None,
                        "mathlib_native_mode": claim_bucket == "mathlib_native",
                    },
                )
            )
            prover_packet = formalize_result.payload.model_copy(
                update={
                    "claim_type": claim_bucket
                    if claim_bucket in {"preamble_definable", "mathlib_native"}
                    else None
                }
            )
            prove_result = await prover_instance.prove(
                prover_packet,
                f"local_gate_{_sanitize_job_id(claim_id)}",
                max_turns=8,
                timeout=120 if benchmark_mode else 300,
                target_timeouts=target_timeouts,
                allow_decomposition=True,
                benchmark_mode=benchmark_mode,
                on_progress=lambda event, payload: record_progress({**payload, "event": event}),
            )
            theorem_name = prove_result.theorem_name
            termination_reason = prove_result.termination_reason
            verified_via = prove_result.verified_via
            stage_timings["prover_ms"] = float(prove_result.timing_breakdown.get("prover_ms") or 0.0)
            stage_timings["total_ms"] = (
                stage_timings["planner_ms"] + stage_timings["formalizer_ms"] + stage_timings["prover_ms"]
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
            decomposition_steps = sum(1 for step in prove_result.trace if step.action_type == "decompose")
            decomposition_depth = max((target.recursion_depth for target in prove_result.targets), default=0)
            record_progress(
                _progress_event(
                    "prover_verified" if prove_result.status == "verified" else "prover_failed",
                    claim_id=claim_id,
                    stage="prover",
                    status=prove_result.status,
                    message=f"Prover finished with status `{prove_result.status}`.",
                    metadata={
                        "termination_reason": prove_result.termination_reason,
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
            stage_timings["planner_ms"] + stage_timings["formalizer_ms"] + stage_timings["prover_ms"],
            3,
        )
        _accumulate_usage(planner_usage, tokens_by_stage=tokens_by_stage, cost_by_stage=cost_by_stage, cost_by_model=cost_by_model)
        _accumulate_usage(
            formalizer_usage,
            tokens_by_stage=tokens_by_stage,
            cost_by_stage=cost_by_stage,
            cost_by_model=cost_by_model,
        )
        _accumulate_usage(prover_usage, tokens_by_stage=tokens_by_stage, cost_by_stage=cost_by_stage, cost_by_model=cost_by_model)
        _accumulate_failure(failure_code, failure_counts)
        results.append(
            {
                "id": claim_id,
                "benchmark_bucket": claim_bucket,
                "status": result_status,
                "termination_reason": termination_reason,
                "failure_code": failure_code,
                "theorem_name": theorem_name,
                "raw_claim": raw_claim,
                "benchmark_mode": benchmark_mode,
                "verified_via": verified_via,
                "target_timeouts": target_timeouts.model_dump(mode="json"),
                "theorem_stub_reference": theorem_stub,
                "timing_breakdown": stage_timings,
                "tool_calls": tool_calls,
                "lsp_tool_calls": lsp_tool_calls,
                "native_search_attempts": native_search_attempts,
                "mathlib_native_mode_usage": mathlib_native_mode_usage,
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
                **({"raw_planner_response": raw_planner_response} if planner_schema_invalid else {}),
            }
        )
        if reporter is not None:
            reporter.claim_finished(len(results), len(claims), results[-1])

    claims_passed = sum(1 for item in results if item["status"] == "verified")
    claims_total = len(results)
    average_tool_calls = round(sum(int(item.get("tool_calls") or 0) for item in results) / claims_total, 3) if claims_total else 0.0
    average_lsp_tool_calls = (
        round(sum(int(item.get("lsp_tool_calls") or 0) for item in results) / claims_total, 3)
        if claims_total
        else 0.0
    )
    average_native_search_attempts = (
        round(sum(int(item.get("native_search_attempts") or 0) for item in results) / claims_total, 3)
        if claims_total
        else 0.0
    )
    mathlib_native_mode_usage = sum(int(item.get("mathlib_native_mode_usage") or 0) for item in results)
    average_decomposition_steps = round(
        sum(int(item.get("decomposition_steps") or 0) for item in results) / claims_total,
        3,
    ) if claims_total else 0.0
    average_decomposition_depth = round(
        sum(int(item.get("decomposition_depth") or 0) for item in results) / claims_total,
        3,
    ) if claims_total else 0.0
    return {
        "claim_set": claim_set,
        "mode": "benchmark_pipeline" if benchmark_mode else "live_pipeline",
        "benchmark_mode": benchmark_mode,
        "target_timeouts": target_timeouts.model_dump(mode="json"),
        "generated_at": _timestamp(),
        "claim_set_manifest": claim_set_manifest,
        **selection_info,
        "claims_total": claims_total,
        "claims_passed": claims_passed,
        "claims_failed": claims_total - claims_passed,
        "pass_at_1": round(claims_passed / claims_total, 6) if claims_total else 0.0,
        "average_tool_calls": average_tool_calls,
        "average_lsp_tool_calls": average_lsp_tool_calls,
        "average_native_search_attempts": average_native_search_attempts,
        "mathlib_native_mode_usage": mathlib_native_mode_usage,
        "average_decomposition_steps": average_decomposition_steps,
        "average_decomposition_depth": average_decomposition_depth,
        "executed": True,
        "readiness": readiness,
        "tokens_by_stage": tokens_by_stage,
        "cost_by_stage": cost_by_stage,
        "cost_by_model": cost_by_model,
        "failure_counts": failure_counts,
        "results": results,
    }


def run_claim_set(
    claim_set: str,
    *,
    planner_service: PlannerService | None = None,
    formalizer_service: FormalizerService | None = None,
    prover_instance: Prover | None = None,
    enforce_readiness: bool = True,
    benchmark_mode: bool = False,
    limit: int | None = None,
    stratified: bool = False,
    sample_seed: int | None = None,
    reporter: _TerminalReporter | None = None,
    progress_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        _run_claim_set_async(
            claim_set,
            planner_service=planner_service or PlannerService(),
            formalizer_service=formalizer_service or DEFAULT_FORMALIZER,
            prover_instance=prover_instance or DEFAULT_PROVER,
            enforce_readiness=enforce_readiness,
            benchmark_mode=benchmark_mode,
            limit=limit,
            stratified=stratified,
            sample_seed=sample_seed,
            reporter=reporter,
            progress_sink=progress_sink,
        )
    )


def _combine_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    tokens_by_stage: dict[str, dict[str, int]] = {}
    cost_by_stage: dict[str, float] = {}
    cost_by_model: dict[str, dict[str, Any]] = {}
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
                float(bucket["estimated_cost_usd"]) + float(payload.get("estimated_cost_usd") or 0.0),
                8,
            )
        for error_code, count in summary.get("failure_counts", {}).items():
            failure_counts[error_code] = failure_counts.get(error_code, 0) + int(count)
        manifest = summary.get("claim_set_manifest", {})
        for bucket, count in manifest.get("bucket_counts", {}).items():
            benchmark_category_mix[bucket] = benchmark_category_mix.get(bucket, 0) + int(count)
    claims_total = sum(int(summary.get("claims_total") or 0) for summary in summaries)
    claims_passed = sum(int(summary.get("claims_passed") or 0) for summary in summaries)
    benchmark_mode = any(bool(summary.get("benchmark_mode")) for summary in summaries)
    target_timeouts = BENCHMARK_TARGET_TIMEOUTS if benchmark_mode else LIVE_TARGET_TIMEOUTS
    all_results = [result for summary in summaries for result in summary.get("results", [])]
    average_tool_calls = (
        round(sum(int(item.get("tool_calls") or 0) for item in all_results) / len(all_results), 3)
        if all_results
        else 0.0
    )
    average_lsp_tool_calls = (
        round(sum(int(item.get("lsp_tool_calls") or 0) for item in all_results) / len(all_results), 3)
        if all_results
        else 0.0
    )
    average_native_search_attempts = (
        round(sum(int(item.get("native_search_attempts") or 0) for item in all_results) / len(all_results), 3)
        if all_results
        else 0.0
    )
    mathlib_native_mode_usage = sum(int(item.get("mathlib_native_mode_usage") or 0) for item in all_results)
    return {
        "claim_set": "local_gate",
        "mode": "benchmark_pipeline" if benchmark_mode else "live_pipeline",
        "benchmark_mode": benchmark_mode,
        "target_timeouts": target_timeouts.model_dump(mode="json"),
        "generated_at": _timestamp(),
        "claims_total": claims_total,
        "claims_passed": claims_passed,
        "claims_failed": claims_total - claims_passed,
        "pass_at_1": round(claims_passed / claims_total, 6) if claims_total else 0.0,
        "average_tool_calls": average_tool_calls,
        "average_lsp_tool_calls": average_lsp_tool_calls,
        "average_native_search_attempts": average_native_search_attempts,
        "mathlib_native_mode_usage": mathlib_native_mode_usage,
        "readiness": {
            "ready": all(bool(summary.get("readiness", {}).get("ready")) for summary in summaries),
            "claim_sets": {summary["claim_set"]: summary.get("readiness", {}) for summary in summaries},
        },
        "tokens_by_stage": tokens_by_stage,
        "cost_by_stage": cost_by_stage,
        "cost_by_model": cost_by_model,
        "failure_counts": failure_counts,
        "benchmark_category_mix": benchmark_category_mix,
        "claim_sets": summaries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claim-set", action="append")
    parser.add_argument("--claim-sets", type=str, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--allow-unready", action="store_true")
    parser.add_argument("--benchmark-mode", action="store_true")
    parser.add_argument("--save-history", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--stratified", action="store_true")
    parser.add_argument("--sample-seed", type=int, default=DEFAULT_SAMPLE_SEED)
    args = parser.parse_args(argv)

    selected_from_csv = tuple(item.strip() for item in (args.claim_sets or "").split(",") if item.strip())
    selected = tuple(args.claim_set or selected_from_csv or CLAIM_SETS)
    output_dir = args.output_dir or (
        BENCHMARK_BASELINE_DIR / ("benchmark_mode" if args.benchmark_mode else "live_pipeline")
    )
    reporter = _TerminalReporter()
    summaries: list[dict[str, Any]] = []
    for claim_set in selected:
        reset_progress_log(claim_set, output_dir)
        summaries.append(
            run_claim_set(
                claim_set,
                enforce_readiness=not args.allow_unready,
                benchmark_mode=args.benchmark_mode,
                limit=args.limit,
                stratified=args.stratified,
                sample_seed=args.sample_seed if args.limit is not None else None,
                reporter=reporter,
                progress_sink=lambda event, claim_set=claim_set: append_progress_event(claim_set, event, output_dir),
            )
        )
    for summary in summaries:
        progress_events = [event for result in summary.get("results", []) for event in result.get("progress_events", [])]
        progress_path = write_progress_log(summary["claim_set"], progress_events, output_dir)
        summary["progress_log_path"] = str(progress_path)
        path = write_summary(summary["claim_set"], summary, output_dir)
        reporter.claim_set_completed(summary, path)
    combined = _combine_summaries(summaries)
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
    if not combined["readiness"]["ready"]:
        return 1
    return 0 if combined["claims_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
