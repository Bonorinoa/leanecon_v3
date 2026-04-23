"""FastAPI request and response models for v3."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.formalizer.models import FormalizationPacket


class LeanEconModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanRequest(LeanEconModel):
    claim: str = Field(min_length=1)
    context: dict[str, Any] | None = None
    benchmark_mode: bool = False


class FormalizeRequest(LeanEconModel):
    claim: str = Field(min_length=1)
    planner_packet: dict[str, Any] | None = None
    benchmark_mode: bool = False


class ProverTargetTimeoutsRequest(LeanEconModel):
    theorem_body: int | None = Field(default=None, ge=1, le=1800)
    subgoal: int | None = Field(default=None, ge=1, le=1800)
    apollo_lemma: int | None = Field(default=None, ge=1, le=1800)


class ProveRequest(LeanEconModel):
    formalization_packet: FormalizationPacket
    max_turns: int = Field(default=8, ge=1, le=32)
    timeout: int = Field(default=300, ge=1, le=1800)
    target_timeouts: ProverTargetTimeoutsRequest | None = None
    allow_decomposition: bool = True
    benchmark_mode: bool = False

    @field_validator("formalization_packet", mode="before")
    @classmethod
    def _sanitize_formalization_packet(cls, value: Any) -> Any:
        if isinstance(value, dict):
            allowed = FormalizationPacket.model_fields.keys()
            return {key: item for key, item in value.items() if key in allowed}
        return value


class JobReviewRequest(LeanEconModel):
    stage: str = Field(pattern="^(plan|formalization)$")
    decision: str = Field(pattern="^(approve|reject)$")
    notes: str | None = None


class JobAcceptedResponse(LeanEconModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(LeanEconModel):
    id: str
    status: str
    review_state: str | None = None
    created_at: str
    updated_at: str
    result: dict[str, Any] | None = None
    error: str | None = None


class HealthResponse(LeanEconModel):
    status: str
    lean_available: bool
    version: str
    runtime: dict[str, Any]


class MetricsResponse(LeanEconModel):
    jobs: dict[str, int]
    memory: dict[str, int]
    benchmark_claim_sets: dict[str, int]
    usage_totals: dict[str, Any] = Field(default_factory=dict)
    usage_by_stage: dict[str, Any] = Field(default_factory=dict)
    usage_by_model: dict[str, Any] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    stage_success_counts: dict[str, Any] = Field(default_factory=dict)
    stage_event_counts: dict[str, int] = Field(default_factory=dict)
    tool_call_distribution: dict[str, int] = Field(default_factory=dict)
    benchmark_category_mix: dict[str, int] = Field(default_factory=dict)
    integrity: dict[str, Any] = Field(default_factory=dict)
    backend_status: dict[str, Any] = Field(default_factory=dict)
    recent: dict[str, Any] = Field(default_factory=dict)
