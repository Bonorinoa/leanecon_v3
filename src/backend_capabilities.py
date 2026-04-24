"""Central backend capability metadata for runtime status and benchmark manifests."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class BackendCapability:
    stage: str
    backend: str
    provider: str
    transport: str
    structured_output: bool
    production_supported: bool
    benchmark_supported: bool
    experimental: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BACKEND_CAPABILITIES: dict[tuple[str, str], BackendCapability] = {
    ("planner", "mistral-structured"): BackendCapability(
        stage="planner",
        backend="mistral-structured",
        provider="mistral",
        transport="HTTP /chat/completions",
        structured_output=True,
        production_supported=True,
        benchmark_supported=True,
        notes="Primary hosted planner path via Mistral structured chat completions.",
    ),
    ("planner", "hf-structured"): BackendCapability(
        stage="planner",
        backend="hf-structured",
        provider="huggingface",
        transport="InferenceClient.chat_completion|text_generation",
        structured_output=True,
        production_supported=True,
        benchmark_supported=True,
        notes="Primary open-model planner path.",
    ),
    ("planner", "ollama-cloud"): BackendCapability(
        stage="planner",
        backend="ollama-cloud",
        provider="ollama",
        transport="HTTP /api/chat",
        structured_output=True,
        production_supported=False,
        benchmark_supported=True,
        experimental=True,
        notes="Useful for research and local benchmarking, but not yet treated as Railway production-ready.",
    ),
    ("formalizer", "leanstral"): BackendCapability(
        stage="formalizer",
        backend="leanstral",
        provider="mistral",
        transport="HTTP /chat/completions",
        structured_output=True,
        production_supported=True,
        benchmark_supported=True,
        notes="Primary formalizer path in the current repo.",
    ),
    ("formalizer", "goedel-prover-v2"): BackendCapability(
        stage="formalizer",
        backend="goedel-prover-v2",
        provider="huggingface",
        transport="InferenceClient.chat_completion|text_generation",
        structured_output=True,
        production_supported=False,
        benchmark_supported=True,
        experimental=True,
        notes="Alternative open formalizer path; not treated as deployment-default.",
    ),
    ("prover", "goedel-prover-v2"): BackendCapability(
        stage="prover",
        backend="goedel-prover-v2",
        provider="huggingface",
        transport="InferenceClient.chat_completion|text_generation",
        structured_output=True,
        production_supported=True,
        benchmark_supported=True,
        notes="Primary open prover path.",
    ),
    ("prover", "leanstral"): BackendCapability(
        stage="prover",
        backend="leanstral",
        provider="mistral",
        transport="HTTP /chat/completions",
        structured_output=True,
        production_supported=True,
        benchmark_supported=True,
        notes="Fallback prover path.",
    ),
}


EXPERIMENTAL_RUNTIME_CAPABILITIES: dict[str, dict[str, Any]] = {
    "memory": {
        "name": "memory",
        "experimental": True,
        "production_supported": False,
        "notes": "Episodic memory is preserved for research but excluded from readiness claims.",
    },
    "lean_lsp": {
        "name": "lean_lsp",
        "experimental": False,
        "production_supported": True,
        "notes": "lean-lsp-mcp is part of the mathlib-native proving path and exposes goal, diagnostic, code-action, hover, LeanSearch, and Loogle tools.",
    },
}


def get_backend_capability(stage: str, backend: str) -> dict[str, Any]:
    capability = BACKEND_CAPABILITIES.get((stage, backend))
    if capability is None:
        return {
            "stage": stage,
            "backend": backend,
            "provider": "unknown",
            "transport": "unknown",
            "structured_output": False,
            "production_supported": False,
            "benchmark_supported": False,
            "experimental": True,
            "notes": "No centralized capability metadata is defined for this backend.",
        }
    return capability.to_dict()


def active_backend_capabilities(
    *,
    planner_backend: str,
    formalizer_backend: str,
    prover_backend: str,
) -> dict[str, dict[str, Any]]:
    return {
        "planner": get_backend_capability("planner", planner_backend),
        "formalizer": get_backend_capability("formalizer", formalizer_backend),
        "prover": get_backend_capability("prover", prover_backend),
        "experimental_runtime": dict(EXPERIMENTAL_RUNTIME_CAPABILITIES),
    }
