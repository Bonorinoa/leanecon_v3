"""Provider normalization helpers for hosted inference backends."""

from __future__ import annotations


def normalize_huggingface_provider(provider: str) -> str:
    normalized = provider.strip()
    if normalized in {"", "auto", "huggingface"}:
        return "auto"
    return normalized


def is_provider_pinned(platform: str, provider: str) -> bool:
    if platform == "huggingface":
        return normalize_huggingface_provider(provider) != "auto"
    normalized = provider.strip()
    return normalized not in {"", "auto"}
