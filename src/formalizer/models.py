"""Structured models for the v3 formalizer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_unique_strings(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


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
    claim_scope: Literal[
        "release_reliable",
        "supported_attempt",
        "frontier_collect",
        "out_of_scope",
    ] = "supported_attempt"
    claim_type: Literal["preamble_definable", "mathlib_native"] | None = None
    required_primitives: list[str] = Field(default_factory=list)
    theorem_shape_recommendation: str = ""
    assumption_audit: list[str] = Field(default_factory=list)
    scope_reason: str = ""
    imports: list[str] = Field(default_factory=list)
    open_statements: list[str] = Field(default_factory=list)
    preamble_entries: list[PreambleContextEntry] = Field(default_factory=list)

    @field_validator(
        "textbook_defaults",
        "planner_subgoals",
        "selected_preamble",
        "required_primitives",
        "assumption_audit",
        "imports",
        "open_statements",
    )
    @classmethod
    def _normalize_string_lists(cls, values: list[str]) -> list[str]:
        return _normalize_unique_strings(values)


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
    # Claim type is policy metadata for the prover, not a proof hint:
    # mathlib-native claims should not be closed by LeanEcon Preamble shortcuts.
    claim_type: Literal["preamble_definable", "mathlib_native"] | None = None
    claim_scope: Literal[
        "release_reliable",
        "supported_attempt",
        "frontier_collect",
        "out_of_scope",
    ] = "supported_attempt"
    required_primitives: list[str] = Field(default_factory=list)
    theorem_shape_recommendation: str = ""
    assumption_audit: list[str] = Field(default_factory=list)
    scope_reason: str = ""
    formalization_source: Literal["theorem_stub", "preamble_template", "llm_generation"] = "llm_generation"
    imports: list[str] = Field(default_factory=list)
    selected_imports: list[str] = Field(default_factory=list)
    open_statements: list[str] = Field(default_factory=list)
    subgoals: list[FormalizerSubgoal] = Field(default_factory=list)
    selected_preamble: list[str] = Field(default_factory=list)
    planner_plan_paragraph: str = ""
    planner_textbook_defaults: list[str] = Field(default_factory=list)
    planner_subgoals: list[str] = Field(default_factory=list)
    proof_sketch: dict[str, Any] | None = None
    vacuity: dict[str, Any]
    faithfulness: FaithfulnessAssessment
    parse_check: ParseCheck
    review_state: str
    backend: str
    provider: str
    model: str

    @field_validator(
        "required_primitives",
        "assumption_audit",
        "imports",
        "selected_imports",
        "open_statements",
        "selected_preamble",
        "planner_textbook_defaults",
        "planner_subgoals",
    )
    @classmethod
    def _normalize_string_lists(cls, values: list[str]) -> list[str]:
        return _normalize_unique_strings(values)

    @field_validator("review_state", "backend", "provider", "model")
    @classmethod
    def _validate_non_empty_metadata(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Formalization packet metadata values must be non-empty.")
        return cleaned
