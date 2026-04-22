"""Shared observability models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Generic, TypeVar


@dataclass(frozen=True)
class TokenUsage:
    stage: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: float | None = None
    success: bool = True
    usage_source: str = "unavailable"
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if payload["estimated_cost_usd"] is not None:
            payload["estimated_cost_usd"] = round(float(payload["estimated_cost_usd"]), 8)
        if payload["latency_ms"] is not None:
            payload["latency_ms"] = round(float(payload["latency_ms"]), 3)
        return payload


@dataclass(frozen=True)
class StageTiming:
    planner_ms: float = 0.0
    formalizer_ms: float = 0.0
    prover_ms: float = 0.0
    total_ms: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "planner_ms": round(self.planner_ms, 3),
            "formalizer_ms": round(self.formalizer_ms, 3),
            "prover_ms": round(self.prover_ms, 3),
            "total_ms": round(self.total_ms, 3),
        }


@dataclass(frozen=True)
class AuditEvent:
    stage: str
    event_type: str
    provider: str
    model: str
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    prompt_hash: str | None = None
    response_hash: str | None = None
    raw_planner_response: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class ProviderCallMetadata:
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_source: str = "unavailable"
    prompt_text: str | None = None
    response_text: str | None = None
    raw_planner_response: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usage_source": self.usage_source,
            "raw_planner_response": self.raw_planner_response,
            "metadata": dict(self.metadata),
        }


T = TypeVar("T")


@dataclass(frozen=True)
class StageRunResult(Generic[T]):
    payload: T
    usage: TokenUsage
    audit_events: list[AuditEvent] = field(default_factory=list)
