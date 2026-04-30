"""Provider driver implementations for the prover package.

This module owns network transports and backend registry metadata. It should not
contain proof-search policy or harness logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from src.config import (
    FORMALIZER_TIMEOUT,
    HF_TOKEN,
    MISTRAL_API_KEY,
    MISTRAL_BASE_URL,
    PROVER_PROVIDER,
)
from src.observability import ProviderCallMetadata
from src.providers import normalize_huggingface_provider
from src.prover.models import ProverAction
from src.utils.json_extraction import extract_json_object

PROVER_RETRY_ATTEMPTS = 3

PROVER_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0)

PROVER_RETRYABLE_ERROR_CODES = frozenset(
    {"rate_limit", "provider_http_error", "provider_unavailable", "timeout"}
)

def _classify_http_status(code: int) -> str:
    if code in {401, 403}:
        return "auth"
    if code == 429:
        return "rate_limit"
    if code in {502, 503, 504}:
        return "provider_unavailable"
    return "provider_http_error"

class ProverDriverError(RuntimeError):
    """Raised when a prover backend cannot complete a request."""

@dataclass(frozen=True)
class ProverBackend:
    name: str
    provider: str
    model: str
    notes: str

class ProverDriver(Protocol):
    def next_action(
        self,
        *,
        backend: ProverBackend,
        prompt: str,
    ) -> ProverAction | tuple[ProverAction, ProviderCallMetadata]:
        """Return the next structured prover action."""

class DriverRegistry:
    def __init__(self) -> None:
        self._backends = {
            "goedel-prover-v2": ProverBackend(
                name="goedel-prover-v2",
                provider="huggingface",
                model="Goedel-LM/Goedel-Prover-V2-32B",
                notes="Primary proving backend for Lean proof search.",
            ),
            "leanstral": ProverBackend(
                name="leanstral",
                provider="mistral",
                model="labs-leanstral-2603",
                notes="Mistral-hosted Leanstral backend.",
            ),
        }

    def get(self, name: str) -> ProverBackend:
        if name not in self._backends:
            raise KeyError(name)
        return self._backends[name]

    def register(self, backend: ProverBackend) -> None:
        self._backends[backend.name] = backend

    def available(self) -> list[str]:
        return list(self._backends.keys())

def _extract_json_payload(raw_text: str) -> dict[str, object]:
    return extract_json_object(
        raw_text,
        error_factory=lambda message: ProverDriverError(
            message.replace("Driver", "Prover backend", 1)
        ),
    )

class HuggingFaceProverDriver:
    """HF hosted-inference driver for structured prover actions."""

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
        prompt: str,
    ) -> tuple[ProverAction, ProviderCallMetadata]:
        messages = [
            {"role": "system", "content": "You are a Lean theorem prover. Return only JSON."},
            {"role": "user", "content": prompt},
        ]
        raw = client.chat_completion(
            messages,
            max_tokens=800,
            temperature=0.1,
        )
        content = raw.choices[0].message.content
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            raise ProverDriverError("Prover chat-completion response did not contain text content.")
        usage = getattr(raw, "usage", None)
        return ProverAction.model_validate(_extract_json_payload(content)), ProviderCallMetadata(
            input_tokens=int(usage.prompt_tokens)
            if getattr(usage, "prompt_tokens", None) is not None
            else None,
            output_tokens=int(usage.completion_tokens)
            if getattr(usage, "completion_tokens", None) is not None
            else None,
            usage_source="provider" if usage is not None else "estimated_chars",
            prompt_text=json.dumps(messages, ensure_ascii=True),
            response_text=content,
        )

    def _text_generation(
        self,
        *,
        client,
        prompt: str,
    ) -> tuple[ProverAction, ProviderCallMetadata]:
        raw_text = client.text_generation(
            prompt,
            max_new_tokens=800,
            temperature=0.1,
            return_full_text=False,
            details=True,
            decoder_input_details=True,
        )
        generated_text = getattr(raw_text, "generated_text", None)
        details = getattr(raw_text, "details", None)
        response_text = str(generated_text if generated_text is not None else raw_text)
        return ProverAction.model_validate(
            _extract_json_payload(response_text)
        ), ProviderCallMetadata(
            input_tokens=len(getattr(details, "prefill", []) or [])
            if details is not None
            else None,
            output_tokens=getattr(details, "generated_tokens", None)
            if details is not None
            else None,
            usage_source="provider" if details is not None else "estimated_chars",
            prompt_text=prompt,
            response_text=response_text,
        )

    def _should_fallback_to_text_generation(self, error: Exception) -> bool:
        message = str(error).lower()
        return (
            "supported task: text-generation" in message
            or "supported task: text generation" in message
        )

    def next_action(
        self,
        *,
        backend: ProverBackend,
        prompt: str,
    ) -> ProverAction | tuple[ProverAction, ProviderCallMetadata]:
        try:
            from huggingface_hub import InferenceClient
        except Exception as error:
            raise ProverDriverError(
                "huggingface_hub is required for Hugging Face prover backends."
            ) from error

        try:
            client = InferenceClient(
                model=backend.model,
                token=self.token,
                timeout=self.timeout,
                provider=self.inference_provider,
            )
            try:
                return self._chat_completion(client=client, prompt=prompt)
            except Exception as error:
                if not self._should_fallback_to_text_generation(error):
                    raise
                return self._text_generation(client=client, prompt=prompt)
        except Exception as error:
            raise ProverDriverError(
                f"Prover backend invocation failed for {backend.model}: {error}"
            ) from error

class MistralProverDriver:
    """Mistral chat-completions driver (provider tag: ``mistral``).

    Not coupled to a specific model — any Mistral-hosted chat-completions
    model can be driven through this transport, including Leanstral.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = FORMALIZER_TIMEOUT,
    ) -> None:
        self.api_key = api_key if api_key is not None else MISTRAL_API_KEY
        resolved_base = base_url if base_url is not None else MISTRAL_BASE_URL
        self.base_url = resolved_base.rstrip("/")
        self.timeout = timeout

    def next_action(
        self,
        *,
        backend: ProverBackend,
        prompt: str,
    ) -> ProverAction | tuple[ProverAction, ProviderCallMetadata]:
        if not self.api_key:
            raise ProverDriverError(
                f"MISTRAL_API_KEY is required for the `{backend.name}` prover backend (provider=mistral)."
            )
        payload = json.dumps(
            {
                "model": backend.model,
                "temperature": 0.1,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a Lean theorem prover. Return only JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        request_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/chat/completions"
        raw = self._post_with_retry(url=url, payload=payload, headers=request_headers)

        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProverDriverError("Mistral prover response did not contain choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) for part in content if isinstance(part, dict)
            )
        if not isinstance(content, str):
            raise ProverDriverError("Mistral prover response did not contain text content.")
        usage = raw.get("usage", {}) if isinstance(raw.get("usage"), dict) else {}
        return ProverAction.model_validate(_extract_json_payload(content)), ProviderCallMetadata(
            input_tokens=int(usage.get("prompt_tokens"))
            if usage.get("prompt_tokens") is not None
            else None,
            output_tokens=int(usage.get("completion_tokens"))
            if usage.get("completion_tokens") is not None
            else None,
            usage_source="provider" if usage else "estimated_chars",
            prompt_text=prompt,
            response_text=content,
        )

    def _post_with_retry(
        self,
        *,
        url: str,
        payload: bytes,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(PROVER_RETRY_ATTEMPTS):
            request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            error_code = "unknown"
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="ignore")
                wrapped = ProverDriverError(
                    f"Mistral prover request failed: {body or error.reason}"
                )
                wrapped.__cause__ = error
                error_code = _classify_http_status(error.code)
                last_error = wrapped
            except urllib.error.URLError as error:
                wrapped = ProverDriverError(f"Mistral prover request failed: {error.reason}")
                wrapped.__cause__ = error
                error_code = (
                    "timeout"
                    if "timed out" in str(error.reason).lower()
                    else "provider_unavailable"
                )
                last_error = wrapped
            except TimeoutError as error:
                wrapped = ProverDriverError(f"Mistral prover request timed out: {error}")
                wrapped.__cause__ = error
                error_code = "timeout"
                last_error = wrapped

            if error_code not in PROVER_RETRYABLE_ERROR_CODES:
                raise last_error
            if attempt + 1 >= PROVER_RETRY_ATTEMPTS:
                break
            backoff = PROVER_RETRY_BACKOFF_SECONDS[
                min(attempt, len(PROVER_RETRY_BACKOFF_SECONDS) - 1)
            ]
            time.sleep(backoff)

        assert last_error is not None
        raise last_error

def _unwrap_action_response(
    value: ProverAction | tuple[ProverAction, ProviderCallMetadata],
) -> tuple[ProverAction, ProviderCallMetadata | None]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], ProviderCallMetadata):
        return value[0], value[1]
    return value, None

