"""Structured models for the v3 formalizer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FormalizerModel(BaseModel):
    """Strict base model for formalizer data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FormalizerSubgoal(FormalizerModel):
    name: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    rationale: str | None = None

    @field_validator("statement")
    @classmethod
    def _normalize_statement(cls, value: str) -> str:
        return value.strip()


class PreambleContextEntry(FormalizerModel):
    name: str
    lean_module: str
    description: str
    definitions: list[str] = Field(default_factory=list)
    definition_signatures: list[str] = Field(default_factory=list)
    proven_lemmas: list[str] = Field(default_factory=list)
    theorem_template: str | None = None
    tactic_hints: list[str] = Field(default_factory=list)
    textbook_source: str | None = None
    related: list[str] = Field(default_factory=list)
    source_excerpt: str = ""


class FormalizerContext(FormalizerModel):
    claim: str = Field(min_length=1)
    plan_paragraph: str = ""
    textbook_defaults: list[str] = Field(default_factory=list)
    planner_subgoals: list[str] = Field(default_factory=list)
    selected_preamble: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    open_statements: list[str] = Field(default_factory=list)
    preamble_entries: list[PreambleContextEntry] = Field(default_factory=list)


class FormalizerGenerationResponse(FormalizerModel):
    theorem_name: str = Field(min_length=1)
    theorem_docstring: str = Field(min_length=1)
    theorem_statement: str = Field(min_length=1)
    open_statements: list[str] = Field(default_factory=list)
    subgoals: list[FormalizerSubgoal] = Field(default_factory=list)
    final_expression: str | None = None

    @field_validator("open_statements")
    @classmethod
    def _normalize_open_statements(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]

    @field_validator("subgoals")
    @classmethod
    def _validate_subgoals(cls, values: list[FormalizerSubgoal]) -> list[FormalizerSubgoal]:
        if not values:
            raise ValueError("Formalizer must emit at least one subgoal.")
        return values


class FaithfulnessAssessment(FormalizerModel):
    score: float = Field(ge=0, le=5)
    coverage: float = Field(ge=0, le=1)
    structural_isomorphism: float = Field(ge=0, le=1)
    primitive_faithfulness: float = Field(ge=0, le=1)
    claim_frame: dict[str, Any]
    stub_frame: dict[str, Any]
    needs_human_review: bool
    passes_gate: bool
    feedback: list[str] = Field(default_factory=list)


class ParseCheck(FormalizerModel):
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class FormalizationPacket(FormalizerModel):
    claim: str = Field(min_length=1)
    lean_code: str = Field(min_length=1)
    theorem_with_sorry: str = Field(min_length=1)
    theorem_name: str = Field(min_length=1)
    imports: list[str] = Field(default_factory=list)
    selected_imports: list[str] = Field(default_factory=list)
    open_statements: list[str] = Field(default_factory=list)
    subgoals: list[FormalizerSubgoal] = Field(default_factory=list)
    selected_preamble: list[str] = Field(default_factory=list)
    vacuity: dict[str, Any]
    faithfulness: FaithfulnessAssessment
    parse_check: ParseCheck
    review_state: str
    backend: str
    provider: str
    model: str
