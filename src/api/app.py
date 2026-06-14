"""FastAPI v3 application."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import threading
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from evals.benchmark_manifest import build_manifest
from src import __version__
from src.api.jobs import job_store
from src.api.models import (
    FormalizeRequest,
    HealthResponse,
    JobReviewRequest,
    JobAcceptedResponse,
    JobStatusResponse,
    MetricsResponse,
    PlanRequest,
    ProveRequest,
)
from src.backend_capabilities import get_backend_capability
from src.budget_profiles import (
    BudgetProfile,
    clamp_int,
    clamp_target_timeouts,
    evaluate_provider_guardrail,
    resolve_budget_profile,
)
from src.config import (
    API_PORT,
    APP_VERSION,
    BENCHMARK_REQUIRE_PRICING,
    BUDGET_PROFILE,
    COST_TRACKING_ENABLED,
    CORS_ORIGINS,
    EVAL_CLAIMS_DIR,
    HF_TOKEN,
    JOB_MAX_CONCURRENT,
    LEANECON_ENV,
    MISTRAL_API_KEY,
    OLLAMA_API_KEY,
    PROVER_FALLBACK_BACKEND,
    PROVER_PROVIDER,
)
from src.evals.metrics_aggregator import CANONICAL_HISTORY_PATH, load_history_rows
from src.formalizer import DEFAULT_FORMALIZER
from src.lean import lean_workspace_probe
from src.memory import trace_store
from src.observability import (
    AuditEvent,
    StageExecutionError,
    StageTiming,
    TokenUsage,
    classify_exception,
    complete_usage,
    dump_pricing_registry,
    log_event,
    lookup_pricing,
)
from src.planner import PlannerService
from src.providers import is_provider_pinned
from src.prover import DEFAULT_PROVER, ProverTargetTimeouts


def _encode_sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


def _claim_set_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not EVAL_CLAIMS_DIR.exists():
        return counts
    for path in sorted(EVAL_CLAIMS_DIR.glob("*.jsonl")):
        counts[path.stem] = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return counts


def _credential_status(platform: str) -> bool:
    if platform == "huggingface":
        return bool(HF_TOKEN)
    if platform == "ollama":
        return bool(OLLAMA_API_KEY)
    if platform == "mistral":
        return bool(MISTRAL_API_KEY)
    return True


def _backend_entry(
    name: str,
    *,
    backend_name: str,
    stage: str,
    platform: str,
    provider: str,
    model: str,
    endpoint_reachable: bool | None = None,
    availability_reason: str | None = None,
) -> dict[str, Any]:
    provider_pinned = is_provider_pinned(platform, provider)
    price_key = "huggingface" if platform == "huggingface" else platform
    price_known = lookup_pricing(price_key, model) is not None
    credentials_present = _credential_status(platform)
    available = credentials_present and (endpoint_reachable if endpoint_reachable is not None else True)
    benchmark_ready = available and (price_known or not BENCHMARK_REQUIRE_PRICING)
    payload = {
        "name": name,
        "backend": backend_name,
        "capability": get_backend_capability(stage, backend_name),
        "platform": platform,
        "provider": provider,
        "model": model,
        "provider_pinned": provider_pinned,
        "credentials_present": credentials_present,
        "price_known": price_known,
        "available": available,
        "benchmark_ready": benchmark_ready,
    }
    if endpoint_reachable is not None:
        payload["endpoint_reachable"] = endpoint_reachable
    if availability_reason:
        payload["availability_reason"] = availability_reason
    return payload


def _backend_status() -> dict[str, Any]:
    planner_backend = planner.backend
    formalizer_backend = formalizer.backend
    prover_backend = prover.primary_backend
    planner_platform = (
        "ollama"
        if planner_backend.name == "ollama-cloud"
        else "mistral"
        if planner_backend.name == "mistral-structured"
        else "huggingface"
    )
    planner_endpoint_reachable, planner_availability_reason = planner.connectivity_check()
    return {
        "planner": _backend_entry(
            "planner",
            backend_name=planner_backend.name,
            stage="planner",
            platform=planner_platform,
            provider=planner_backend.provider,
            model=planner_backend.model,
            endpoint_reachable=planner_endpoint_reachable,
            availability_reason=planner_availability_reason,
        ),
        "formalizer": _backend_entry(
            "formalizer",
            backend_name=formalizer_backend.name,
            stage="formalizer",
            platform=formalizer_backend.provider,
            provider=formalizer_backend.provider,
            model=formalizer_backend.model,
        ),
        "prover": _backend_entry(
            "prover",
            backend_name=prover_backend.name,
            stage="prover",
            platform=prover_backend.provider,
            provider=PROVER_PROVIDER if prover_backend.provider == "huggingface" else prover_backend.provider,
            model=prover_backend.model,
        ),
        "lean_lsp": prover.lsp_client.status()
        if hasattr(prover.lsp_client, "status")
        else {
            "name": "lean_lsp",
            "state": "unavailable",
            "available": False,
            "benchmark_ready": False,
            "reason": "Configured LSP client does not expose status().",
        },
    }


def _provider_stage_config() -> dict[str, dict[str, str]]:
    fallback_backend = getattr(prover, "fallback_backend", None)
    stages = {
        "planner": {
            "provider": str(planner.backend.provider),
            "model": str(planner.backend.model),
        },
        "formalizer": {
            "provider": str(formalizer.backend.provider),
            "model": str(formalizer.backend.model),
        },
        "prover": {
            "provider": str(prover.primary_backend.provider),
            "model": str(prover.primary_backend.model),
        },
    }
    if fallback_backend is not None:
        stages["prover_fallback"] = {
            "provider": str(getattr(fallback_backend, "provider", "")),
            "model": str(getattr(fallback_backend, "model", "")),
            "backend": str(getattr(fallback_backend, "name", PROVER_FALLBACK_BACKEND)),
        }
    return stages


def _provider_guardrail(profile: BudgetProfile) -> dict[str, Any]:
    return evaluate_provider_guardrail(profile, _provider_stage_config())


def _resolve_request_profile(value: str | None) -> BudgetProfile:
    try:
        return resolve_budget_profile(value, runtime_env=LEANECON_ENV)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _budget_profile_payload(profile: BudgetProfile) -> dict[str, Any]:
    return {
        "active": profile.name,
        "default": BUDGET_PROFILE.name,
        "caps": profile.public_dict(),
        "provider_guardrail": _provider_guardrail(profile),
    }


def _ensure_release_provider_guardrail(profile: BudgetProfile) -> None:
    guardrail = _provider_guardrail(profile)
    if profile.name == "release" and not guardrail.get("release_compliant"):
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Release budget profile requires Mistral-primary provider defaults.",
                "provider_guardrail": guardrail,
            },
        )


def _ensure_profile_allows_packet(profile: BudgetProfile, packet: Any) -> None:
    claim_type = getattr(packet, "claim_type", None)
    claim_scope = getattr(packet, "claim_scope", None)
    if claim_type == "mathlib_native" and not profile.allow_mathlib_native:
        raise HTTPException(
            status_code=400,
            detail=(
                "Budget profile `release` does not allow mathlib-native frontier "
                "proof behavior. Select `frontier` or `research` for diagnostic attempts."
            ),
        )
    if str(claim_scope) in {"frontier_collect", "out_of_scope"} and not profile.allow_frontier_claims:
        raise HTTPException(
            status_code=400,
            detail=(
                "Budget profile `release` does not allow frontier claim scopes. "
                "Select `frontier` or `research` for diagnostic attempts."
            ),
        )


def _validate_prove_budget_request(request: ProveRequest, profile: BudgetProfile) -> None:
    if request.max_turns is not None and request.max_turns > profile.max_prover_turns:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested max_turns={request.max_turns} exceeds `{profile.name}` "
                f"profile cap {profile.max_prover_turns}."
            ),
        )
    if request.timeout is not None and request.timeout > profile.max_timeout_seconds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested timeout={request.timeout} exceeds `{profile.name}` "
                f"profile cap {profile.max_timeout_seconds}."
            ),
        )
    if request.target_timeouts is None:
        return
    requested = request.target_timeouts.model_dump(mode="json")
    for key, cap in profile.target_timeout_caps.items():
        value = requested.get(key)
        if value is not None and int(value) > int(cap):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Requested target_timeouts.{key}={value} exceeds `{profile.name}` "
                    f"profile cap {cap}."
                ),
            )


def _effective_prove_budget(request: ProveRequest, profile: BudgetProfile) -> dict[str, Any]:
    requested_timeouts = (
        request.target_timeouts.model_dump(mode="json")
        if request.target_timeouts is not None
        else profile.target_timeout_caps
    )
    return {
        "max_turns": request.max_turns or profile.max_prover_turns,
        "timeout": request.timeout or profile.max_timeout_seconds,
        "target_timeouts": clamp_target_timeouts(requested_timeouts, profile),
    }


def _stage_timing(stage: str, latency_ms: float | None) -> StageTiming:
    duration = float(latency_ms or 0.0)
    payload = {
        "planner": StageTiming(planner_ms=duration, total_ms=duration),
        "formalizer": StageTiming(formalizer_ms=duration, total_ms=duration),
        "prover": StageTiming(prover_ms=duration, total_ms=duration),
    }
    return payload[stage]


def _record_usage(job_id: str, usage: TokenUsage | dict[str, Any] | None, *, success_override: bool | None = None) -> None:
    if usage is None:
        return
    usage_payload = usage.to_dict() if isinstance(usage, TokenUsage) else dict(usage)
    if success_override is not None:
        usage_payload["success"] = success_override
        if success_override:
            usage_payload["error_code"] = None
    job_store.record_stage_usage(job_id, TokenUsage(**usage_payload))


def _record_audit_events(job_id: str, events: list[AuditEvent] | list[dict[str, Any]] | None) -> None:
    for event in events or []:
        payload = event.to_dict() if isinstance(event, AuditEvent) else dict(event)
        payload.pop("created_at", None)
        job_store.record_audit_event(job_id, AuditEvent(**payload))


def _planner_raw_response(events: list[AuditEvent]) -> tuple[bool, str | None]:
    for event in events:
        if event.stage == "planner" and event.error_code == "schema_invalid":
            return True, event.raw_planner_response or event.metadata.get("raw_planner_response")
    return False, None


def _persist_stage_success(
    job_id: str,
    *,
    stage: str,
    payload: dict[str, Any],
    review_state: str,
    status: str,
    usage: TokenUsage,
    audit_events: list[AuditEvent],
) -> JobStatusResponse:
    if stage == "planner":
        has_schema_invalid, raw_response = _planner_raw_response(audit_events)
        if has_schema_invalid:
            payload["raw_planner_response"] = raw_response
    _record_usage(job_id, usage, success_override=True)
    _record_audit_events(job_id, audit_events)
    job_store.record_timing(job_id, _stage_timing(stage, usage.latency_ms))
    job = job_store.update(job_id, status=status, review_state=review_state, result=payload)
    assert job is not None
    return JobStatusResponse(**job.__dict__)


def _persist_stage_failure(job_id: str, exc: StageExecutionError, *, result: dict[str, Any]) -> JobStatusResponse:
    _record_usage(job_id, exc.usage, success_override=False)
    _record_audit_events(job_id, exc.audit_events)
    if exc.usage is not None:
        job_store.record_timing(job_id, _stage_timing(exc.stage, exc.usage.latency_ms))
    job = job_store.update(
        job_id,
        status="failed",
        review_state="failed",
        result=result,
        error=exc.message,
    )
    assert job is not None
    return JobStatusResponse(**job.__dict__)


def _prometheus_lines(snapshot: dict[str, Any]) -> str:
    lines: list[str] = []
    profile_payload = snapshot.get("budget_profile", {})
    active_profile = str(profile_payload.get("active") or BUDGET_PROFILE.name)
    lines.append(f'leanecon_budget_profile_active{{profile="{active_profile}"}} 1')
    for cap_name, value in (profile_payload.get("caps") or {}).items():
        if isinstance(value, (int, float, bool)):
            metric_value = int(value) if isinstance(value, bool) else value
            lines.append(
                f'leanecon_budget_profile_cap{{profile="{active_profile}",cap="{cap_name}"}} {metric_value}'
            )
    for status, count in snapshot["jobs"].items():
        lines.append(f'leanecon_jobs{{status="{status}"}} {count}')
    for name, count in snapshot["benchmark_claim_sets"].items():
        lines.append(f'leanecon_benchmark_claims{{claim_set="{name}"}} {count}')
    usage_totals = snapshot.get("usage_totals", {})
    lines.append(f"leanecon_input_tokens_total {usage_totals.get('input_tokens', 0)}")
    lines.append(f"leanecon_output_tokens_total {usage_totals.get('output_tokens', 0)}")
    lines.append(f"leanecon_estimated_cost_usd_total {usage_totals.get('estimated_cost_usd', 0.0)}")
    for stage, payload in snapshot.get("usage_by_stage", {}).items():
        lines.append(f'leanecon_stage_input_tokens{{stage="{stage}"}} {payload.get("input_tokens", 0)}')
        lines.append(f'leanecon_stage_output_tokens{{stage="{stage}"}} {payload.get("output_tokens", 0)}')
        lines.append(f'leanecon_stage_estimated_cost_usd{{stage="{stage}"}} {payload.get("estimated_cost_usd", 0.0)}')
    for model_key, payload in snapshot.get("usage_by_model", {}).items():
        provider = payload.get("provider", "")
        model = payload.get("model", model_key)
        lines.append(
            f'leanecon_model_estimated_cost_usd{{provider="{provider}",model="{model}"}} {payload.get("estimated_cost_usd", 0.0)}'
        )
    for claim_type, payload in snapshot.get("usage_by_claim_type", {}).items():
        lines.append(
            f'leanecon_claim_type_estimated_cost_usd{{claim_type="{claim_type}"}} {payload.get("estimated_cost_usd", 0.0)}'
        )
    for claim_scope, payload in snapshot.get("usage_by_claim_scope", {}).items():
        lines.append(
            f'leanecon_claim_scope_estimated_cost_usd{{claim_scope="{claim_scope}"}} {payload.get("estimated_cost_usd", 0.0)}'
        )
    for source, payload in snapshot.get("usage_by_source", {}).items():
        lines.append(
            f'leanecon_usage_source_records{{usage_source="{source}"}} {payload.get("records", 0)}'
        )
    for stage, payload in snapshot.get("latency_by_stage", {}).items():
        lines.append(f'leanecon_stage_latency_ms_sum{{stage="{stage}"}} {payload.get("latency_ms_sum", 0.0)}')
        lines.append(f'leanecon_stage_latency_ms_avg{{stage="{stage}"}} {payload.get("latency_ms_avg", 0.0)}')
    for reason, payload in snapshot.get("budget_exhaustion", {}).get("by_reason", {}).items():
        lines.append(f'leanecon_budget_exhaustions{{reason="{reason}"}} {payload}')
    for error_code, count in snapshot.get("failure_counts", {}).items():
        lines.append(f'leanecon_failures{{error_code="{error_code}"}} {count}')
    for tool_name, count in snapshot.get("tool_call_distribution", {}).items():
        lines.append(f'leanecon_tool_calls{{tool="{tool_name}"}} {count}')
    integrity = snapshot.get("integrity", {})
    if integrity.get("schema_invalid_rate") is not None:
        lines.append(f"leanecon_schema_invalid_rate {integrity['schema_invalid_rate']}")
    for event_name, count in snapshot.get("integrity", {}).get("direct_close_stats", {}).items():
        lines.append(f'leanecon_direct_close_events{{event="{event_name}"}} {count}')
    for backend_name, payload in snapshot.get("backend_status", {}).items():
        lines.append(f'leanecon_backend_available{{backend="{backend_name}"}} {1 if payload.get("available") else 0}')
    recent = snapshot.get("recent", {})
    if recent.get("success_rate") is not None:
        lines.append(f"leanecon_recent_prover_success_rate {recent['success_rate']}")
    if recent.get("avg_cost_per_successful_job") is not None:
        lines.append(f"leanecon_recent_avg_cost_per_successful_job {recent['avg_cost_per_successful_job']}")
    return "\n".join(lines) + "\n"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    trace_store.initialize()
    yield


def _benchmark_category_mix() -> dict[str, int]:
    manifest = build_manifest()
    return {
        bucket: int(count)
        for bucket, count in manifest.get("aggregate_bucket_counts", {}).items()
    }


def _review_transition(job: JobStatusResponse, request: JobReviewRequest) -> tuple[str, str, dict[str, Any], str | None]:
    expected_status = "awaiting_plan_review" if request.stage == "plan" else "awaiting_formalization_review"
    if job.status != expected_status:
        raise HTTPException(status_code=409, detail=f"Job is in `{job.status}`, not `{expected_status}`.")
    payload = dict(job.result or {})
    payload["review"] = {
        "stage": request.stage,
        "decision": request.decision,
        "notes": request.notes,
    }
    payload["review_gate_honest"] = True
    if request.decision == "approve":
        return "completed", "approved", payload, None
    return "failed", "rejected", payload, f"{request.stage} review rejected."


app = FastAPI(
    title="Lean Econ v3",
    description="HILBERT + APOLLO thin harness for economic-theory formalization.",
    version=APP_VERSION,
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

planner = PlannerService()
formalizer = DEFAULT_FORMALIZER
prover = DEFAULT_PROVER
_prove_semaphore = threading.BoundedSemaphore(max(1, int(JOB_MAX_CONCURRENT)))


class _ProofJobSlot:
    def __enter__(self) -> None:
        _prove_semaphore.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        _prove_semaphore.release()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    probe = lean_workspace_probe()
    recent = job_store.recent_prover_stats()
    profile = BUDGET_PROFILE
    guardrail = _provider_guardrail(profile)
    runtime = {
        "probe": probe,
        "port": API_PORT,
        "budget_profile": _budget_profile_payload(profile),
        "provider_guardrail": guardrail,
        "cost_tracking_enabled": COST_TRACKING_ENABLED,
        "pricing_registry": dump_pricing_registry(),
        "backends": _backend_status(),
        "benchmark_category_mix": _benchmark_category_mix(),
        "public_score_ready": False,
        "recent_success_rate_last_100": recent["success_rate"],
        "avg_cost_per_successful_job_last_100": recent["avg_cost_per_successful_job"],
    }
    return HealthResponse(
        status="ok",
        lean_available=bool(probe.get("available")),
        version=__version__,
        runtime=runtime,
    )


@app.get("/metrics", response_model=MetricsResponse)
async def metrics() -> MetricsResponse:
    profile = BUDGET_PROFILE
    snapshot = {
        "jobs": job_store.counts(),
        "memory": trace_store.counts(),
        "benchmark_claim_sets": _claim_set_counts(),
        "benchmark_category_mix": _benchmark_category_mix(),
        **job_store.metrics_snapshot(),
        "backend_status": _backend_status(),
        "budget_profile": _budget_profile_payload(profile),
        "provider_guardrail": _provider_guardrail(profile),
    }
    return MetricsResponse(**snapshot)


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus() -> PlainTextResponse:
    profile = BUDGET_PROFILE
    snapshot = {
        "jobs": job_store.counts(),
        "memory": trace_store.counts(),
        "benchmark_claim_sets": _claim_set_counts(),
        "benchmark_category_mix": _benchmark_category_mix(),
        **job_store.metrics_snapshot(),
        "backend_status": _backend_status(),
        "budget_profile": _budget_profile_payload(profile),
        "provider_guardrail": _provider_guardrail(profile),
    }
    return PlainTextResponse(_prometheus_lines(snapshot), media_type="text/plain; version=0.0.4")


@app.get("/metrics/history")
async def metrics_history() -> dict[str, Any]:
    return {
        "path": str(CANONICAL_HISTORY_PATH),
        "rows": load_history_rows(CANONICAL_HISTORY_PATH),
    }


@app.post("/plan", response_model=JobStatusResponse)
async def plan(request: PlanRequest) -> JobStatusResponse:
    profile = _resolve_request_profile(request.budget_profile)
    _ensure_release_provider_guardrail(profile)
    job = job_store.create(
        status="queued",
        review_state="in_progress",
        result={
            "claim": request.claim,
            "benchmark_mode": request.benchmark_mode,
            "budget_profile": profile.name,
            "budget_caps": profile.public_dict(),
            "provider_guardrail": _provider_guardrail(profile),
        },
    )
    job_store.publish_progress(
        job.id,
        "planner_started",
        stage="planner",
        status="queued",
        review_state="in_progress",
        message="Planner started.",
        metadata={"benchmark_mode": request.benchmark_mode, "budget_profile": profile.name},
    )
    try:
        stage_result = await asyncio.to_thread(
            planner.build_plan_with_telemetry,
            request.claim,
            benchmark_mode=request.benchmark_mode,
        )
    except StageExecutionError as exc:
        job_store.publish_progress(
            job.id,
            "planner_failed",
            stage="planner",
            status="failed",
            review_state="failed",
            message=exc.message,
            metadata={"error_code": exc.error_code},
        )
        return _persist_stage_failure(
            job.id,
            exc,
            result={
                "claim": request.claim,
                "benchmark_mode": request.benchmark_mode,
                "budget_profile": profile.name,
                "budget_caps": profile.public_dict(),
                "provider_guardrail": _provider_guardrail(profile),
                "stage": "planner",
            },
        )

    packet = stage_result.payload.model_copy(
        update={
            "needs_review": not request.benchmark_mode,
            "review_state": "approved" if request.benchmark_mode else "awaiting_plan_review",
        }
    )
    job_store.publish_progress(
        job.id,
        "planner_completed",
        stage="planner",
        status="completed" if request.benchmark_mode else "awaiting_plan_review",
        review_state=packet.review_state,
        message="Planner completed.",
        metadata={"benchmark_mode": request.benchmark_mode},
    )
    return _persist_stage_success(
        job.id,
        stage="planner",
        payload={
            **packet.model_dump(mode="json"),
            "budget_profile": profile.name,
            "budget_caps": profile.public_dict(),
            "provider_guardrail": _provider_guardrail(profile),
        },
        review_state=packet.review_state,
        status="completed" if request.benchmark_mode else "awaiting_plan_review",
        usage=stage_result.usage,
        audit_events=stage_result.audit_events,
    )


@app.post("/formalize", response_model=JobStatusResponse)
async def formalize(request: FormalizeRequest) -> JobStatusResponse:
    profile = _resolve_request_profile(request.budget_profile)
    _ensure_release_provider_guardrail(profile)
    job = job_store.create(
        status="queued",
        review_state="in_progress",
        result={
            "claim": request.claim,
            "benchmark_mode": request.benchmark_mode,
            "budget_profile": profile.name,
            "budget_caps": profile.public_dict(),
            "provider_guardrail": _provider_guardrail(profile),
        },
    )
    job_store.publish_progress(
        job.id,
        "formalizer_started",
        stage="formalizer",
        status="queued",
        review_state="in_progress",
        message="Formalizer started.",
        metadata={"benchmark_mode": request.benchmark_mode, "budget_profile": profile.name},
    )
    try:
        stage_result = await asyncio.to_thread(
            formalizer.formalize_with_telemetry,
            request.claim,
            planner_packet=request.planner_packet,
            benchmark_mode=request.benchmark_mode,
        )
    except StageExecutionError as exc:
        job_store.publish_progress(
            job.id,
            "formalizer_failed",
            stage="formalizer",
            status="failed",
            review_state="failed",
            message=exc.message,
            metadata={"error_code": exc.error_code},
        )
        return _persist_stage_failure(
            job.id,
            exc,
            result={
                "claim": request.claim,
                "benchmark_mode": request.benchmark_mode,
                "budget_profile": profile.name,
                "budget_caps": profile.public_dict(),
                "provider_guardrail": _provider_guardrail(profile),
                "stage": "formalizer",
            },
        )

    payload = stage_result.payload.model_dump(mode="json")
    payload["benchmark_mode"] = request.benchmark_mode
    payload["budget_profile"] = profile.name
    payload["budget_caps"] = profile.public_dict()
    payload["provider_guardrail"] = _provider_guardrail(profile)
    job_store.publish_progress(
        job.id,
        "formalizer_completed",
        stage="formalizer",
        status="completed" if request.benchmark_mode else "awaiting_formalization_review",
        review_state=stage_result.payload.review_state,
        message="Formalizer completed.",
        metadata={"benchmark_mode": request.benchmark_mode},
    )
    return _persist_stage_success(
        job.id,
        stage="formalizer",
        payload=payload,
        review_state=stage_result.payload.review_state,
        status="completed" if request.benchmark_mode else "awaiting_formalization_review",
        usage=stage_result.usage,
        audit_events=stage_result.audit_events,
    )


async def _run_prove_job(job_id: str, request: ProveRequest) -> None:
    with _ProofJobSlot():
        await _run_prove_job_in_slot(job_id, request)


async def _run_prove_job_in_slot(job_id: str, request: ProveRequest) -> None:
    started_at = time.perf_counter()
    profile = resolve_budget_profile(request.budget_profile, runtime_env=LEANECON_ENV)
    effective_budget = _effective_prove_budget(request, profile)
    job_store.update(job_id, status="running_prover", review_state="in_progress")
    job_store.publish_progress(
        job_id,
        "prover_started",
        stage="prover",
        status="running_prover",
        review_state="in_progress",
        message="Prover started.",
        metadata={
            "benchmark_mode": request.benchmark_mode,
            "budget_profile": profile.name,
            "effective_budget": effective_budget,
        },
    )
    target_timeouts = ProverTargetTimeouts.model_validate(effective_budget["target_timeouts"])
    try:
        result = await prover.prove(
            request.formalization_packet,
            job_id,
            max_turns=effective_budget["max_turns"],
            timeout=effective_budget["timeout"],
            target_timeouts=target_timeouts,
            allow_decomposition=request.allow_decomposition,
            benchmark_mode=request.benchmark_mode,
            budget_profile=profile,
            on_progress=lambda event, payload: job_store.publish(job_id, payload, event=event),
        )
        payload = result.model_dump(mode="json")
        prover_usage = payload.get("usage_by_stage", {}).get("prover")
        _record_usage(job_id, prover_usage, success_override=result.status == "verified")
        _record_audit_events(job_id, payload.get("audit_summary", {}).get("events"))
        timings = payload.get("timing_breakdown") or {}
        job_store.record_timing(
            job_id,
            StageTiming(
                prover_ms=float(timings.get("prover_ms") or 0.0),
                total_ms=float(timings.get("total_ms") or timings.get("prover_ms") or 0.0),
            ),
        )
        provider = prover_usage.get("provider") if isinstance(prover_usage, dict) else prover.primary_backend.provider
        model = prover_usage.get("model") if isinstance(prover_usage, dict) else prover.primary_backend.model
        terminal_event = AuditEvent(
            stage="prover",
            event_type="job_terminal",
            provider=str(provider),
            model=str(model),
            success=result.status == "verified",
            error_code=result.failure.error_code if result.failure is not None else None,
            error_message=result.failure.message if result.failure is not None else None,
            metadata={
                "termination_reason": result.termination_reason,
                "benchmark_mode": result.benchmark_mode,
                "target_timeouts": result.target_timeouts.model_dump(mode="json"),
                "budget_profile": profile.name,
                "effective_budget": effective_budget,
            },
        )
        job_store.record_audit_event(job_id, terminal_event)
        if result.status == "verified":
            job_store.publish_progress(
                job_id,
                "prover_verified",
                stage="prover",
                status="completed",
                review_state="complete",
                message="Prover verified the claim.",
                metadata={"termination_reason": result.termination_reason},
            )
            job_store.update(job_id, status="completed", review_state="complete", result=payload)
            return
        job_store.publish_progress(
            job_id,
            "prover_failed",
            stage="prover",
            status="failed",
            review_state="failed",
            message=result.failure.message if result.failure is not None else "Proof failed.",
            metadata={"termination_reason": result.termination_reason},
        )
        job_store.update(
            job_id,
            status="failed",
            review_state="failed",
            result=payload,
            error=result.failure.message if result.failure is not None else "Proof failed.",
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        error_code = classify_exception(exc)
        usage = complete_usage(
            stage="prover",
            provider=prover.primary_backend.provider,
            model=prover.primary_backend.model,
            latency_ms=latency_ms,
            success=False,
            error_code=error_code,
        )
        _record_usage(job_id, usage, success_override=False)
        job_store.record_timing(job_id, StageTiming(prover_ms=latency_ms, total_ms=latency_ms))
        job_store.record_audit_event(
            job_id,
            AuditEvent(
                stage="prover",
                event_type="job_terminal",
                provider=prover.primary_backend.provider,
                model=prover.primary_backend.model,
                success=False,
                error_code=error_code,
                error_message=str(exc),
                metadata={
                    "termination_reason": "exception",
                    "benchmark_mode": request.benchmark_mode,
                    "budget_profile": profile.name,
                    "effective_budget": effective_budget,
                    "target_timeouts": target_timeouts.model_dump(mode="json"),
                },
            ),
        )
        job_store.update(
            job_id,
            status="failed",
            review_state="failed",
            result={
                "benchmark_mode": request.benchmark_mode,
                "budget_profile": profile.name,
                "budget_caps": profile.public_dict(),
                "effective_budget": effective_budget,
                "target_timeouts": target_timeouts.model_dump(mode="json"),
                "stage": "prover",
            },
            error=str(exc),
        )
        job_store.publish_progress(
            job_id,
            "prover_failed",
            stage="prover",
            status="failed",
            review_state="failed",
            message=str(exc),
            metadata={"termination_reason": "exception", "error_code": error_code},
        )
        log_event("api.prove_job_failed", stage="prover", error_code=error_code, message=str(exc))


@app.post("/prove", response_model=JobAcceptedResponse)
async def prove(request: ProveRequest) -> JobAcceptedResponse:
    profile = _resolve_request_profile(request.budget_profile)
    _ensure_release_provider_guardrail(profile)
    _ensure_profile_allows_packet(profile, request.formalization_packet)
    _validate_prove_budget_request(request, profile)
    effective_budget = _effective_prove_budget(request, profile)
    job = job_store.create(
        status="queued",
        review_state="auto_approved" if request.benchmark_mode else "queued",
        result={
            "benchmark_mode": request.benchmark_mode,
            "budget_profile": profile.name,
            "budget_caps": profile.public_dict(),
            "effective_budget": effective_budget,
            "provider_guardrail": _provider_guardrail(profile),
            "target_timeouts": effective_budget["target_timeouts"],
            "theorem_name": request.formalization_packet.theorem_name,
            "claim": request.formalization_packet.claim,
            "claim_type": request.formalization_packet.claim_type,
            "claim_scope": request.formalization_packet.claim_scope,
        },
    )
    job_store.publish_progress(
        job.id,
        "prover_queued",
        stage="prover",
        status="queued",
        review_state=job.review_state,
        message="Proof job queued.",
        metadata={
            "benchmark_mode": request.benchmark_mode,
            "budget_profile": profile.name,
            "effective_budget": effective_budget,
        },
    )
    threading.Thread(
        target=lambda: asyncio.run(_run_prove_job(job.id, request)),
        daemon=True,
    ).start()
    return JobAcceptedResponse(job_id=job.id, status=job.status, message="Proof job queued.")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return JobStatusResponse(**job.__dict__)


@app.post("/jobs/{job_id}/review", response_model=JobStatusResponse)
async def review_job(job_id: str, request: JobReviewRequest) -> JobStatusResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    current = JobStatusResponse(**job.__dict__)
    next_status, next_review_state, payload, error = _review_transition(current, request)
    updated = job_store.update(
        job_id,
        status=next_status,
        review_state=next_review_state,
        result=payload,
        error=error,
    )
    assert updated is not None
    job_store.publish_progress(
        job_id,
        f"{request.stage}_review_{request.decision}d",
        stage=request.stage,
        status=next_status,
        review_state=next_review_state,
        message=f"{request.stage.capitalize()} review {request.decision}d.",
        metadata={"notes": request.notes},
    )
    return JobStatusResponse(**updated.__dict__)


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    if job_store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job.")

    async def event_stream():
        async for envelope in job_store.subscribe(job_id):
            yield _encode_sse(str(envelope.get("event") or "job.update"), dict(envelope.get("payload") or {}))

    return StreamingResponse(event_stream(), media_type="text/event-stream")
