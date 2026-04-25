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
class RetrievalEvent:
    retrieved_premises: list[dict[str, Any]] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    latency_ms: float = 0.0
    k: int = 5
    source: str = "mathlib_rag"
    goal_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "RetrievalEvent",
            "source": self.source,
            "goal_digest": self.goal_digest,
            "retrieved_premises": [dict(premise) for premise in self.retrieved_premises],
            "scores": [round(float(score), 6) for score in self.scores],
            "latency_ms": round(float(self.latency_ms), 3),
            "k": int(self.k),
            "retrieved_count": len(self.retrieved_premises),
            "hit": bool(self.retrieved_premises),
        }


@dataclass(frozen=True)
class ProgressDelta:
    goals_reduced: bool = False
    complexity_reduced: bool = False
    stall_detected: bool = False
    goal_count_before: int = 0
    goal_count_after: int = 0
    complexity_before: int = 0
    complexity_after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "ProgressDelta",
            "goals_reduced": self.goals_reduced,
            "complexity_reduced": self.complexity_reduced,
            "stall_detected": self.stall_detected,
            "goal_count_before": int(self.goal_count_before),
            "goal_count_after": int(self.goal_count_after),
            "complexity_before": int(self.complexity_before),
            "complexity_after": int(self.complexity_after),
        }


@dataclass(frozen=True)
class StateTransition:
    goal_count_before: int
    goal_count_after: int
    progress_delta: ProgressDelta
    state_hash_before: str | None = None
    state_hash_after: str | None = None
    turn_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "StateTransition",
            "turn_index": self.turn_index,
            "goal_count_before": int(self.goal_count_before),
            "goal_count_after": int(self.goal_count_after),
            "state_hash_before": self.state_hash_before,
            "state_hash_after": self.state_hash_after,
            "progress_delta": self.progress_delta.to_dict(),
        }


@dataclass(frozen=True)
class ToolUsageTrace:
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    state_hash_before: str | None = None
    state_hash_after: str | None = None
    latency_ms: float | None = None
    success: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": "ToolUsageTrace",
            "tool_name": self.tool_name,
            "args": dict(self.args),
            "result": self.result,
            "state_hash_before": self.state_hash_before,
            "state_hash_after": self.state_hash_after,
            "latency_ms": (
                round(float(self.latency_ms), 3) if self.latency_ms is not None else None
            ),
            "success": self.success,
        }


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
