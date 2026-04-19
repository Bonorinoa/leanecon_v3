"""Semantic retrieval stub for episodic traces."""

from __future__ import annotations

from src.memory.models import ProofTrace


def retrieve_similar_traces(
    traces: list[ProofTrace],
    *,
    query: str,
    limit: int = 3,
) -> list[ProofTrace]:
    query_tokens = set(query.lower().split())
    ranked = sorted(
        traces,
        key=lambda trace: len(query_tokens & set(trace.claim_text.lower().split())),
        reverse=True,
    )
    return ranked[:limit]
