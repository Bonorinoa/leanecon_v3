"""Structured planner models."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlannerModel(BaseModel):
    """Strict base model for planner data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PreambleHit(PlannerModel):
    name: str
    lean_module: str
    score: float = Field(ge=0)
    description: str
    concepts: list[str] = Field(default_factory=list)
    proven_lemmas: list[str] = Field(default_factory=list)
    tactic_hints: list[str] = Field(default_factory=list)
    textbook_source: str | None = None
    related: list[str] = Field(default_factory=list)


class MemoryTraceExample(PlannerModel):
    claim_text: str = Field(min_length=1)
    preamble_names: list[str] = Field(default_factory=list)
    shared_preamble_names: list[str] = Field(default_factory=list)
    structure_tags: list[str] = Field(default_factory=list)
    tactic_sequence: list[str] = Field(default_factory=list)
    outcome: str
    timestamp: str
    lesson: str = Field(min_length=1)
    subgoal_hint: str | None = None


class PlannerContext(PlannerModel):
    claim: str = Field(min_length=1)
    selected_preamble: list[PreambleHit] = Field(default_factory=list)
    few_shot_traces: list[MemoryTraceExample] = Field(default_factory=list, max_length=1)
    preamble_context: str = ""
    memory_context: str = ""


class PlannerLLMResponse(PlannerModel):
    clarifying_questions: list[str] = Field(default_factory=list, max_length=3)
    textbook_defaults: list[str] = Field(default_factory=list, min_length=1)
    plan_paragraph: str = Field(min_length=1)
    subgoals: list[str] = Field(min_length=1, max_length=6)
    needs_review: bool = True
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("clarifying_questions")
    @classmethod
    def _validate_questions(cls, questions: list[str]) -> list[str]:
        cleaned = [question.strip() for question in questions if question.strip()]
        if len(cleaned) > 3:
            raise ValueError("Planner may emit at most 3 clarifying questions.")
        for question in cleaned:
            if not question.endswith("?"):
                raise ValueError("Clarifying questions must be phrased as actionable questions.")
        return cleaned

    @field_validator("textbook_defaults")
    @classmethod
    def _validate_defaults(cls, defaults: list[str]) -> list[str]:
        cleaned = [default.strip() for default in defaults if default.strip()]
        if not cleaned:
            raise ValueError("Planner must emit at least one textbook default.")
        return cleaned

    @field_validator("plan_paragraph")
    @classmethod
    def _validate_paragraph(cls, paragraph: str) -> str:
        cleaned = paragraph.strip()
        if "\n\n" in cleaned:
            raise ValueError("Planner must emit exactly one paragraph.")
        return cleaned

    @field_validator("subgoals")
    @classmethod
    def _validate_subgoals(cls, subgoals: list[str]) -> list[str]:
        cleaned = [subgoal.rstrip() for subgoal in subgoals if subgoal.strip()]
        if not 1 <= len(cleaned) <= 6:
            raise ValueError("Planner must emit 1 to 6 subgoals.")
        return cleaned

class PlannerPacket(PlannerLLMResponse):
    claim: str = Field(min_length=1)
    review_state: str
    backend: str
    model: str
    selected_preamble: list[PreambleHit] = Field(default_factory=list)
    few_shot_traces: list[MemoryTraceExample] = Field(default_factory=list, max_length=1)

    @field_validator("review_state")
    @classmethod
    def _validate_review_state(cls, review_state: str) -> str:
        cleaned = review_state.strip()
        if not cleaned:
            raise ValueError("Review state must be non-empty.")
        return cleaned

    @field_validator("backend", "model")
    @classmethod
    def _validate_identity(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Planner backend/model values must be non-empty.")
        return cleaned


def slugify_claim(claim: str, *, prefix: str = "planner") -> str:
    """Create a stable theorem-style slug from a natural-language claim."""

    tokens = re.findall(r"[A-Za-z0-9]+", claim.lower())
    stem = "_".join(tokens[:8]) or "claim"
    return f"{prefix}_{stem}"[:72]
