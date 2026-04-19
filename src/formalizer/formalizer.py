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
from src.guardrails import semantic_faithfulness_score, vacuity_report
from src.lean import lean_run_code
from src.planner.models import PlannerPacket


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
    ) -> FormalizerGenerationResponse:
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
    ) -> FormalizerGenerationResponse:
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
        return FormalizerGenerationResponse.model_validate(_extract_json_payload(content))


class HuggingFaceFormalizerDriver:
    """HF text-generation driver for non-Leanstral formalizer backends."""

    def __init__(self, *, token: str = HF_TOKEN, timeout: float = FORMALIZER_TIMEOUT) -> None:
        self.token = token
        self.timeout = timeout

    def generate(
        self,
        *,
        backend: FormalizerBackend,
        system_prompt: str,
        user_prompt: str,
        context: FormalizerContext,
    ) -> FormalizerGenerationResponse:
        try:
            from huggingface_hub import InferenceClient
        except Exception as error:
            raise FormalizerDriverError("huggingface_hub is required for Hugging Face formalizer backends.") from error

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
                max_new_tokens=1600,
                temperature=0.1,
                return_full_text=False,
            )
        except Exception as error:
            raise FormalizerDriverError(
                f"Formalizer backend invocation failed for {backend.model}: {error}"
            ) from error
        return FormalizerGenerationResponse.model_validate(_extract_json_payload(str(raw_text)))


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

    def formalize(
        self,
        claim: str,
        *,
        planner_packet: dict[str, Any] | None = None,
        benchmark_mode: bool = False,
    ) -> FormalizationPacket:
        validated_packet = PlannerPacket.model_validate(planner_packet) if planner_packet else None
        built = self.context_builder.build(claim, validated_packet)
        user_prompt = build_user_prompt(built.context)
        response = self._driver_for_backend().generate(
            backend=self.backend,
            system_prompt=self.system_prompt,
            user_prompt=user_prompt,
            context=built.context,
        )
        theorem_name = self._canonical_theorem_name(response.theorem_name, claim)
        lean_code = self.render_lean_code(
            theorem_name=theorem_name,
            generation=response,
            context=built.context,
        )
        vacuity = vacuity_report(lean_code)
        faithfulness = self._assess_faithfulness(claim, lean_code)
        parse_result = lean_run_code(lean_code, filename=f"{theorem_name}.lean")
        parse_check = ParseCheck(
            success=bool(parse_result.get("success")),
            exit_code=int(parse_result.get("exit_code", -1)),
            stdout=str(parse_result.get("stdout", "")),
            stderr=str(parse_result.get("stderr", "")),
        )
        review_state = "approved" if benchmark_mode else "awaiting_formalization_review"
        return FormalizationPacket(
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
        )

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
        if generation.final_expression and generation.final_expression.strip() == "sorry":
            theorem_lines.append("  sorry")
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
