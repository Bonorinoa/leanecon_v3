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


def _planner_raw_metadata(error: BaseException) -> str | None:
    for candidate in (error, getattr(error, "__cause__", None)):
        metadata = getattr(candidate, "provider_metadata", None)
        if metadata is not None:
            return getattr(metadata, "raw_planner_response", None) or getattr(metadata, "response_text", None)
    return None


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

    def connectivity_check(self) -> tuple[bool, str | None]:
        return self._planner.connectivity_check()

    def build_plan_with_telemetry(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        benchmark_mode: bool = False,
    ) -> StageRunResult[PlannerPacket]:
        started_at = time.perf_counter()
        try:
            packet, metadata = self._planner.build_plan_with_metadata(
                claim,
                theorem_stub=theorem_stub,
                preamble_names=preamble_names,
                benchmark_mode=benchmark_mode,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            provider = getattr(self._planner.backend, "provider", "unknown")
            model = getattr(self._planner.backend, "model", "unknown")
            error_code = classify_exception(exc)
            raw_planner_response = _planner_raw_metadata(exc) if error_code == "schema_invalid" else None
            usage = complete_usage(
                stage="planner",
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                success=False,
                error_code=error_code,
            )
            audit = AuditEvent(
                stage="planner",
                event_type="stage_failed",
                provider=provider,
                model=model,
                success=False,
                error_code=usage.error_code,
                error_message=str(exc),
                raw_planner_response=raw_planner_response,
            )
            log_event(
                "planner.stage_failed",
                stage="planner",
                provider=provider,
                model=model,
                latency_ms=round(latency_ms, 3),
                error_code=usage.error_code,
                raw_planner_response=raw_planner_response,
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
        repair_error_code = metadata.metadata.get("error_code") if metadata is not None else None
        repair_error_message = metadata.metadata.get("error_message") if metadata is not None else None
        usage = complete_usage(
            stage="planner",
            provider=self._planner.backend.provider,
            model=packet.model,
            latency_ms=latency_ms,
            success=True,
            error_code=repair_error_code,
            metadata=metadata,
        )
        audits: list[AuditEvent] = []
        if repair_error_code == "schema_invalid":
            log_event(
                "planner.schema_repaired",
                stage="planner",
                provider=self._planner.backend.provider,
                model=packet.model,
                error_code=repair_error_code,
                error_message=repair_error_message,
                raw_planner_response=metadata.raw_planner_response if metadata is not None else None,
            )
            audits.append(
                AuditEvent(
                    stage="planner",
                    event_type="schema_repaired",
                    provider=self._planner.backend.provider,
                    model=packet.model,
                    success=True,
                    error_code=repair_error_code,
                    error_message=str(repair_error_message) if repair_error_message is not None else None,
                    raw_planner_response=metadata.raw_planner_response if metadata is not None else None,
                )
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
        audits.append(audit)
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
        return StageRunResult(payload=packet, usage=usage, audit_events=audits)

    def build_plan(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        benchmark_mode: bool = False,
    ) -> PlannerPacket:
        packet = self.build_plan_with_telemetry(
            claim,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
            benchmark_mode=benchmark_mode,
        ).payload
        return packet.model_copy(
            update={
                "needs_review": not benchmark_mode,
                "review_state": "approved" if benchmark_mode else "awaiting_plan_review",
            }
        )
