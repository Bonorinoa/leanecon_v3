"""Telemetry and provenance helpers."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.preamble_library import PREAMBLE_LIBRARY


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_theorem_name(theorem_with_sorry: str) -> str:
    for line in theorem_with_sorry.splitlines():
        stripped = line.strip()
        if stripped.startswith(("theorem ", "lemma ")):
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]
    return "anonymous_theorem"


@dataclass
class SpanRecorder:
    """Track Lean, provider, and orchestration time for one verification job."""

    started_at: float = field(default_factory=time.perf_counter)
    lean_ms: float = 0.0
    provider_ms: float = 0.0

    def record_lean(self, started_at: float) -> None:
        self.lean_ms += max(0.0, (time.perf_counter() - started_at) * 1000.0)

    def record_provider(self, started_at: float, *, lean_ms_during_span: float = 0.0) -> None:
        elapsed_ms = max(0.0, (time.perf_counter() - started_at) * 1000.0)
        self.provider_ms += max(0.0, elapsed_ms - lean_ms_during_span)

    def snapshot(self) -> dict[str, float]:
        total_ms = max(0.0, (time.perf_counter() - self.started_at) * 1000.0)
        orchestration_ms = max(0.0, total_ms - self.lean_ms - self.provider_ms)
        return {
            "lean_ms": round(self.lean_ms, 3),
            "provider_ms": round(self.provider_ms, 3),
            "orchestration_ms": round(orchestration_ms, 3),
            "wall_clock_ms": round(total_ms, 3),
        }


def sync_classified_errors(
    verification_trace: dict[str, Any] | None,
    compile_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    payload = list(compile_result.get("classified_errors") or []) if compile_result else []
    if verification_trace is not None:
        verification_trace["last_classified_errors"] = payload
    return payload


_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.]+)\s*$")


def preamble_names_from_theorem(theorem_with_sorry: str) -> list[str]:
    module_names: list[str] = []
    seen_modules: set[str] = set()
    for line in theorem_with_sorry.splitlines():
        match = _IMPORT_RE.match(line)
        if match is None:
            continue
        module_name = match.group(1).strip()
        if not module_name or module_name in seen_modules:
            continue
        seen_modules.add(module_name)
        module_names.append(module_name)

    preamble_names: list[str] = []
    seen_preambles: set[str] = set()
    for module_name in module_names:
        for entry in PREAMBLE_LIBRARY.values():
            if entry.lean_module != module_name or entry.name in seen_preambles:
                continue
            preamble_names.append(entry.name)
            seen_preambles.add(entry.name)
            break
    return preamble_names


def tactic_sequence_from_attempts(attempts: list[dict[str, Any]] | None) -> list[str]:
    tactics: list[str] = []
    for attempt in attempts or []:
        if not isinstance(attempt, dict):
            continue
        tactic = attempt.get("tactic")
        if isinstance(tactic, str) and tactic.strip():
            tactics.append(tactic.strip())
    return tactics


def dominant_failure_class(classified_errors: list[dict[str, Any]] | None) -> str | None:
    counts: dict[str, int] = {}
    best_class: str | None = None
    best_count = 0
    for entry in classified_errors or []:
        if not isinstance(entry, dict):
            continue
        error_class = entry.get("error_class")
        if not isinstance(error_class, str) or not error_class.strip():
            continue
        normalized = error_class.strip()
        counts[normalized] = counts.get(normalized, 0) + 1
        if counts[normalized] > best_count:
            best_class = normalized
            best_count = counts[normalized]
    return best_class


def verification_outcome(
    status: str,
    termination_reason: str | None,
    *,
    result_status: str | None = None,
) -> str:
    normalized_reason = (termination_reason or "").strip().lower()
    normalized_status = (result_status or status or "").strip().lower()

    if normalized_status == "rejected" or normalized_reason == "claim_rejected":
        return "rejected"
    if normalized_status == "verified" or normalized_reason == "verified":
        return "verified"
    if normalized_reason in {"timeout", "timed_out"}:
        return "timeout"
    if normalized_reason in {"provider_budget_exhausted", "tool_budget_exhausted"}:
        return "budget_exhausted"
    if normalized_reason in {"faithfulness_failed", "faithfulness_check_failed"}:
        return "faithfulness_failed"
    if normalized_reason in {"vacuous_rejected", "vacuity_check_failed"}:
        return "vacuous_rejected"
    if normalized_reason in {
        "initial_compile_invalid",
        "provider_failed_compile",
        "provider_driver_error",
        "repl_materialization_compile_failed",
        "no_provider_proof_available",
        "exception",
        "early_abort",
        "repetitive_tool_usage",
        "read_without_action",
        "unknown_tool_usage",
    }:
        return "compile_failed"
    if normalized_status == "failed":
        return "compile_failed"
    return normalized_status or "compile_failed"


def finalize_verification_result(
    result: dict[str, Any],
    telemetry: SpanRecorder,
    verification_trace: dict[str, Any] | None = None,
    *,
    claim_id: str | None = None,
    preamble_names: list[str] | None = None,
    tactic_sequence: list[str] | None = None,
    failure_class: str | None = None,
    repair_count: int | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    payload = attach_telemetry(result, telemetry, verification_trace)
    payload["claim_id"] = claim_id
    payload["preamble_names"] = list(preamble_names or [])
    payload["tactic_sequence"] = list(tactic_sequence or [])
    payload["failure_class"] = failure_class
    payload["repair_count"] = repair_count if repair_count is not None else 0
    payload["outcome"] = outcome or verification_outcome(
        str(payload.get("status") or ""),
        payload.get("termination_reason") if isinstance(payload.get("termination_reason"), str) else None,
        result_status=str(payload.get("status") or "") if payload.get("status") is not None else None,
    )
    payload["input_tokens"] = None
    payload["output_tokens"] = None
    payload["estimated_cost_usd"] = None
    return payload


def attach_telemetry(
    result: dict[str, Any],
    telemetry: SpanRecorder,
    verification_trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(result)
    payload["telemetry"] = telemetry.snapshot()
    if verification_trace is not None:
        payload["verification_trace"] = dict(verification_trace)
    return payload


def repl_validation_result(repl_report: dict[str, Any]) -> dict[str, Any]:
    success = bool(repl_report.get("used"))
    fallback_reason = repl_report.get("fallback_reason")
    return {
        "success": success,
        "has_sorry": True,
        "axiom_warnings": [],
        "output": "",
        "errors": []
        if success
        else (
            [fallback_reason]
            if fallback_reason
            else ["LeanInteract did not validate the theorem stub."]
        ),
        "warnings": [],
        "stdout": "",
        "stderr": "",
        "exit_code": 0 if success else 1,
        "source": "repl_start_proof",
    }


async def generate_proof_sketch(
    driver: ProverDriver,
    theorem_with_sorry: str,
    on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    usage_acc: list[ProviderUsage] | None = None,
) -> str | None:
    def reject_tool_call(tool_call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call.id,
            "Proof sketch generation does not use tools.",
            is_error=True,
        )

    sketch_chunks: list[str] = []
    try:
        async for event in driver.prove(
            system_prompt=PROOF_SKETCH_SYSTEM_PROMPT,
            user_prompt=build_proof_sketch_user_prompt(theorem_with_sorry),
            tools=[],
            on_tool_call=reject_tool_call,
            max_steps=1,
        ):
            if event.type == "assistant":
                content = event.data.get("content") if isinstance(event.data, dict) else None
                if isinstance(content, str) and content.strip():
                    sketch_chunks.append(content.strip())
                continue
            if event.type == "usage":
                append_usage_event(usage_acc, event.data if isinstance(event.data, dict) else None)
                continue
            if event.type == "done":
                content = event.data.get("content") if isinstance(event.data, dict) else None
                if isinstance(content, str) and content.strip():
                    sketch_chunks.append(content.strip())
                break
            if event.type == "error":
                if on_progress is not None:
                    on_progress("proof_sketch_fallback", {"reason": str(event.data)})
                return None
            if event.type == "tool_call":
                if on_progress is not None:
                    on_progress(
                        "proof_sketch_fallback",
                        {"reason": "Proof sketch generation attempted an unexpected tool call."},
                    )
                return None
    except Exception as exc:
        if on_progress is not None:
            on_progress("proof_sketch_fallback", {"reason": f"{type(exc).__name__}: {exc}"})
        return None

    sketch = "\n".join(chunk for chunk in sketch_chunks if chunk).strip()
    if sketch and on_progress is not None:
        on_progress("proof_sketch", {"sketch": sketch})
    return sketch or None
