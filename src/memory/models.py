"""Episodic memory models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ProofTraceSchema = (
    ("claim_id", "TEXT NOT NULL"),
    ("claim_text", "TEXT NOT NULL"),
    ("preamble_names_json", "TEXT NOT NULL"),
    ("tactic_sequence_json", "TEXT NOT NULL"),
    ("stage_outcomes_json", "TEXT NOT NULL"),
    ("failure_class", "TEXT"),
    ("repair_count", "INTEGER NOT NULL"),
    ("outcome", "TEXT NOT NULL"),
    ("formalizer_model", "TEXT NOT NULL"),
    ("timestamp", "TEXT NOT NULL"),
    ("lesson_summary", "TEXT"),
    ("full_trace_json", "TEXT"),
    ("prover_backend", "TEXT"),
    ("trace_metadata_json", "TEXT"),
)


@dataclass(frozen=True)
class ProofTrace:
    claim_id: str
    claim_text: str
    preamble_names: list[str]
    tactic_sequence: list[str]
    stage_outcomes: dict[str, str]
    failure_class: str | None
    repair_count: int
    outcome: str
    formalizer_model: str
    timestamp: str
    lesson_summary: str | None = None
    full_trace: dict[str, Any] | None = None
    prover_backend: str | None = None
    trace_metadata: dict[str, Any] | None = None
