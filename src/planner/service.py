"""Planner service facade."""

from __future__ import annotations

from src.planner.models import PlannerPacket
from src.planner.planner import Planner, PlannerDriver
from src.planner.retrieval import PlannerRetrievalService, TextEmbedder


class PlannerService:
    def __init__(
        self,
        *,
        backend: str | None = None,
        driver: PlannerDriver | None = None,
        retrieval_service: PlannerRetrievalService | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        planner_kwargs: dict[str, object] = {}
        if backend is not None:
            planner_kwargs["backend"] = backend
        if driver is not None:
            planner_kwargs["driver"] = driver
        if retrieval_service is not None:
            planner_kwargs["retrieval_service"] = retrieval_service
        if embedder is not None:
            planner_kwargs["embedder"] = embedder
        self._planner = Planner(**planner_kwargs)

    def build_plan(self, claim: str, *, benchmark_mode: bool = False) -> PlannerPacket:
        packet = self._planner.build_plan(claim, benchmark_mode=benchmark_mode)
        return packet.model_copy(
            update={
                "needs_review": not benchmark_mode,
                "review_state": "approved" if benchmark_mode else "awaiting_plan_review",
            }
        )
