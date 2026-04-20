"""Planner service facade."""

from __future__ import annotations

import time

from src.observability.errors import StageExecutionError, classify_exception
from src.observability.logging import log_event
from src.observability.models import AuditEvent, StageRunResult
from src.observability.pricing import complete_usage, stable_hash_text
from src.planner.models import PlannerPacket
from src.planner.planner import Planner, PlannerDriver
from src.planner.retrieval import PlannerRetrievalService, TextEmbedder


class PlannerService:
    def __init__(
        self,
        *,
        backend: str | None = None,
        driver: PlannerDriver | None = None,
        retrieval_service: PlannerRetrievalService | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        planner_kwargs: dict[str, object] = {}
        if backend is not None:
            planner_kwargs["backend"] = backend
        if driver is not None:
            planner_kwargs["driver"] = driver
        if retrieval_service is not None:
            planner_kwargs["retrieval_service"] = retrieval_service
        if embedder is not None:
            planner_kwargs["embedder"] = embedder
        self._planner = Planner(**planner_kwargs)

    @property
    def backend(self):
        return self._planner.backend

    def build_plan_with_telemetry(
        self,
        claim: str,
        *,
        benchmark_mode: bool = False,
    ) -> StageRunResult[PlannerPacket]:
        started_at = time.perf_counter()
        try:
            packet, metadata = self._planner.build_plan_with_metadata(claim, benchmark_mode=benchmark_mode)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            provider = getattr(self._planner.backend, "provider", "unknown")
            model = getattr(self._planner.backend, "model", "unknown")
            usage = complete_usage(
                stage="planner",
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                success=False,
                error_code=classify_exception(exc),
            )
            audit = AuditEvent(
                stage="planner",
                event_type="stage_failed",
                provider=provider,
                model=model,
                success=False,
                error_code=usage.error_code,
                error_message=str(exc),
            )
            log_event(
                "planner.stage_failed",
                stage="planner",
                provider=provider,
                model=model,
                latency_ms=round(latency_ms, 3),
                error_code=usage.error_code,
            )
            raise StageExecutionError(
                stage="planner",
                provider=provider,
                model=model,
                error_code=usage.error_code or "unknown",
                message=str(exc),
                usage=usage,
                audit_events=[audit],
            ) from exc

        latency_ms = (time.perf_counter() - started_at) * 1000.0
        usage = complete_usage(
            stage="planner",
            provider=self._planner.backend.provider,
            model=packet.model,
            latency_ms=latency_ms,
            success=True,
            metadata=metadata,
        )
        audit = AuditEvent(
            stage="planner",
            event_type="stage_completed",
            provider=self._planner.backend.provider,
            model=packet.model,
            success=True,
            prompt_hash=stable_hash_text(metadata.prompt_text if metadata is not None else None),
            response_hash=stable_hash_text(metadata.response_text if metadata is not None else None),
            metadata={
                "claim_length": len(claim),
                "review_state": packet.review_state,
                "usage_source": usage.usage_source,
            },
        )
        log_event(
            "planner.stage_completed",
            stage="planner",
            provider=self._planner.backend.provider,
            model=packet.model,
            latency_ms=round(latency_ms, 3),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
        )
        return StageRunResult(payload=packet, usage=usage, audit_events=[audit])

    def build_plan(self, claim: str, *, benchmark_mode: bool = False) -> PlannerPacket:
        packet = self.build_plan_with_telemetry(claim, benchmark_mode=benchmark_mode).payload
        return packet.model_copy(
            update={
                "needs_review": not benchmark_mode,
                "review_state": "approved" if benchmark_mode else "awaiting_plan_review",
            }
        )
