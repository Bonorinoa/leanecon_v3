"""Planner module — claim decomposition + preamble retrieval.

The `Planner` consumes an informal economic claim and emits a
`PlannerPacket`: a structured plan with sub-targets, claim-type
classification, and matched preamble lemmas (`PreambleHit`s) sourced from
the curated `preamble_library` via `PlannerService`. Output feeds the
formalizer and prover stages.

Drivers (`HuggingFacePlannerDriver`, `MistralPlannerDriver`,
`OllamaPlannerDriver`) are pluggable. Retrieval uses pluggable embedders
(see `src.planner.retrieval.get_default_embedder`).
"""

from .models import MemoryTraceExample, PlannerContext, PlannerLLMResponse, PlannerPacket, PreambleHit
from .planner import (
    DriverRegistry,
    HuggingFacePlannerDriver,
    MistralPlannerDriver,
    OllamaPlannerDriver,
    Planner,
    PlannerBackend,
    PlannerDriverError,
)
from .service import PlannerService

__all__ = [
    "DriverRegistry",
    "HuggingFacePlannerDriver",
    "MistralPlannerDriver",
    "OllamaPlannerDriver",
    "MemoryTraceExample",
    "Planner",
    "PlannerBackend",
    "PlannerContext",
    "PlannerDriverError",
    "PlannerLLMResponse",
    "PlannerPacket",
    "PlannerService",
    "PreambleHit",
]
