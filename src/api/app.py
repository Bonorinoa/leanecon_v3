"""FastAPI v3 application."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import shutil
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse

from src import __version__
from src.api.jobs import job_store
from src.api.models import (
    FormalizeRequest,
    HealthResponse,
    JobAcceptedResponse,
    JobStatusResponse,
    MetricsResponse,
    PlanRequest,
    ProveRequest,
)
from src.config import (
    API_PORT,
    APP_VERSION,
    BENCHMARK_REQUIRE_PRICING,
    COST_TRACKING_ENABLED,
    CORS_ORIGINS,
    EVAL_CLAIMS_DIR,
    HF_TOKEN,
    LEAN_WORKSPACE,
    MISTRAL_API_KEY,
    PLANNER_PROVIDER,
    PROVER_PROVIDER,
)
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
    encode_sse,
    log_event,
    lookup_pricing,
)
from src.planner import PlannerService
from src.providers import is_provider_pinned
from src.prover import DEFAULT_PROVER, ProverTargetTimeouts


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
    if platform == "mistral":
        return bool(MISTRAL_API_KEY)
    return True


def _backend_entry(name: str, *, backend_name: str, platform: str, provider: str, model: str) -> dict[str, Any]:
    provider_pinned = is_provider_pinned(platform, provider)
    price_key = "huggingface" if platform == "huggingface" else platform
    price_known = lookup_pricing(price_key, model) is not None
    credentials_present = _credential_status(platform)
    available = credentials_present
    benchmark_ready = available and (price_known or not BENCHMARK_REQUIRE_PRICING)
    return {
        "name": name,
        "backend": backend_name,
        "platform": platform,
        "provider": provider,
        "model": model,
        "provider_pinned": provider_pinned,
        "credentials_present": credentials_present,
        "price_known": price_known,
        "available": available,
        "benchmark_ready": benchmark_ready,
    }


def _backend_status() -> dict[str, Any]:
    planner_backend = planner.backend
    formalizer_backend = formalizer.backend
    prover_backend = prover.primary_backend
    return {
        "planner": _backend_entry(
            "planner",
            backend_name=planner_backend.name,
            platform="huggingface",
            provider=planner_backend.provider,
            model=planner_backend.model,
        ),
        "formalizer": _backend_entry(
            "formalizer",
            backend_name=formalizer_backend.name,
            platform=formalizer_backend.provider,
            provider=formalizer_backend.provider,
            model=formalizer_backend.model,
        ),
        "prover": _backend_entry(
            "prover",
            backend_name=prover_backend.name,
            platform=prover_backend.provider,
            provider=PROVER_PROVIDER if prover_backend.provider == "huggingface" else prover_backend.provider,
            model=prover_backend.model,
        ),
        "lean_lsp": {
            "name": "lean_lsp",
            "binary": "uvx lean-lsp-mcp",
            "available": shutil.which("uvx") is not None and LEAN_WORKSPACE.exists(),
            "benchmark_ready": shutil.which("uvx") is not None and LEAN_WORKSPACE.exists(),
        },
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
    for error_code, count in snapshot.get("failure_counts", {}).items():
        lines.append(f'leanecon_failures{{error_code="{error_code}"}} {count}')
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


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    probe = lean_workspace_probe()
    recent = job_store.recent_prover_stats()
    runtime = {
        "probe": probe,
        "port": API_PORT,
        "cost_tracking_enabled": COST_TRACKING_ENABLED,
        "pricing_registry": dump_pricing_registry(),
        "backends": _backend_status(),
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
    snapshot = {
        "jobs": job_store.counts(),
        "memory": trace_store.counts(),
        "benchmark_claim_sets": _claim_set_counts(),
        **job_store.metrics_snapshot(),
        "backend_status": _backend_status(),
    }
    return MetricsResponse(**snapshot)


@app.get("/metrics/prometheus", response_class=PlainTextResponse)
async def metrics_prometheus() -> PlainTextResponse:
    snapshot = {
        "jobs": job_store.counts(),
        "memory": trace_store.counts(),
        "benchmark_claim_sets": _claim_set_counts(),
        **job_store.metrics_snapshot(),
        "backend_status": _backend_status(),
    }
    return PlainTextResponse(_prometheus_lines(snapshot), media_type="text/plain; version=0.0.4")


@app.post("/plan", response_model=JobStatusResponse)
async def plan(request: PlanRequest) -> JobStatusResponse:
    job = job_store.create(
        status="queued",
        review_state="in_progress",
        result={"claim": request.claim, "benchmark_mode": request.benchmark_mode},
    )
    try:
        stage_result = planner.build_plan_with_telemetry(request.claim, benchmark_mode=request.benchmark_mode)
    except StageExecutionError as exc:
        return _persist_stage_failure(
            job.id,
            exc,
            result={"claim": request.claim, "benchmark_mode": request.benchmark_mode, "stage": "planner"},
        )

    packet = stage_result.payload.model_copy(
        update={
            "needs_review": not request.benchmark_mode,
            "review_state": "approved" if request.benchmark_mode else "awaiting_plan_review",
        }
    )
    return _persist_stage_success(
        job.id,
        stage="planner",
        payload=packet.model_dump(mode="json"),
        review_state=packet.review_state,
        status="completed" if request.benchmark_mode else "awaiting_plan_review",
        usage=stage_result.usage,
        audit_events=stage_result.audit_events,
    )


@app.post("/formalize", response_model=JobStatusResponse)
async def formalize(request: FormalizeRequest) -> JobStatusResponse:
    job = job_store.create(
        status="queued",
        review_state="in_progress",
        result={"claim": request.claim, "benchmark_mode": request.benchmark_mode},
    )
    try:
        stage_result = formalizer.formalize_with_telemetry(
            request.claim,
            planner_packet=request.planner_packet,
            benchmark_mode=request.benchmark_mode,
        )
    except StageExecutionError as exc:
        return _persist_stage_failure(
            job.id,
            exc,
            result={"claim": request.claim, "benchmark_mode": request.benchmark_mode, "stage": "formalizer"},
        )

    payload = stage_result.payload.model_dump(mode="json")
    payload["benchmark_mode"] = request.benchmark_mode
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
    started_at = time.perf_counter()
    job_store.update(job_id, status="running_prover", review_state="in_progress")
    target_timeouts = (
        ProverTargetTimeouts.model_validate(request.target_timeouts.model_dump(mode="json"))
        if request.target_timeouts is not None
        else None
    )
    try:
        result = await prover.prove(
            request.formalization_packet,
            job_id,
            max_turns=request.max_turns,
            timeout=request.timeout,
            target_timeouts=target_timeouts,
            allow_decomposition=request.allow_decomposition,
            benchmark_mode=request.benchmark_mode,
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
            },
        )
        job_store.record_audit_event(job_id, terminal_event)
        if result.status == "verified":
            job_store.update(job_id, status="completed", review_state="complete", result=payload)
            return
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
                    "target_timeouts": request.target_timeouts.model_dump(mode="json") if request.target_timeouts else None,
                },
            ),
        )
        job_store.update(
            job_id,
            status="failed",
            review_state="failed",
            result={
                "benchmark_mode": request.benchmark_mode,
                "target_timeouts": request.target_timeouts.model_dump(mode="json") if request.target_timeouts else None,
                "stage": "prover",
            },
            error=str(exc),
        )
        log_event("api.prove_job_failed", stage="prover", error_code=error_code, message=str(exc))


@app.post("/prove", response_model=JobAcceptedResponse)
async def prove(request: ProveRequest) -> JobAcceptedResponse:
    job = job_store.create(
        status="queued",
        review_state="auto_approved" if request.benchmark_mode else "queued",
        result={
            "benchmark_mode": request.benchmark_mode,
            "target_timeouts": request.target_timeouts.model_dump(mode="json") if request.target_timeouts else None,
            "theorem_name": request.formalization_packet.theorem_name,
            "claim": request.formalization_packet.claim,
        },
    )
    asyncio.create_task(_run_prove_job(job.id, request))
    return JobAcceptedResponse(job_id=job.id, status=job.status, message="Proof job queued.")


@app.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return JobStatusResponse(**job.__dict__)


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    if job_store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Unknown job.")

    async def event_stream():
        async for payload in job_store.subscribe(job_id):
            yield encode_sse("job.update", payload)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
