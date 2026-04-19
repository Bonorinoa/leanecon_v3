"""FastAPI request and response models for v3."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class VerifyRequest(LeanEconModel):
    theorem_with_sorry: str = Field(min_length=1)
    formalization_packet: dict[str, Any] | None = None
    max_steps: int = Field(default=32, ge=1, le=128)
    timeout: int = Field(default=300, ge=1, le=1800)
    benchmark_mode: bool = False


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
