"""Thin service facade for the v3 formalizer."""

from __future__ import annotations

import time
from typing import Any

from src.observability.errors import StageExecutionError, classify_exception
from src.observability.logging import log_event
from src.observability.models import AuditEvent, StageRunResult
from src.observability.pricing import complete_usage, stable_hash_text
from src.config import FORMALIZER_BACKEND
from src.formalizer.context_builder import FormalizerContextBuilder
from src.formalizer.formalizer import (
    DriverRegistry,
    Formalizer,
    FormalizerBackend,
    FormalizerDriver,
    FormalizerDriverError,
    HuggingFaceFormalizerDriver,
    MistralFormalizerDriver,
)
from src.formalizer.models import FormalizationPacket


class FormalizerService:
    """Backward-compatible entry point for the API layer."""

    def __init__(
        self,
        backend: str = FORMALIZER_BACKEND,
        *,
        mistral_driver: FormalizerDriver | None = None,
        huggingface_driver: FormalizerDriver | None = None,
        context_builder: FormalizerContextBuilder | None = None,
    ) -> None:
        self._formalizer = Formalizer(
            backend=backend,
            mistral_driver=mistral_driver,
            huggingface_driver=huggingface_driver,
            context_builder=context_builder,
        )

    @property
    def backend(self) -> FormalizerBackend:
        return self._formalizer.backend

    @property
    def registry(self) -> DriverRegistry:
        return self._formalizer.registry

    def formalize_with_telemetry(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        benchmark_mode: bool = False,
    ) -> StageRunResult[FormalizationPacket]:
        started_at = time.perf_counter()
        try:
            packet, metadata = self._formalizer.formalize_with_metadata(
                claim,
                planner_packet=planner_packet,
                benchmark_mode=benchmark_mode,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - started_at) * 1000.0
            provider = self._formalizer.backend.provider
            model = self._formalizer.backend.model
            usage = complete_usage(
                stage="formalizer",
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                success=False,
                error_code=classify_exception(exc),
            )
            audit = AuditEvent(
                stage="formalizer",
                event_type="stage_failed",
                provider=provider,
                model=model,
                success=False,
                error_code=usage.error_code,
                error_message=str(exc),
            )
            log_event(
                "formalizer.stage_failed",
                stage="formalizer",
                provider=provider,
                model=model,
                latency_ms=round(latency_ms, 3),
                error_code=usage.error_code,
            )
            raise StageExecutionError(
                stage="formalizer",
                provider=provider,
                model=model,
                error_code=usage.error_code or "unknown",
                message=str(exc),
                usage=usage,
                audit_events=[audit],
            ) from exc
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        usage = complete_usage(
            stage="formalizer",
            provider=packet.provider,
            model=packet.model,
            latency_ms=latency_ms,
            success=True,
            metadata=metadata,
        )
        audit = AuditEvent(
            stage="formalizer",
            event_type="stage_completed",
            provider=packet.provider,
            model=packet.model,
            success=True,
            prompt_hash=stable_hash_text(metadata.prompt_text if metadata is not None else None),
            response_hash=stable_hash_text(metadata.response_text if metadata is not None else None),
            metadata={
                "review_state": packet.review_state,
                "faithfulness_score": packet.faithfulness.score,
                "usage_source": usage.usage_source,
            },
        )
        log_event(
            "formalizer.stage_completed",
            stage="formalizer",
            provider=packet.provider,
            model=packet.model,
            latency_ms=round(latency_ms, 3),
            faithfulness_score=packet.faithfulness.score,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
        )
        return StageRunResult(payload=packet, usage=usage, audit_events=[audit])

    def formalize(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        benchmark_mode: bool = False,
    ) -> FormalizationPacket:
        return self.formalize_with_telemetry(
            claim,
            planner_packet=planner_packet,
            benchmark_mode=benchmark_mode,
        ).payload


DEFAULT_FORMALIZER = FormalizerService()
