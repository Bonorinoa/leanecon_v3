"""Token counting and price estimation helpers."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from src.config import COST_TRACKING_ENABLED, PRICE_OVERRIDES
from src.observability.models import ProviderCallMetadata, TokenUsage

_BUILTIN_PRICING: dict[str, dict[str, dict[str, float]]] = {
    "huggingface": {
        "OBLITERATUS/gemma-4-E4B-it-OBLITERATED": {
            "input_per_million": 0.0005,
            "output_per_million": 0.0005,
        },
        "MiniMaxAI/MiniMax-M2.7": {
            "input_per_million": 0.0005,
            "output_per_million": 0.0005,
        },
        "Goedel-LM/Goedel-Prover-V2-32B": {
            "input_per_million": 0.0004,
            "output_per_million": 0.0004,
        },
    },
    "mistral": {
        "mistral-large-2512": {
            "input_per_million": 0.5,
            "output_per_million": 1.5,
        },
        "mistral-large-latest": {
            "input_per_million": 0.5,
            "output_per_million": 1.5,
        },
        "labs-leanstral-2603": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
        },
    },
    # Ollama Cloud publishes subscription / usage-tier pricing rather than
    # stable per-token public rates. We track these as zero-cost sentinels so
    # benchmark readiness can treat the model as price-known.
    "ollama": {
        "gemma4:31b": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
        },
        "gemma4:31b-cloud": {
            "input_per_million": 0.0,
            "output_per_million": 0.0,
        },
    },
}


def stable_hash_text(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def estimate_tokens_from_text(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return 0
    return max(1, math.ceil(len(stripped) / 4))


def normalize_price_overrides(raw: Any) -> dict[str, dict[str, dict[str, float]]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, dict[str, float]]] = {}
    for provider, models in raw.items():
        if not isinstance(provider, str) or not isinstance(models, dict):
            continue
        provider_bucket = normalized.setdefault(provider.strip(), {})
        for model, payload in models.items():
            if not isinstance(model, str) or not isinstance(payload, dict):
                continue
            try:
                # Accept either explicit per-million keys or the shorter input/output aliases
                # used in the repo's env examples.
                input_price = float(payload.get("input_per_million", payload["input"]))
                output_price = float(payload.get("output_per_million", payload["output"]))
            except (KeyError, TypeError, ValueError):
                continue
            provider_bucket[model.strip()] = {
                "input_per_million": input_price,
                "output_per_million": output_price,
            }
    return normalized


PRICE_REGISTRY = normalize_price_overrides(PRICE_OVERRIDES)


def lookup_pricing(provider: str, model: str) -> dict[str, float] | None:
    for source in (PRICE_REGISTRY, _BUILTIN_PRICING):
        provider_entry = source.get(provider)
        if provider_entry and model in provider_entry:
            return provider_entry[model]
    return None


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    if not COST_TRACKING_ENABLED:
        return None
    pricing = lookup_pricing(provider, model)
    if pricing is None:
        return None
    return ((input_tokens or 0) / 1_000_000.0) * pricing["input_per_million"] + (
        (output_tokens or 0) / 1_000_000.0
    ) * pricing["output_per_million"]


def complete_usage(
    *,
    stage: str,
    provider: str,
    model: str,
    latency_ms: float | None,
    success: bool,
    error_code: str | None = None,
    metadata: ProviderCallMetadata | None = None,
    prompt_text: str | None = None,
    response_text: str | None = None,
) -> TokenUsage:
    resolved_prompt = (
        metadata.prompt_text if metadata and metadata.prompt_text is not None else prompt_text
    )
    resolved_response = (
        metadata.response_text if metadata and metadata.response_text is not None else response_text
    )
    input_tokens = (
        metadata.input_tokens
        if metadata and metadata.input_tokens is not None
        else estimate_tokens_from_text(resolved_prompt)
    )
    output_tokens = (
        metadata.output_tokens
        if metadata and metadata.output_tokens is not None
        else estimate_tokens_from_text(resolved_response)
    )
    usage_source = metadata.usage_source if metadata is not None else "estimated_chars"
    if metadata is None:
        usage_source = "estimated_chars"
    estimated_cost = estimate_cost_usd(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return TokenUsage(
        stage=stage,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost,
        latency_ms=latency_ms,
        success=success,
        usage_source=usage_source,
        error_code=error_code,
    )


def dump_pricing_registry() -> dict[str, dict[str, dict[str, float]]]:
    return json.loads(json.dumps({**_BUILTIN_PRICING, **PRICE_REGISTRY}))
