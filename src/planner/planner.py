"""Planner orchestration and model drivers."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import time
from typing import Protocol
from urllib import error as urllib_error
from urllib.parse import urlparse
from urllib import request as urllib_request

from src.config import HF_TOKEN, OLLAMA_API_KEY, OLLAMA_HOST, PLANNER_BACKEND, PLANNER_MODEL, PLANNER_PROVIDER, PLANNER_TIMEOUT
from src.observability.errors import classify_exception
from src.observability.models import ProviderCallMetadata
from src.planner.models import PlannerContext, PlannerLLMResponse, PlannerPacket, slugify_claim
from src.planner.prompts import build_system_prompt, build_user_prompt
from src.planner.retrieval import PlannerRetrievalService, TextEmbedder
from src.providers import normalize_huggingface_provider


PLANNER_RETRY_ATTEMPTS = 3
PLANNER_RETRY_BACKOFF_SECONDS = (0.5, 1.0)
PLANNER_RETRYABLE_ERROR_CODES = frozenset({"rate_limit", "provider_http_error", "provider_unavailable", "timeout"})
PLANNER_MAX_OUTPUT_TOKENS = 500
PLANNER_REPAIR_DEFAULT = "Standard PhD-level assumptions (MWG/SLP continuous/bounded return, β∈(0,1), complete metric spaces)"


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
            "ollama-cloud": PlannerBackend(
                "ollama-cloud",
                PLANNER_MODEL,
                PLANNER_PROVIDER,
                "Structured-output planner backend via Ollama Cloud remote chat API.",
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
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
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


def _clean_string_list(values: object) -> list[str]:
    if isinstance(values, str):
        cleaned = values.strip()
        return [cleaned] if cleaned else []
    if not isinstance(values, list):
        return []
    cleaned_values = [str(item).strip() for item in values if str(item).strip()]
    return cleaned_values


def _extract_subgoal_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if not isinstance(value, dict):
        return None
    for key in ("statement", "text", "theorem", "goal", "subgoal", "expression", "tactic"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    for candidate in value.values():
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _coerce_subgoals(value: object) -> list[str]:
    if isinstance(value, list):
        cleaned = [_extract_subgoal_text(item) for item in value]
        return [item for item in cleaned if item]
    single = _extract_subgoal_text(value)
    return [single] if single else []


def _coerce_textbook_defaults(value: object) -> list[str]:
    if isinstance(value, dict):
        cleaned = [f"{key}: {str(item).strip()}" for key, item in value.items() if str(item).strip()]
        return cleaned or [PLANNER_REPAIR_DEFAULT]
    cleaned = _clean_string_list(value)
    return cleaned or [PLANNER_REPAIR_DEFAULT]


def _coerce_bool(value: object, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return default


def _coerce_confidence(value: object, *, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _normalize_planner_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    normalized["clarifying_questions"] = _clean_string_list(payload.get("clarifying_questions"))
    normalized["textbook_defaults"] = _coerce_textbook_defaults(payload.get("textbook_defaults"))
    paragraph = payload.get("plan_paragraph", payload.get("plan"))
    if isinstance(paragraph, str):
        normalized["plan_paragraph"] = paragraph.strip()
    elif paragraph is not None:
        normalized["plan_paragraph"] = str(paragraph).strip()
    normalized["subgoals"] = _coerce_subgoals(payload.get("subgoals", payload.get("subgoal")))
    normalized["needs_review"] = _coerce_bool(payload.get("needs_review"), default=False)
    normalized["confidence"] = _coerce_confidence(payload.get("confidence"), default=0.75)
    return normalized


def _unwrap_driver_response(
    value: PlannerLLMResponse | tuple[PlannerLLMResponse, ProviderCallMetadata],
) -> tuple[PlannerLLMResponse, ProviderCallMetadata | None]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], ProviderCallMetadata):
        return value[0], value[1]
    return value, None


def _planner_error_metadata(error: BaseException) -> ProviderCallMetadata | None:
    for candidate in (error, getattr(error, "__cause__", None)):
        metadata = getattr(candidate, "provider_metadata", None)
        if isinstance(metadata, ProviderCallMetadata):
            return metadata
    return None


def _schema_invalid_error(message: str, *, metadata: ProviderCallMetadata, cause: Exception) -> PlannerDriverError:
    error = PlannerDriverError(message)
    setattr(error, "provider_metadata", metadata)
    raise error from cause


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
        timeout: float = PLANNER_TIMEOUT,
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
            max_tokens=PLANNER_MAX_OUTPUT_TOKENS,
            temperature=0.2,
            response_format=_planner_response_format(),
        )
        content = raw.choices[0].message.content
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise PlannerDriverError("Planner chat-completion response did not contain text content.")
        usage = getattr(raw, "usage", None)
        metadata = ProviderCallMetadata(
            input_tokens=int(usage.prompt_tokens) if getattr(usage, "prompt_tokens", None) is not None else None,
            output_tokens=int(usage.completion_tokens) if getattr(usage, "completion_tokens", None) is not None else None,
            usage_source="provider" if usage is not None else "estimated_chars",
            prompt_text=json.dumps(messages, ensure_ascii=True),
            response_text=content,
            raw_planner_response=content,
        )
        try:
            payload = _extract_json_payload(content)
            response = PlannerLLMResponse.model_validate(_normalize_planner_payload(payload))
        except Exception as error:
            _schema_invalid_error(f"Planner backend returned schema-invalid JSON: {error}", metadata=metadata, cause=error)
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
            max_new_tokens=PLANNER_MAX_OUTPUT_TOKENS,
            temperature=0.2,
            return_full_text=False,
            details=True,
            decoder_input_details=True,
        )
        generated_text = getattr(raw_text, "generated_text", None)
        details = getattr(raw_text, "details", None)
        response_text = str(generated_text if generated_text is not None else raw_text)
        metadata = ProviderCallMetadata(
            input_tokens=len(getattr(details, "prefill", []) or []) if details is not None else None,
            output_tokens=getattr(details, "generated_tokens", None) if details is not None else None,
            usage_source="provider" if details is not None else "estimated_chars",
            prompt_text=prompt,
            response_text=response_text,
            raw_planner_response=response_text,
        )
        try:
            payload = _extract_json_payload(response_text)
            response = PlannerLLMResponse.model_validate(_normalize_planner_payload(payload))
        except Exception as error:
            _schema_invalid_error(f"Planner backend returned schema-invalid JSON: {error}", metadata=metadata, cause=error)
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
            wrapped = PlannerDriverError(f"Planner backend invocation failed for {backend.model}: {error}")
            metadata = _planner_error_metadata(error)
            if metadata is not None:
                setattr(wrapped, "provider_metadata", metadata)
            raise wrapped from error


class OllamaPlannerDriver:
    """Ollama Cloud planner driver using the documented `/api/chat` schema format."""

    def __init__(
        self,
        *,
        api_key: str = OLLAMA_API_KEY,
        host: str = OLLAMA_HOST,
        timeout: float = PLANNER_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.host = host.rstrip("/")
        self.timeout = timeout

    @property
    def api_url(self) -> str:
        if self.host.endswith("/api"):
            return f"{self.host}/chat"
        return f"{self.host}/api/chat"

    @property
    def uses_local_host(self) -> bool:
        parsed = urlparse(self.host)
        host = (parsed.hostname or "").strip().lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def generate(
        self,
        *,
        backend: PlannerBackend,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[PlannerLLMResponse, ProviderCallMetadata]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        body = {
            "model": backend.model,
            "messages": messages,
            "format": _planner_response_format()["json_schema"]["schema"],
            "options": {
                "temperature": 0.2,
                "num_predict": PLANNER_MAX_OUTPUT_TOKENS,
            },
            "stream": False,
            "think": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key and not self.uses_local_host:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib_request.Request(
            self.api_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError:
            raise
        except urllib_error.URLError:
            raise
        except json.JSONDecodeError as error:
            raise PlannerDriverError(f"Ollama planner returned invalid JSON payload: {error}") from error
        except Exception as error:
            raise PlannerDriverError(f"Ollama planner request failed: {error}") from error

        content = payload.get("message", {}).get("content")
        if not isinstance(content, str):
            raise PlannerDriverError("Ollama planner response did not contain assistant text content.")
        metadata = ProviderCallMetadata(
            input_tokens=int(payload.get("prompt_eval_count")) if payload.get("prompt_eval_count") is not None else None,
            output_tokens=int(payload.get("eval_count")) if payload.get("eval_count") is not None else None,
            usage_source="provider",
            prompt_text=json.dumps(messages, ensure_ascii=True),
            response_text=content,
            raw_planner_response=content,
            metadata={
                "done_reason": payload.get("done_reason"),
                "total_duration": payload.get("total_duration"),
                "load_duration": payload.get("load_duration"),
            },
        )
        try:
            parsed = _extract_json_payload(content)
            response = PlannerLLMResponse.model_validate(_normalize_planner_payload(parsed))
        except Exception as error:
            _schema_invalid_error(f"Planner backend returned schema-invalid JSON: {error}", metadata=metadata, cause=error)
        return response, metadata


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


def _extract_stub_subgoal(theorem_stub: str | None) -> str | None:
    if not theorem_stub or not theorem_stub.strip():
        return None
    lines = theorem_stub.strip().splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("theorem ") or stripped.startswith("lemma "):
            return "\n".join(lines[index:]).strip()
    return None


def _minimal_hit_subgoal(claim: str, hit) -> str:
    slug = slugify_claim(claim)
    hint = hit.proven_lemmas[0] if hit.proven_lemmas else hit.tactic_hints[0] if hit.tactic_hints else hit.name
    return "\n".join(
        [
            f"theorem {slug}_{hit.name}_subgoal :",
            f"    Prop := by  -- close this goal by reusing `{hint}`",
            "  sorry",
        ]
    )


def _fallback_subgoals(claim: str, context: PlannerContext, *, theorem_stub: str | None = None) -> list[str]:
    stub_subgoal = _extract_stub_subgoal(theorem_stub)
    if stub_subgoal is not None:
        return [stub_subgoal]
    if context.selected_preamble:
        return [_minimal_hit_subgoal(claim, context.selected_preamble[0])]
    slug = slugify_claim(claim)
    return [
        "\n".join(
            [
                f"theorem {slug}_subgoal :",
                "    Prop := by  -- organize the claim around explicit hypotheses and a direct closing lemma",
                "  sorry",
            ]
        )
    ]


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
    elif len(context.selected_preamble) >= 2:
        confidence += 0.05
    if context.few_shot_traces:
        confidence += 0.02
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
        self.driver = driver or (
            OllamaPlannerDriver() if self.backend.name == "ollama-cloud" else HuggingFacePlannerDriver()
        )
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
        context = self.retrieval_service.build_context(
            claim,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
        )
        user_prompt = build_user_prompt(
            claim,
            context,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
        )
        try:
            response, metadata = self._generate_with_retry(user_prompt)
        except Exception as exc:
            if classify_exception(exc) != "schema_invalid":
                raise
            metadata = _planner_error_metadata(exc)
            response = self._repair_planner_response(exc, metadata.raw_planner_response if metadata else None)
            raw_text = metadata.raw_planner_response if metadata else None
            metadata = replace(
                metadata or ProviderCallMetadata(response_text=raw_text, raw_planner_response=raw_text),
                response_text=(metadata.response_text if metadata else None) or raw_text,
                raw_planner_response=raw_text,
                metadata={
                    **((metadata.metadata if metadata is not None else {})),
                    "planner_repaired": True,
                    "error_code": "schema_invalid",
                    "error_message": str(exc),
                },
            )
        repaired = bool(metadata and metadata.metadata.get("planner_repaired"))
        upgraded_subgoals = repaired or _subgoals_need_upgrade(response.subgoals)
        subgoals = _dedupe_subgoals(
            _fallback_subgoals(claim, context, theorem_stub=theorem_stub) if upgraded_subgoals else response.subgoals
        )[:3]
        return (
            PlannerPacket(
                claim=claim,
                clarifying_questions=response.clarifying_questions,
                textbook_defaults=response.textbook_defaults,
                plan_paragraph=response.plan_paragraph,
                subgoals=subgoals,
                needs_review=(not benchmark_mode) or response.needs_review,
                confidence=0.65 if repaired else _calibrate_confidence(response, context, upgraded_subgoals=upgraded_subgoals),
                review_state="approved" if benchmark_mode else "awaiting_plan_review",
                backend=self.backend.name,
                model=self.backend.model or PLANNER_MODEL,
                selected_preamble=context.selected_preamble,
                few_shot_traces=context.few_shot_traces,
            ),
            metadata,
        )

    def _repair_planner_response(self, error: Exception, raw_text: str | None) -> PlannerLLMResponse:
        if not raw_text:
            raise error
        try:
            payload = _normalize_planner_payload(_extract_json_payload(raw_text))
            payload["needs_review"] = True
            payload["confidence"] = 0.65
            return PlannerLLMResponse.model_validate(payload)
        except Exception:
            raise error

    def _generate_with_retry(
        self, user_prompt: str
    ) -> tuple[PlannerLLMResponse, ProviderCallMetadata | None]:
        last_error: Exception | None = None
        last_metadata: ProviderCallMetadata | None = None
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
                error_code = classify_exception(exc)
                metadata = _planner_error_metadata(exc)
                if metadata is not None:
                    last_metadata = metadata
                if error_code not in PLANNER_RETRYABLE_ERROR_CODES:
                    raise
                if attempt + 1 >= PLANNER_RETRY_ATTEMPTS:
                    break
                backoff = PLANNER_RETRY_BACKOFF_SECONDS[min(attempt, len(PLANNER_RETRY_BACKOFF_SECONDS) - 1)]
                time.sleep(backoff)
        assert last_error is not None
        if last_metadata is not None and _planner_error_metadata(last_error) is None:
            setattr(last_error, "provider_metadata", last_metadata)
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
