"""Structured models for the packet-aware prover."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProverModel(BaseModel):
    """Strict base model for prover data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProverToolInvocation(ProverModel):
    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProverAction(ProverModel):
    """Single-step structured model output for the prover loop."""

    action_type: Literal["tool", "decompose", "finish"]
    rationale: str = ""
    tool: ProverToolInvocation | None = None
    decomposition_statement: str | None = None
    decomposition_name: str | None = None
    finish_reason: str | None = None

    @field_validator("tool")
    @classmethod
    def _validate_tool(
        cls, value: ProverToolInvocation | None, info: Any
    ) -> ProverToolInvocation | None:
        if info.data.get("action_type") == "tool" and value is None:
            raise ValueError("Tool actions must include a tool invocation.")
        return value


class ProverTargetTimeouts(ProverModel):
    theorem_body: int | None = Field(default=None, ge=1, le=1800)
    subgoal: int | None = Field(default=None, ge=1, le=1800)
    apollo_lemma: int | None = Field(default=None, ge=1, le=1800)


class ProverTarget(ProverModel):
    name: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    kind: Literal["subgoal", "theorem_body", "apollo_lemma"]
    status: Literal["pending", "in_progress", "proved", "failed"] = "pending"
    recursion_depth: int = Field(default=0, ge=0, le=3)
    helper_theorem_name: str | None = None


class ProverTraceStep(ProverModel):
    turn: int = Field(ge=1)
    backend: str = Field(min_length=1)
    target_name: str = Field(min_length=1)
    target_kind: Literal["subgoal", "theorem_body", "apollo_lemma"] | None = None
    action_type: str = Field(min_length=1)
    success: bool
    rationale: str = ""
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    tool_result: str = ""
    lean_feedback: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    code_snapshot: str = ""
    decomposition_theorem: str | None = None
    error_code: str | None = None
    repl_local_solved: bool = False
    claim_type: Literal["preamble_definable", "mathlib_native"] | None = None
    claim_type_policy: str | None = None
    mathlib_native_mode: bool = False
    lsp_tool_call: bool = False
    native_search_attempt: bool = False


class ProverFailure(ProverModel):
    reason: str = Field(min_length=1)
    message: str = Field(min_length=1)
    error_code: str | None = None
    target_name: str | None = None
    turn: int | None = Field(default=None, ge=1)
    backend: str | None = None
    lean_feedback: list[str] = Field(default_factory=list)
    repeated_action: bool = False


class ProverResult(ProverModel):
    status: Literal["verified", "failed"]
    theorem_name: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    benchmark_mode: bool = False
    verified_via: Literal["full_pipeline", "trivial_shortcut"] = "full_pipeline"
    verified_code: str | None = None
    current_code: str = Field(min_length=1)
    trace: list[ProverTraceStep] = Field(default_factory=list)
    targets: list[ProverTarget] = Field(default_factory=list)
    failure: ProverFailure | None = None
    termination_reason: str = Field(min_length=1)
    repair_count: int = Field(default=0, ge=0)
    preamble_names: list[str] = Field(default_factory=list)
    backend_used: str = Field(min_length=1)
    attempted_backends: list[str] = Field(default_factory=list)
    tool_budget: dict[str, Any] = Field(default_factory=dict)
    telemetry: dict[str, float] = Field(default_factory=dict)
    usage_by_stage: dict[str, Any] = Field(default_factory=dict)
    timing_breakdown: dict[str, Any] = Field(default_factory=dict)
    target_timeouts: ProverTargetTimeouts = Field(default_factory=ProverTargetTimeouts)
    audit_summary: dict[str, Any] = Field(default_factory=dict)
    retrieval_events: list[dict[str, Any]] = Field(default_factory=list)
    tool_usage_traces: list[dict[str, Any]] = Field(default_factory=list)
    state_transitions: list[dict[str, Any]] = Field(default_factory=list)
    progress_deltas: list[dict[str, Any]] = Field(default_factory=list)
