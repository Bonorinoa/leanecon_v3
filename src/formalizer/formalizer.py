"""Formalizer orchestration and provider-aware drivers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

from src.config import (
    FORMALIZER_BACKEND,
    FORMALIZER_MODEL,
    FORMALIZER_TIMEOUT,
    HF_TOKEN,
    MISTRAL_API_KEY,
    MISTRAL_BASE_URL,
    PROVER_PROVIDER,
)
from src.formalizer.context_builder import FormalizerContextBuilder
from src.formalizer.models import (
    FaithfulnessAssessment,
    FormalizationPacket,
    FormalizerContext,
    FormalizerGenerationResponse,
    ParseCheck,
)
from src.formalizer.prompts import build_system_prompt, build_user_prompt
from src.formalizer.prompts import build_revision_user_prompt
from src.guardrails import semantic_faithfulness_score, vacuity_report
from src.lean import lean_run_code
from src.observability.models import ProviderCallMetadata
from src.planner.models import PlannerPacket
from src.providers import normalize_huggingface_provider


class FormalizerDriverError(RuntimeError):
    """Raised when a formalizer backend cannot complete a request."""


@dataclass(frozen=True)
class FormalizerBackend:
    name: str
    provider: str
    model: str
    notes: str


class FormalizerDriver(Protocol):
    def generate(
        self,
        *,
        backend: FormalizerBackend,
        system_prompt: str,
        user_prompt: str,
        context: FormalizerContext,
    ) -> FormalizerGenerationResponse | tuple[FormalizerGenerationResponse, ProviderCallMetadata]:
        """Return a validated formalizer response."""


class DriverRegistry:
    def __init__(self) -> None:
        self._backends = {
            "leanstral": FormalizerBackend(
                name="leanstral",
                provider="mistral",
                model="labs-leanstral-2603",
                notes="Primary Lean formalizer via Mistral AI.",
            ),
            "goedel-prover-v2": FormalizerBackend(
                name="goedel-prover-v2",
                provider="huggingface",
                model="Goedel-LM/Goedel-Prover-V2-32B",
                notes="Alternative open formalizer via Hugging Face.",
            ),
        }

    def get(self, name: str) -> FormalizerBackend:
        if name not in self._backends:
            raise KeyError(name)
        return self._backends[name]

    def names(self) -> list[str]:
        return sorted(self._backends)


_STUB_THEOREM_HEAD_RE = re.compile(r"(?:theorem|lemma)\s+([A-Za-z0-9_']+)", re.MULTILINE)
_STUB_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.]+)\s*$", re.MULTILINE)
_STUB_OPEN_RE = re.compile(r"^\s*open\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ParsedTheoremStub:
    prelude: str
    theorem_name: str
    imports: list[str]
    open_statements: list[str]
    raw: str


def parse_theorem_stub(theorem_stub: str) -> ParsedTheoremStub:
    """Parse an authoritative Lean theorem stub into its structural components.

    Raises FormalizerDriverError if the stub is not well-formed (missing
    `theorem`/`lemma` head or `:= by` body placeholder).
    """
    if not theorem_stub or not theorem_stub.strip():
        raise FormalizerDriverError("Authoritative theorem_stub is empty.")
    if ":= by" not in theorem_stub:
        raise FormalizerDriverError("Authoritative theorem_stub does not contain `:= by` body placeholder.")
    head_match = _STUB_THEOREM_HEAD_RE.search(theorem_stub)
    if head_match is None:
        raise FormalizerDriverError("Authoritative theorem_stub does not contain a theorem declaration.")
    theorem_start = head_match.start()
    prelude = theorem_stub[:theorem_start]
    imports = [match.group(1).strip() for match in _STUB_IMPORT_RE.finditer(prelude)]
    open_statements = [match.group(1).strip() for match in _STUB_OPEN_RE.finditer(prelude)]
    return ParsedTheoremStub(
        prelude=prelude,
        theorem_name=head_match.group(1),
        imports=imports,
        open_statements=open_statements,
        raw=theorem_stub,
    )


def _extract_json_payload(raw_text: str) -> dict[str, object]:
    stripped = raw_text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise FormalizerDriverError("Formalizer backend did not return a JSON object.")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise FormalizerDriverError(f"Formalizer backend returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise FormalizerDriverError("Formalizer backend returned non-object JSON.")
    return payload


def _unwrap_driver_response(
    value: FormalizerGenerationResponse | tuple[FormalizerGenerationResponse, ProviderCallMetadata],
) -> tuple[FormalizerGenerationResponse, ProviderCallMetadata | None]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], ProviderCallMetadata):
        return value[0], value[1]
    return value, None


def _merge_provider_metadata(
    primary: ProviderCallMetadata | None,
    secondary: ProviderCallMetadata | None,
) -> ProviderCallMetadata | None:
    if primary is None:
        return secondary
    if secondary is None:
        return primary
    return ProviderCallMetadata(
        input_tokens=(primary.input_tokens or 0) + (secondary.input_tokens or 0),
        output_tokens=(primary.output_tokens or 0) + (secondary.output_tokens or 0),
        usage_source=(
            primary.usage_source
            if primary.usage_source == secondary.usage_source
            else "mixed"
        ),
        prompt_text="\n\n".join(part for part in [primary.prompt_text, secondary.prompt_text] if part),
        response_text="\n\n".join(part for part in [primary.response_text, secondary.response_text] if part),
        metadata={**primary.metadata, **secondary.metadata},
    )


def _sanitize_planner_packet_payload(planner_packet: dict[str, Any] | None) -> dict[str, Any] | None:
    if planner_packet is None:
        return None
    allowed = PlannerPacket.model_fields.keys()
    return {key: value for key, value in planner_packet.items() if key in allowed}


class MistralFormalizerDriver:
    """Mistral chat-completions driver for Leanstral."""

    def __init__(
        self,
        *,
        api_key: str = MISTRAL_API_KEY,
        base_url: str = MISTRAL_BASE_URL,
        timeout: float = FORMALIZER_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(
        self,
        *,
        backend: FormalizerBackend,
        system_prompt: str,
        user_prompt: str,
        context: FormalizerContext,
    ) -> FormalizerGenerationResponse | tuple[FormalizerGenerationResponse, ProviderCallMetadata]:
        if not self.api_key:
            raise FormalizerDriverError("Mistral API key is required for the Leanstral formalizer backend.")
        payload = json.dumps(
            {
                "model": backend.model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        ).encode("utf-8")
        request = urllib_request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise FormalizerDriverError(f"Mistral formalizer request failed: {body or error.reason}") from error
        except urllib_error.URLError as error:
            raise FormalizerDriverError(f"Mistral formalizer request failed: {error.reason}") from error
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise FormalizerDriverError("Mistral formalizer response did not contain choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            raise FormalizerDriverError("Mistral formalizer response did not contain text content.")
        metadata = ProviderCallMetadata(
            input_tokens=(
                int(raw.get("usage", {}).get("prompt_tokens"))
                if isinstance(raw.get("usage"), dict) and raw.get("usage", {}).get("prompt_tokens") is not None
                else None
            ),
            output_tokens=(
                int(raw.get("usage", {}).get("completion_tokens"))
                if isinstance(raw.get("usage"), dict) and raw.get("usage", {}).get("completion_tokens") is not None
                else None
            ),
            usage_source="provider" if isinstance(raw.get("usage"), dict) else "estimated_chars",
            prompt_text=json.dumps(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                ensure_ascii=True,
            ),
            response_text=content,
        )
        return FormalizerGenerationResponse.model_validate(_extract_json_payload(content)), metadata


class HuggingFaceFormalizerDriver:
    """HF hosted-inference driver for non-Leanstral formalizer backends."""

    def __init__(
        self,
        *,
        token: str = HF_TOKEN,
        timeout: float = FORMALIZER_TIMEOUT,
        provider: str = PROVER_PROVIDER,
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
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[FormalizerGenerationResponse, ProviderCallMetadata]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        raw = client.chat_completion(
            messages,
            max_tokens=1600,
            temperature=0.1,
        )
        content = raw.choices[0].message.content
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise FormalizerDriverError("Formalizer chat-completion response did not contain text content.")
        usage = getattr(raw, "usage", None)
        metadata = ProviderCallMetadata(
            input_tokens=int(usage.prompt_tokens) if getattr(usage, "prompt_tokens", None) is not None else None,
            output_tokens=int(usage.completion_tokens) if getattr(usage, "completion_tokens", None) is not None else None,
            usage_source="provider" if usage is not None else "estimated_chars",
            prompt_text=json.dumps(messages, ensure_ascii=True),
            response_text=content,
        )
        return FormalizerGenerationResponse.model_validate(_extract_json_payload(content)), metadata

    def _text_generation(
        self,
        *,
        client,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[FormalizerGenerationResponse, ProviderCallMetadata]:
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
            max_new_tokens=1600,
            temperature=0.1,
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
        )
        return FormalizerGenerationResponse.model_validate(_extract_json_payload(response_text)), metadata

    def _should_fallback_to_text_generation(self, error: Exception) -> bool:
        message = str(error).lower()
        return "supported task: text-generation" in message or "supported task: text generation" in message

    def generate(
        self,
        *,
        backend: FormalizerBackend,
        system_prompt: str,
        user_prompt: str,
        context: FormalizerContext,
    ) -> FormalizerGenerationResponse | tuple[FormalizerGenerationResponse, ProviderCallMetadata]:
        try:
            from huggingface_hub import InferenceClient
        except Exception as error:
            raise FormalizerDriverError("huggingface_hub is required for Hugging Face formalizer backends.") from error

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
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
            except Exception as error:
                if not self._should_fallback_to_text_generation(error):
                    raise
                return self._text_generation(
                    client=client,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
        except Exception as error:
            raise FormalizerDriverError(
                f"Formalizer backend invocation failed for {backend.model}: {error}"
            ) from error


class Formalizer:
    """Orchestrates context building, model invocation, and post-processing."""

    def __init__(
        self,
        *,
        backend: str = FORMALIZER_BACKEND,
        mistral_driver: FormalizerDriver | None = None,
        huggingface_driver: FormalizerDriver | None = None,
        context_builder: FormalizerContextBuilder | None = None,
    ) -> None:
        self.registry = DriverRegistry()
        registry_backend = self.registry.get(backend)
        if backend == FORMALIZER_BACKEND and FORMALIZER_MODEL.strip():
            self.backend = FormalizerBackend(
                name=registry_backend.name,
                provider=registry_backend.provider,
                model=FORMALIZER_MODEL.strip(),
                notes=registry_backend.notes,
            )
        else:
            self.backend = registry_backend
        self.context_builder = context_builder or FormalizerContextBuilder()
        self.system_prompt = build_system_prompt()
        self._drivers: dict[str, FormalizerDriver] = {
            "mistral": mistral_driver or MistralFormalizerDriver(),
            "huggingface": huggingface_driver or HuggingFaceFormalizerDriver(),
        }

    def formalize_with_metadata(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        benchmark_mode: bool = False,
    ) -> tuple[FormalizationPacket, ProviderCallMetadata | None]:
        if theorem_stub and theorem_stub.strip():
            return self._formalize_from_stub(
                claim=claim,
                theorem_stub=theorem_stub,
                preamble_names=preamble_names or [],
                planner_packet=planner_packet,
                benchmark_mode=benchmark_mode,
            )
        sanitized_packet = _sanitize_planner_packet_payload(planner_packet)
        validated_packet = PlannerPacket.model_validate(sanitized_packet) if sanitized_packet else None
        built = self.context_builder.build(claim, validated_packet)
        response, metadata = _unwrap_driver_response(
            self._driver_for_backend().generate(
                backend=self.backend,
                system_prompt=self.system_prompt,
                user_prompt=build_user_prompt(built.context),
                context=built.context,
            )
        )
        response, lean_code, faithfulness, metadata = self._revise_if_needed(
            claim=claim,
            context=built.context,
            generation=response,
            metadata=metadata,
        )
        theorem_name = self._canonical_theorem_name(response.theorem_name, claim)
        vacuity = vacuity_report(lean_code)
        parse_result = lean_run_code(lean_code, filename=f"{theorem_name}.lean")
        parse_check = ParseCheck(
            success=bool(parse_result.get("success")),
            exit_code=int(parse_result.get("exit_code", -1)),
            stdout=str(parse_result.get("stdout", "")),
            stderr=str(parse_result.get("stderr", "")),
        )
        review_state = "approved" if benchmark_mode else "awaiting_formalization_review"
        return (
            FormalizationPacket(
                claim=claim,
                lean_code=lean_code,
                theorem_with_sorry=lean_code,
                theorem_name=theorem_name,
                imports=list(built.context.imports),
                selected_imports=list(built.context.imports),
                open_statements=self._canonical_open_statements(
                    built.context.open_statements + response.open_statements
                ),
                subgoals=response.subgoals,
                selected_preamble=list(built.context.selected_preamble),
                vacuity=vacuity,
                faithfulness=faithfulness,
                parse_check=parse_check,
                review_state=review_state,
                backend=self.backend.name,
                provider=self.backend.provider,
                model=self._model_name(),
            ),
            metadata,
        )

    def formalize(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        theorem_stub: str | None = None,
        preamble_names: list[str] | None = None,
        benchmark_mode: bool = False,
    ) -> FormalizationPacket:
        packet, _ = self.formalize_with_metadata(
            claim,
            planner_packet=planner_packet,
            theorem_stub=theorem_stub,
            preamble_names=preamble_names,
            benchmark_mode=benchmark_mode,
        )
        return packet

    def _formalize_from_stub(
        self,
        *,
        claim: str,
        theorem_stub: str,
        preamble_names: list[str],
        planner_packet: dict[str, Any] | None,
        benchmark_mode: bool,
    ) -> tuple[FormalizationPacket, ProviderCallMetadata | None]:
        """Stub-authoritative formalization: no LLM, treat the stub as truth."""
        parsed = parse_theorem_stub(theorem_stub)
        lean_code = theorem_stub if theorem_stub.endswith("\n") else theorem_stub + "\n"
        parse_result = lean_run_code(lean_code, filename=f"{parsed.theorem_name}.lean")
        parse_check = ParseCheck(
            success=bool(parse_result.get("success")),
            exit_code=int(parse_result.get("exit_code", -1)),
            stdout=str(parse_result.get("stdout", "")),
            stderr=str(parse_result.get("stderr", "")),
        )
        vacuity = vacuity_report(lean_code)
        faithfulness = FaithfulnessAssessment.model_validate(
            {
                "score": 5.0,
                "coverage": 1.0,
                "structural_isomorphism": 1.0,
                "primitive_faithfulness": 1.0,
                "claim_frame": {"concepts": []},
                "stub_frame": {"concepts": []},
                "needs_human_review": False,
                "passes_gate": True,
                "feedback": [
                    "Authoritative theorem stub supplied; formalizer did not regenerate the statement.",
                ],
            }
        )
        review_state = "approved" if benchmark_mode else "awaiting_formalization_review"
        packet = FormalizationPacket(
            claim=claim,
            lean_code=lean_code,
            theorem_with_sorry=lean_code,
            theorem_name=parsed.theorem_name,
            imports=list(parsed.imports),
            selected_imports=list(parsed.imports),
            open_statements=list(parsed.open_statements),
            subgoals=[],
            selected_preamble=list(preamble_names),
            vacuity=vacuity,
            faithfulness=faithfulness,
            parse_check=parse_check,
            review_state=review_state,
            backend=self.backend.name,
            provider=self.backend.provider,
            model=self._model_name(),
        )
        return packet, None

    def _revise_if_needed(
        self,
        *,
        claim: str,
        context: FormalizerContext,
        generation: FormalizerGenerationResponse,
        metadata: ProviderCallMetadata | None,
    ) -> tuple[FormalizerGenerationResponse, str, FaithfulnessAssessment, ProviderCallMetadata | None]:
        lean_code = self.render_lean_code(
            theorem_name=self._canonical_theorem_name(generation.theorem_name, claim),
            generation=generation,
            context=context,
        )
        faithfulness = self._assess_faithfulness(claim, lean_code)
        if not self._needs_revision(context=context, generation=generation, faithfulness=faithfulness):
            return generation, lean_code, faithfulness, metadata

        revised, revised_metadata = _unwrap_driver_response(
            self._driver_for_backend().generate(
                backend=self.backend,
                system_prompt=self.system_prompt,
                user_prompt=build_revision_user_prompt(
                    context,
                    previous_score=faithfulness.score,
                    feedback=faithfulness.feedback,
                    prior_lean_code=lean_code,
                ),
                context=context,
            )
        )
        revised_code = self.render_lean_code(
            theorem_name=self._canonical_theorem_name(revised.theorem_name, claim),
            generation=revised,
            context=context,
        )
        revised_faithfulness = self._assess_faithfulness(claim, revised_code)
        return revised, revised_code, revised_faithfulness, _merge_provider_metadata(metadata, revised_metadata)

    def _driver_for_backend(self) -> FormalizerDriver:
        return self._drivers[self.backend.provider]

    def _model_name(self) -> str:
        return self.backend.model

    def _canonical_theorem_name(self, candidate: str, claim: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_']+", "_", candidate.strip()).strip("_")
        if not cleaned:
            tokens = re.findall(r"[A-Za-z0-9]+", claim.lower())
            cleaned = "_".join(tokens[:8]) or "claim"
        if not cleaned.startswith("formalizer_"):
            cleaned = f"formalizer_{cleaned}"
        return cleaned[:72]

    def _canonical_open_statements(self, values: list[str]) -> list[str]:
        canonical: list[str] = []
        seen: set[str] = set()
        for value in values:
            stripped = value.strip()
            if not stripped or stripped in seen:
                continue
            canonical.append(stripped)
            seen.add(stripped)
        return canonical

    def _placeholder_statement(self, statement: str) -> bool:
        normalized = " ".join(statement.split())
        return normalized in {"True", "False", "Prop", "1 = 1"}

    def _expected_identifier_tokens(self, context: FormalizerContext) -> set[str]:
        tokens: set[str] = set()
        for entry in context.preamble_entries:
            for identifier in [*entry.definitions, *entry.proven_lemmas, *entry.related]:
                cleaned = str(identifier).strip()
                if cleaned:
                    tokens.add(cleaned)
                    tokens.update(part for part in re.split(r"[^A-Za-z0-9_.']+", cleaned) if part)
        return tokens

    def _is_complex_claim(self, context: FormalizerContext) -> bool:
        claim_text = " ".join([context.claim, context.plan_paragraph]).lower()
        markers = ("contraction", "fixed point", "value function", "bellman", "equilibrium")
        return len(context.preamble_entries) >= 3 or sum(marker in claim_text for marker in markers) >= 2

    def _subgoals_are_specific(self, context: FormalizerContext, generation: FormalizerGenerationResponse) -> bool:
        expected = self._expected_identifier_tokens(context)
        specific_count = 0
        for subgoal in generation.subgoals:
            if self._placeholder_statement(subgoal.statement):
                continue
            if any(token in subgoal.statement for token in expected):
                specific_count += 1
        minimum = 4 if self._is_complex_claim(context) else 1
        return specific_count >= minimum and len(generation.subgoals) >= minimum

    def _needs_revision(
        self,
        *,
        context: FormalizerContext,
        generation: FormalizerGenerationResponse,
        faithfulness: FaithfulnessAssessment,
    ) -> bool:
        if faithfulness.score < 4.5:
            return True
        return not self._subgoals_are_specific(context, generation)

    def render_lean_code(
        self,
        *,
        theorem_name: str,
        generation: FormalizerGenerationResponse,
        context: FormalizerContext,
    ) -> str:
        import_lines = [f"import {module}" for module in context.imports]
        open_lines = [f"open {statement}" for statement in self._canonical_open_statements(
            context.open_statements + generation.open_statements
        )]
        theorem_lines = [f"theorem {theorem_name} : {generation.theorem_statement} := by"]
        for subgoal in generation.subgoals:
            if subgoal.rationale:
                theorem_lines.append(f"  -- {subgoal.rationale}")
            theorem_lines.append(f"  have {subgoal.name} : {subgoal.statement} := by")
            theorem_lines.append("    sorry")
        if generation.final_expression and generation.final_expression.strip():
            theorem_lines.append(f"  {generation.final_expression.strip()}")
        else:
            theorem_lines.append("  sorry")
        parts: list[str] = []
        if import_lines:
            parts.extend(import_lines)
        if open_lines:
            parts.append("")
            parts.extend(open_lines)
        parts.append("")
        parts.append("/--")
        parts.append(generation.theorem_docstring)
        parts.append("-/")
        parts.extend(theorem_lines)
        parts.append("")
        return "\n".join(parts)

    def _assess_faithfulness(self, claim: str, lean_code: str) -> FaithfulnessAssessment:
        raw = semantic_faithfulness_score(claim, lean_code)
        feedback: list[str] = []
        claim_concepts = set(raw.get("claim_frame", {}).get("concepts", []))
        stub_concepts = set(raw.get("stub_frame", {}).get("concepts", []))
        missing = sorted(claim_concepts - stub_concepts)
        extras = sorted(stub_concepts - claim_concepts)
        feedback.append(
            f"Concept coverage {raw.get('coverage', 0):.3f}, structural match {raw.get('structural_isomorphism', 0):.3f}, primitive grounding {raw.get('primitive_faithfulness', 0):.3f}."
        )
        if missing:
            feedback.append(f"Claim concepts not yet reflected in the stub frame: {', '.join(missing)}.")
        else:
            feedback.append("Stub frame covers the detected claim concepts.")
        if extras:
            feedback.append(f"Stub introduces extra concepts via imports/context: {', '.join(extras[:6])}.")
        if raw.get("passes_gate"):
            feedback.append("Faithfulness passes the automatic gate.")
        elif raw.get("needs_human_review"):
            feedback.append("Faithfulness lands in the human-review band.")
        else:
            feedback.append("Faithfulness is below the acceptance gate and should be revised before proving.")
        return FaithfulnessAssessment.model_validate({**raw, "feedback": feedback})
