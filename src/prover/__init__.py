"""Prover module — packet-aware Lean prover with self-correction.

The `Prover` orchestrates per-target proof attempts: it drives an LLM tactic
loop, classifies claims by type (preamble-definable vs mathlib-native), and
routes mathlib-native goals through a harness-owned hybrid retrieval path
(local RAG + LeanSearch via lean-lsp-mcp) before each tactic. Failure
pathways (LeanSearch 0-results, hallucinated identifiers, stalled progress)
are observable via `ToolUsageTrace`, `RetrievalEvent`, and
`LeanSearchFailureEvent` in `src.observability`.

Drivers (`HuggingFaceProverDriver`, `MistralProverDriver`) are pluggable;
all retrieval/budget/tactic-checking logic lives in the harness, not the
driver. Note: prover internals are off-limits during cleanup sessions —
behavioral changes belong in dedicated prover-focused sprints.
"""

from .memory_writer import ProverMemoryWriter
from .models import (
    ProverAction,
    ProverFailure,
    ProverResult,
    ProverTarget,
    ProverTargetTimeouts,
    ProverTraceStep,
)
from .prover import (
    DEFAULT_PROVER,
    DriverRegistry,
    HuggingFaceProverDriver,
    MistralProverDriver,
    Prover,
    ProverBackend,
    ProverDriver,
    ProverDriverError,
)
from .tactics import direct_hypothesis_name, suggest_fast_path_tactics

__all__ = [
    "DEFAULT_PROVER",
    "DriverRegistry",
    "HuggingFaceProverDriver",
    "MistralProverDriver",
    "Prover",
    "ProverAction",
    "ProverBackend",
    "ProverDriver",
    "ProverDriverError",
    "ProverFailure",
    "ProverMemoryWriter",
    "ProverResult",
    "ProverTarget",
    "ProverTargetTimeouts",
    "ProverTraceStep",
    "direct_hypothesis_name",
    "suggest_fast_path_tactics",
]
