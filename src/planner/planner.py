"""Planner orchestration and model drivers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Protocol

from src.config import HF_TOKEN, PLANNER_BACKEND, PLANNER_MODEL, PLANNER_PROVIDER
from src.observability.errors import classify_exception
from src.observability.models import ProviderCallMetadata
from src.planner.models import PlannerContext, PlannerLLMResponse, PlannerPacket, slugify_claim
from src.planner.prompts import build_system_prompt, build_user_prompt
from src.planner.retrieval import PlannerRetrievalService, TextEmbedder, infer_structure_tags
from src.providers import normalize_huggingface_provider


PLANNER_RETRY_ATTEMPTS = 3
PLANNER_RETRY_BACKOFF_SECONDS = (0.5, 1.0)
PLANNER_RETRYABLE_ERROR_CODES = frozenset({"schema_invalid", "rate_limit", "provider_http_error", "provider_unavailable"})


class PlannerDriverError(RuntimeError):
    """Raised when the planner driver cannot complete a request."""


@dataclass(frozen=True)
class PlannerBackend:
    name: str
    model: str
    provider: str
    notes: str


class PlannerDriver(Protocol):
    def generate(
        self,
        *,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> PlannerLLMResponse | tuple[PlannerLLMResponse, ProviderCallMetadata]:
        """Return a validated planner response."""


class DriverRegistry:
    def __init__(self) -> None:
        self._backends = {
            "hf-structured": PlannerBackend(
                "hf-structured",
                PLANNER_MODEL,
                PLANNER_PROVIDER,
                "Primary structured-output planner backend via Hugging Face Inference Providers.",
            ),
        }
        self._aliases = {
            "minimax-m2.7": "hf-structured",
            "trinity-large-thinking": "hf-structured",
            "gemma-4-31b-it": "hf-structured",
        }

    def get(self, name: str) -> PlannerBackend:
        return self._backends[self._aliases.get(name, name)]

    def names(self) -> list[str]:
        return sorted({*self._backends, *self._aliases})


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


def _unwrap_driver_response(
    value: PlannerLLMResponse | tuple[PlannerLLMResponse, ProviderCallMetadata],
) -> tuple[PlannerLLMResponse, ProviderCallMetadata | None]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], ProviderCallMetadata):
        return value[0], value[1]
    return value, None


def _planner_response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "PlannerLLMResponse",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "clarifying_questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                    "textbook_defaults": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "plan_paragraph": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "subgoals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 6,
                    },
                    "needs_review": {"type": "boolean"},
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                },
                "required": [
                    "clarifying_questions",
                    "textbook_defaults",
                    "plan_paragraph",
                    "subgoals",
                    "needs_review",
                    "confidence",
                ],
            },
            "strict": True,
        },
    }


class HuggingFacePlannerDriver:
    """HF text-generation backed planner driver."""

    def __init__(
        self,
        *,
        token: str = HF_TOKEN,
        timeout: float = 120.0,
        provider: str = PLANNER_PROVIDER,
    ) -> None:
        self.token = token
        self.timeout = timeout
        self.provider = provider

    @property
    def inference_provider(self) -> str:
        return normalize_huggingface_provider(self.provider)

    def _chat_completion(
        self,
        *,
        client,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[PlannerLLMResponse, ProviderCallMetadata]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        raw = client.chat_completion(
            messages,
            max_tokens=1200,
            temperature=0.2,
            response_format=_planner_response_format(),
        )
        content = raw.choices[0].message.content
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise PlannerDriverError("Planner chat-completion response did not contain text content.")
        payload = _extract_json_payload(content)
        try:
            response = PlannerLLMResponse.model_validate(payload)
        except Exception as error:
            raise PlannerDriverError(f"Planner backend returned schema-invalid JSON: {error}") from error
        usage = getattr(raw, "usage", None)
        metadata = ProviderCallMetadata(
            input_tokens=int(usage.prompt_tokens) if getattr(usage, "prompt_tokens", None) is not None else None,
            output_tokens=int(usage.completion_tokens) if getattr(usage, "completion_tokens", None) is not None else None,
            usage_source="provider" if usage is not None else "estimated_chars",
            prompt_text=json.dumps(messages, ensure_ascii=True),
            response_text=content,
        )
        return response, metadata

    def _text_generation(
        self,
        *,
        client,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[PlannerLLMResponse, ProviderCallMetadata]:
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
        raw_text = client.text_generation(
            prompt,
            max_new_tokens=1200,
            temperature=0.2,
            return_full_text=False,
            details=True,
            decoder_input_details=True,
        )
        generated_text = getattr(raw_text, "generated_text", None)
        details = getattr(raw_text, "details", None)
        response_text = str(generated_text if generated_text is not None else raw_text)
        payload = _extract_json_payload(response_text)
        try:
            response = PlannerLLMResponse.model_validate(payload)
        except Exception as error:
            raise PlannerDriverError(f"Planner backend returned schema-invalid JSON: {error}") from error
        metadata = ProviderCallMetadata(
            input_tokens=len(getattr(details, "prefill", []) or []) if details is not None else None,
            output_tokens=getattr(details, "generated_tokens", None) if details is not None else None,
            usage_source="provider" if details is not None else "estimated_chars",
            prompt_text=prompt,
            response_text=response_text,
        )
        return response, metadata

    def _should_fallback_to_text_generation(self, error: Exception) -> bool:
        message = str(error).lower()
        return "supported task: text-generation" in message or "supported task: text generation" in message

    def generate(
        self,
        *,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> PlannerLLMResponse | tuple[PlannerLLMResponse, ProviderCallMetadata]:
        try:
            from huggingface_hub import InferenceClient
        except Exception as error:
            raise PlannerDriverError("huggingface_hub is required for the planner backend.") from error

        try:
            client = InferenceClient(
                model=backend.model,
                token=self.token,
                timeout=self.timeout,
                provider=self.inference_provider,
            )
            try:
                return self._chat_completion(
                    client=client,
                    backend=backend,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except Exception as error:
                if not self._should_fallback_to_text_generation(error):
                    raise
                return self._text_generation(
                    client=client,
                    backend=backend,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
        except Exception as error:
            raise PlannerDriverError(f"Planner backend invocation failed for {backend.model}: {error}") from error


def _is_generic_subgoal(subgoal: str) -> bool:
    normalized = " ".join(subgoal.lower().split())
    return ": true := by" in normalized or " theorem " not in f" {normalized}"


def _dedupe_subgoals(subgoals: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for subgoal in subgoals:
        key = " ".join(subgoal.split())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(subgoal)
    return deduped


def _fallback_subgoals(claim: str, context: PlannerContext) -> list[str]:
    slug = slugify_claim(claim)
    selected_names = [hit.name for hit in context.selected_preamble]
    lines: list[str] = []
    for index, hit in enumerate(context.selected_preamble[:4], start=1):
        theorem_name = f"{slug}_{hit.name}_subgoal_{index}"
        if hit.name == "contraction_mapping":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{V : Type*}} [MetricSpace V] (T : V → V) :",
                        "    IsContraction T := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "fixed_point_theorem":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{V : Type*}} [MetricSpace V] [CompleteSpace V] [Nonempty V]",
                        "    {K : NNReal} {T : V → V} (hT : ContractingWith K T) :",
                        "    ∃ x, Function.IsFixedPt T x := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "bellman_operator":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{S : Type*}} (reward : S → ℝ) (transition : S → S) (β : ℝ) :",
                        "    ∃ T : (S → ℝ) → (S → ℝ), T = BellmanOperator reward transition β := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "value_function":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{V : Type*}} [MetricSpace V] [CompleteSpace V] [Nonempty V]",
                        "    {K : NNReal} (T : V → V) (hT : ContractingWith K T) :",
                        "    Function.IsFixedPt T (ValueFunction T hT) := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "nash_existence":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{Profile : Type}} (h : HasNashEquilibrium Profile) :",
                        "    ∃ profile, h.isNash profile := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "constrained_optimization":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{α : Type*}} (f : α → ℝ) (feasible : Set α) (x : α) :",
                        "    IsConstrainedMaximum f feasible x := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "kuhn_tucker":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{α ι : Type*}} (x : α) (g : α → ι → ℝ) (μ : ι → ℝ) :",
                        "    KuhnTuckerPoint x g μ := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "continuous_preference":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{α : Type*}} [TopologicalSpace α] [TopologicalSpace ℝ] (u : α → ℝ) :",
                        "    ContinuousPreference u := by",
                        "  sorry",
                    ]
                )
            )
        elif hit.name == "convex_preference":
            lines.append(
                "\n".join(
                    [
                        f"theorem {theorem_name} {{E : Type*}} (u : E → ℝ) :",
                        "    ConvexPreference u := by",
                        "  sorry",
                    ]
                )
            )
    if not lines:
        theorem_name = f"{slug}_structure_subgoal"
        concept = selected_names[0] if selected_names else "economic_claim"
        lines.append(
            "\n".join(
                [
                    f"theorem {theorem_name} :",
                    f"    Prop := by  -- organize the {concept} formalization around explicit hypotheses",
                    "  sorry",
                ]
            )
        )
    return lines[:6]


def _bellman_subgoals(claim: str, context: PlannerContext) -> list[str]:
    slug = slugify_claim(claim, prefix="bellman")
    subgoals = [
        "\n".join(
            [
                f"theorem {slug}_subgoal_1 {{S : Type*}} (reward : S → ℝ) (transition : S → S) (β : ℝ) :",
                "    ∃ T : (S → ℝ) → (S → ℝ), T = BellmanOperator reward transition β := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_2 {{S : Type*}} {{reward : S → ℝ}} {{transition : S → S}} {{β : ℝ}}",
                "    (hβ : 0 ≤ β) {v w : S → ℝ} (hvw : ∀ s, v s ≤ w s) :",
                "    ∀ s, BellmanOperator reward transition β v s ≤ BellmanOperator reward transition β w s := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_3 {{S : Type*}} [MetricSpace (S → ℝ)]",
                "    (reward : S → ℝ) (transition : S → S) (β : ℝ) :",
                "    IsContraction (BellmanOperator reward transition β) := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_4 {{S : Type*}} [MetricSpace (S → ℝ)] [CompleteSpace (S → ℝ)] [Nonempty (S → ℝ)]",
                "    (reward : S → ℝ) (transition : S → S) (β : ℝ)",
                "    (hT : IsContraction (BellmanOperator reward transition β)) :",
                "    ∃ v, Function.IsFixedPt (BellmanOperator reward transition β) v := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_5 {{S : Type*}} [MetricSpace (S → ℝ)] [CompleteSpace (S → ℝ)] [Nonempty (S → ℝ)]",
                "    {K : NNReal} (reward : S → ℝ) (transition : S → S) (β : ℝ)",
                "    (hK : ContractingWith K (BellmanOperator reward transition β)) :",
                "    Function.IsFixedPt (BellmanOperator reward transition β)",
                "      (ValueFunction (BellmanOperator reward transition β) hK) := by",
                "  sorry",
            ]
        ),
    ]
    if context.few_shot_traces:
        subgoals.append(
            "\n".join(
                [
                    f"theorem {slug}_subgoal_6 {{S : Type*}} {{reward : S → ℝ}} {{transition : S → S}} {{β : ℝ}} :",
                    "    ∀ v w : S → ℝ, BellmanOperator reward transition β v = BellmanOperator reward transition β w → v = w := by",
                    "  sorry",
                ]
            )
        )
    return subgoals[:6]


def _equilibrium_subgoals(claim: str) -> list[str]:
    slug = slugify_claim(claim, prefix="equilibrium")
    return [
        "\n".join(
            [
                f"theorem {slug}_subgoal_1 {{Profile : Type}} :",
                "    ∃ h : HasNashEquilibrium Profile, h.isNash h.witness := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_2 {{Profile : Type}} (h : HasNashEquilibrium Profile) :",
                "    ∃ profile, h.isNash profile := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_3 {{α : Type*}} [MetricSpace α] [CompleteSpace α] [Nonempty α]",
                "    {K : NNReal} {f : α → α} (hf : ContractingWith K f) :",
                "    ∃ x, Function.IsFixedPt f x := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_4 {{Profile : Type}} :",
                "    Prop := by  -- translate the economic equilibrium statement into a witness-based Lean target",
                "  sorry",
            ]
        ),
    ]


def _optimization_subgoals(claim: str) -> list[str]:
    slug = slugify_claim(claim, prefix="optimization")
    return [
        "\n".join(
            [
                f"theorem {slug}_subgoal_1 {{α : Type*}} (f : α → ℝ) (feasible : Set α) (x : α) :",
                "    IsConstrainedMaximum f feasible x := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_2 {{α ι : Type*}} (x : α) (g : α → ι → ℝ) (μ : ι → ℝ) :",
                "    KuhnTuckerPoint x g μ := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_3 {{α : Type*}} (f : α → ℝ) (feasible : Set α) (x y : α)",
                "    (hx : IsConstrainedMaximum f feasible x) (hy : y ∈ feasible) :",
                "    f y ≤ f x := by",
                "  sorry",
            ]
        ),
        "\n".join(
            [
                f"theorem {slug}_subgoal_4 {{α ι : Type*}} (x : α) (g : α → ι → ℝ) (μ : ι → ℝ)",
                "    (hkt : KuhnTuckerPoint x g μ) :",
                "    Prop := by  -- isolate complementary slackness or multiplier conditions",
                "  sorry",
            ]
        ),
    ]


def _synthesize_subgoals(claim: str, context: PlannerContext) -> list[str]:
    selected_names = {hit.name for hit in context.selected_preamble}
    tags = set(infer_structure_tags(claim, preamble_names=list(selected_names)))
    if "bellman_operator" in selected_names or ("bellman" in tags and "contraction" in tags):
        return _bellman_subgoals(claim, context)
    if "nash_existence" in selected_names or "equilibrium" in tags:
        return _equilibrium_subgoals(claim)
    if "constrained_optimization" in selected_names or "kuhn_tucker" in selected_names or "optimization" in tags:
        return _optimization_subgoals(claim)
    return _fallback_subgoals(claim, context)


def _subgoals_need_upgrade(subgoals: list[str]) -> bool:
    return any(
        _is_generic_subgoal(subgoal)
        or "theorem " not in subgoal
        or ":= by" not in subgoal
        or "sorry" not in subgoal
        for subgoal in subgoals
    )


def _calibrate_confidence(
    response: PlannerLLMResponse,
    context: PlannerContext,
    *,
    upgraded_subgoals: bool,
) -> float:
    confidence = float(response.confidence)
    if response.clarifying_questions:
        confidence -= 0.2
    if not context.selected_preamble:
        confidence -= 0.2
    elif len(context.selected_preamble) >= 3:
        confidence += 0.05
    if context.few_shot_traces:
        confidence += 0.05
    else:
        confidence -= 0.05
    if upgraded_subgoals:
        confidence = min(confidence, 0.82)
    return round(min(max(confidence, 0.0), 1.0), 3)


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

    def build_plan_with_metadata(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        benchmark_mode: bool = False,
    ) -> tuple[PlannerPacket, ProviderCallMetadata | None]:
        context = self.retrieval_service.build_context(claim)
        user_prompt = build_user_prompt(
            claim,
            context,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
        )
        response, metadata = self._generate_with_retry(user_prompt)
        stub_authoritative = bool(theorem_stub and theorem_stub.strip())
        if stub_authoritative:
            subgoals = _dedupe_subgoals(response.subgoals)[:6]
            upgraded_subgoals = False
        else:
            upgraded_subgoals = _subgoals_need_upgrade(response.subgoals)
            subgoals = _dedupe_subgoals(
                _synthesize_subgoals(claim, context) if upgraded_subgoals else response.subgoals
            )[:6]
        return (
            PlannerPacket(
                claim=claim,
                clarifying_questions=response.clarifying_questions,
                textbook_defaults=response.textbook_defaults,
                plan_paragraph=response.plan_paragraph,
                subgoals=subgoals,
                needs_review=(not benchmark_mode) or response.needs_review,
                confidence=_calibrate_confidence(response, context, upgraded_subgoals=upgraded_subgoals),
                review_state="approved" if benchmark_mode else "awaiting_plan_review",
                backend=self.backend.name,
                model=self.backend.model or PLANNER_MODEL,
                selected_preamble=context.selected_preamble,
                few_shot_traces=context.few_shot_traces,
            ),
            metadata,
        )

    def _generate_with_retry(
        self, user_prompt: str
    ) -> tuple[PlannerLLMResponse, ProviderCallMetadata | None]:
        last_error: Exception | None = None
        for attempt in range(PLANNER_RETRY_ATTEMPTS):
            try:
                return _unwrap_driver_response(
                    self.driver.generate(
                        backend=self.backend,
                        system_prompt=self.system_prompt,
                        user_prompt=user_prompt,
                    )
                )
            except Exception as exc:
                last_error = exc
                if classify_exception(exc) not in PLANNER_RETRYABLE_ERROR_CODES:
                    raise
                if attempt + 1 >= PLANNER_RETRY_ATTEMPTS:
                    break
                backoff = PLANNER_RETRY_BACKOFF_SECONDS[min(attempt, len(PLANNER_RETRY_BACKOFF_SECONDS) - 1)]
                time.sleep(backoff)
        assert last_error is not None
        raise last_error

    def build_plan(
        self,
        claim: str,
        *,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        benchmark_mode: bool = False,
    ) -> PlannerPacket:
        packet, _ = self.build_plan_with_metadata(
            claim,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
            benchmark_mode=benchmark_mode,
        )
        return packet
