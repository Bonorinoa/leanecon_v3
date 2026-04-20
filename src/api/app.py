"""FastAPI v3 application."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

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
from src.config import API_PORT, APP_VERSION, CORS_ORIGINS, EVAL_CLAIMS_DIR
from src.formalizer import DEFAULT_FORMALIZER
from src.lean import lean_workspace_probe
from src.memory import trace_store
from src.observability import encode_sse
from src.planner import PlannerService
from src.prover import DEFAULT_PROVER


def _claim_set_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not EVAL_CLAIMS_DIR.exists():
        return counts
    for path in sorted(EVAL_CLAIMS_DIR.glob("*.jsonl")):
        counts[path.stem] = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return counts


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
    return HealthResponse(
        status="ok",
        lean_available=bool(probe.get("available")),
        version=__version__,
        runtime={"probe": probe, "port": API_PORT},
    )


@app.get("/metrics", response_model=MetricsResponse)
async def metrics() -> MetricsResponse:
    return MetricsResponse(
        jobs=job_store.counts(),
        memory=trace_store.counts(),
        benchmark_claim_sets=_claim_set_counts(),
    )


@app.post("/plan", response_model=JobStatusResponse)
async def plan(request: PlanRequest) -> JobStatusResponse:
    packet = planner.build_plan(request.claim, benchmark_mode=request.benchmark_mode)
    job = job_store.create(
        status="awaiting_plan_review" if not request.benchmark_mode else "completed",
        review_state=packet.review_state,
        result=packet.model_dump(mode="json"),
    )
    return JobStatusResponse(**job.__dict__)


@app.post("/formalize", response_model=JobStatusResponse)
async def formalize(request: FormalizeRequest) -> JobStatusResponse:
    packet = formalizer.formalize(
        request.claim,
        planner_packet=request.planner_packet,
        benchmark_mode=request.benchmark_mode,
    )
    result = packet.model_dump(mode="json")
    result["benchmark_mode"] = request.benchmark_mode
    job = job_store.create(
        status="awaiting_formalization_review" if not request.benchmark_mode else "completed",
        review_state=packet.review_state,
        result=result,
    )
    return JobStatusResponse(**job.__dict__)


async def _run_prove_job(job_id: str, request: ProveRequest) -> None:
    job_store.update(job_id, status="running_prover", review_state="in_progress")
    result = await prover.prove(
        request.formalization_packet,
        job_id,
        max_turns=request.max_turns,
        timeout=request.timeout,
        allow_decomposition=request.allow_decomposition,
    )
    payload = result.model_dump(mode="json")
    if result.status == "verified":
        job_store.update(job_id, status="completed", review_state="complete", result=payload)
    else:
        job_store.update(
            job_id,
            status="failed",
            review_state="failed",
            result=payload,
            error=result.failure.message if result.failure is not None else "Proof failed.",
        )


@app.post("/prove", response_model=JobAcceptedResponse)
async def prove(request: ProveRequest) -> JobAcceptedResponse:
    job = job_store.create(
        status="queued",
        review_state="auto_approved" if request.benchmark_mode else "queued",
        result={
            "benchmark_mode": request.benchmark_mode,
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
