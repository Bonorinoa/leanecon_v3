"""Planner orchestration and model drivers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from src.config import HF_TOKEN, PLANNER_BACKEND, PLANNER_MODEL
from src.planner.models import PlannerLLMResponse, PlannerPacket
from src.planner.prompts import build_system_prompt, build_user_prompt
from src.planner.retrieval import PlannerRetrievalService, TextEmbedder


class PlannerDriverError(RuntimeError):
    """Raised when the planner driver cannot complete a request."""


@dataclass(frozen=True)
class PlannerBackend:
    name: str
    model: str
    notes: str


class PlannerDriver(Protocol):
    def generate(
        self,
        *,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> PlannerLLMResponse:
        """Return a validated planner response."""


class DriverRegistry:
    def __init__(self) -> None:
        self._backends = {
            "minimax-m2.7": PlannerBackend(
                "minimax-m2.7",
                "MiniMaxAI/MiniMax-M2.7",
                "Primary HILBERT planner backend.",
            ),
            "trinity-large-thinking": PlannerBackend(
                "trinity-large-thinking",
                "arcee-ai/Trinity-Large-Thinking",
                "Alternative reasoning-heavy planner backend.",
            ),
            "gemma-4-31b-it": PlannerBackend(
                "gemma-4-31b-it",
                "google/gemma-4-31B-it",
                "Fallback open planner backend.",
            ),
        }

    def get(self, name: str) -> PlannerBackend:
        return self._backends[name]

    def names(self) -> list[str]:
        return sorted(self._backends)


def _extract_json_payload(raw_text: str) -> dict[str, object]:
    stripped = raw_text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise PlannerDriverError("Planner backend did not return a JSON object.")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise PlannerDriverError(f"Planner backend returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise PlannerDriverError("Planner backend returned non-object JSON.")
    return payload


class HuggingFacePlannerDriver:
    """HF text-generation backed planner driver."""

    def __init__(self, *, token: str = HF_TOKEN, timeout: float = 120.0) -> None:
        self.token = token
        self.timeout = timeout

    def generate(
        self,
        *,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> PlannerLLMResponse:
        try:
            from huggingface_hub import InferenceClient
        except Exception as error:
            raise PlannerDriverError("huggingface_hub is required for the planner backend.") from error

        prompt = "\n\n".join(
            [
                "<system>",
                system_prompt,
                "</system>",
                "<user>",
                user_prompt,
                "</user>",
            ]
        )
        try:
            client = InferenceClient(model=backend.model, token=self.token, timeout=self.timeout)
            raw_text = client.text_generation(
                prompt,
                max_new_tokens=1200,
                temperature=0.2,
                return_full_text=False,
            )
        except Exception as error:
            raise PlannerDriverError(f"Planner backend invocation failed for {backend.model}: {error}") from error

        payload = _extract_json_payload(str(raw_text))
        try:
            return PlannerLLMResponse.model_validate(payload)
        except Exception as error:
            raise PlannerDriverError(f"Planner backend returned schema-invalid JSON: {error}") from error


class Planner:
    """Orchestrates retrieval, prompting, model invocation, and packet assembly."""

    def __init__(
        self,
        *,
        backend: str = PLANNER_BACKEND,
        driver: PlannerDriver | None = None,
        retrieval_service: PlannerRetrievalService | None = None,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self.registry = DriverRegistry()
        self.backend = self.registry.get(backend)
        self.driver = driver or HuggingFacePlannerDriver()
        self.retrieval_service = retrieval_service or PlannerRetrievalService(embedder=embedder)
        self.system_prompt = build_system_prompt()

    def build_plan(self, claim: str, *, benchmark_mode: bool = False) -> PlannerPacket:
        context = self.retrieval_service.build_context(claim)
        response = self.driver.generate(
            backend=self.backend,
            system_prompt=self.system_prompt,
            user_prompt=build_user_prompt(claim, context),
        )
        return PlannerPacket(
            claim=claim,
            clarifying_questions=response.clarifying_questions,
            textbook_defaults=response.textbook_defaults,
            plan_paragraph=response.plan_paragraph,
            subgoals=response.subgoals,
            needs_review=(not benchmark_mode) or response.needs_review,
            review_state="approved" if benchmark_mode else "awaiting_plan_review",
            backend=self.backend.name,
            model=self.backend.model or PLANNER_MODEL,
            selected_preamble=context.selected_preamble,
            few_shot_traces=context.few_shot_traces,
        )
