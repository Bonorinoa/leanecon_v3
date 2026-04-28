"""Packet-aware prover with self-correction and bounded APOLLO decomposition."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Literal, Protocol

from src.config import (
    BENCHMARK_MAX_RECURSION_DEPTH,
    FORMALIZER_TIMEOUT,
    HF_TOKEN,
    MISTRAL_API_KEY,
    MISTRAL_BASE_URL,
    PROVER_BACKEND,
    PROVER_FALLBACK_BACKEND,
    PROVER_PROVIDER,
)
from src.formalizer.models import FormalizationPacket
from src.lean import LeanREPLSession, compile_check, lean_run_code
from src.memory import ProofTraceStore, trace_store as default_trace_store
from src.prover.lsp_cache import LSPCache
from src.utils.json_extraction import extract_json_object
from src.observability import (
    AuditEvent,
    BudgetTracker,
    LeanLSPClient,
    LeanLSPUnavailableError,
    LeanSearchFailureEvent,
    ProgressDelta,
    ProviderCallMetadata,
    RetrievalEvent,
    SpanRecorder,
    StateTransition,
    TokenUsage,
    ToolUsageTrace,
    build_progress_event,
    classify_exception,
    complete_usage,
    default_lean_lsp_client,
    log_event,
    stable_hash_text,
)
from src.prover.file_controller import ProofFileController
from src.prover.memory_writer import ProverMemoryWriter
from src.prover.models import (
    ProverAction,
    ProverFailure,
    ProverResult,
    ProverTarget,
    ProverTargetTimeouts,
    ProverToolInvocation,
    ProverTraceStep,
)
from src.prover.tactics import (
    classify_goal_shape,
    direct_hypothesis_name,
    failure_feedback_messages,
    goal_shape_scaffold,
    intro_names_from_body,
    normalized_diagnostic_signature,
    normalized_goal_text,
    repeated_noop_action,
    should_decompose,
    suggest_fast_path_tactics,
    theorem_explicit_parameter_names,
    theorem_goal_statement,
    theorem_parameter_names,
    validate_action,
)
from src.providers import normalize_huggingface_provider
from src.observability.tool_tracker import LSP_TOOL_NAMES, NATIVE_SEARCH_TOOL_NAMES
from src.tools import ToolCall, ToolRegistry, ToolResult, build_default_registry


SHORTCUT_ATTEMPT_TIMEOUT_SECONDS = 25
MAX_DIRECT_CLOSURE_CANDIDATES = 24
POST_DIRECT_CLOSURE_STALL_LIMIT = 2
SHALLOW_LOOP_WINDOW = 4

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


SHORTCUT_FALLBACK_TACTICS: tuple[tuple[str, str], ...] = (
    ("assumption", "Goal matches a local hypothesis; closing via `assumption`."),
    ("rfl", "Goal closes by definitional reflexivity."),
    ("exact?", "Library search closed the goal via `exact?`."),
    ("decide", "Goal is decidable; closing via `decide`."),
    ("norm_num", "Numerical goal closes via `norm_num`."),
    ("simp", "Goal closes after `simp` normalization."),
    ("linarith", "Linear-arithmetic closure via `linarith`."),
)

_PROGRESS_CODE_WINDOW = 240
_HINT_FIRST_PREAMBLES = frozenset(
    {
        "continuous_preference",
        "convex_preference",
        "constrained_optimization",
        "kuhn_tucker",
        "value_function",
    }
)
_WRAPPER_SIMPA_SHAPES = frozenset({"Monotone", "Antitone", "StrictMono", "StrictAnti"})
_RECOGNIZED_CLAIM_TYPES = frozenset({"preamble_definable", "mathlib_native"})
MATHLIB_NATIVE_DIRECT_CLOSE_LIMIT = 2
MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT = 6
MATHLIB_NATIVE_LSP_SEARCH_RESULTS = 8
MATHLIB_NATIVE_PROMPT_ONLY_TOOLS = frozenset(
    {
        "lean_diagnostic_messages",
        "lean_leansearch",
        "lean_loogle",
        "lean_local_search",
        "lean_file_outline",
    }
)


class ProverDriverError(RuntimeError):
    """Raised when a prover backend cannot complete a request."""


@dataclass(frozen=True)
class ProverBackend:
    name: str
    provider: str
    model: str
    notes: str


ClaimType = Literal["preamble_definable", "mathlib_native"]


@dataclass(frozen=True)
class DirectClosePolicy:
    claim_type: ClaimType | None
    claim_type_policy: str
    attempt_cap: int
    preamble_shortcuts_enabled: bool


@dataclass(frozen=True)
class DirectCloseAttemptSummary:
    candidate_count: int
    attempt_limit: int
    attempts_used: int
    claim_type: ClaimType | None
    claim_type_policy: str
    preamble_shortcuts_enabled: bool

    @property
    def exhausted(self) -> bool:
        return self.candidate_count > 0 and self.attempts_used >= self.attempt_limit

    def metadata(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "attempt_limit": self.attempt_limit,
            "attempts_used": self.attempts_used,
            "claim_type": self.claim_type,
            "claim_type_policy": self.claim_type_policy,
            "preamble_shortcuts_enabled": self.preamble_shortcuts_enabled,
        }


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


def _contains_lsp_unavailable(value: Any) -> bool:
    if isinstance(value, str):
        return "lsp_unavailable" in value
    if isinstance(value, dict):
        return any(_contains_lsp_unavailable(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_lsp_unavailable(child) for child in value)
    return False


# Stage 2 P1.A: identifier extraction for hypothesis-aware leansearch queries.
# Matches Mathlib-style CamelCase tokens (e.g. IsCompact, BddAbove, ContinuousOn,
# Tendsto). Excludes single capitals like "P", "Q", "S" (variable placeholders).
_MATHLIB_IDENT_RE = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)*)\b")
_MATHLIB_IDENT_STOPWORDS = frozenset(
    {"True", "False", "None", "Type", "Prop", "Sort", "Set", "Nat", "Int", "Real"}
)


def _extract_mathlib_idents(text: str) -> list[str]:
    """Return Mathlib-style CamelCase identifiers in *text*, in first-seen order.

    Used to build refined leansearch queries from goal/hypothesis context.
    Stopwords filter out common non-discriminating types so queries stay
    targeted on lemma-bearing identifiers like ``IsCompact``/``Monotone``.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _MATHLIB_IDENT_RE.finditer(text):
        ident = match.group(1)
        if ident in _MATHLIB_IDENT_STOPWORDS:
            continue
        # Require at least one lowercase letter to skip pure-acronym noise like "BBB".
        if ident not in seen:
            seen[ident] = None
    return list(seen.keys())


# Stage 2-followup D: rescue path when the model hallucinates an identifier.
# Lean reports e.g. ``unknown identifier 'monotone_bddAbove_converges'`` —
# we lift the missing name out of the error text so a second-pass LeanSearch
# can retry on the concept tokens that gave the model the right *idea*.
_UNKNOWN_IDENT_RE = re.compile(
    r"unknown\s+identifier\s+[`'\"]([A-Za-z_][A-Za-z0-9_.']*)[`'\"]",
    re.IGNORECASE,
)


def _extract_unknown_identifier(error_text: str) -> str | None:
    if not error_text:
        return None
    match = _UNKNOWN_IDENT_RE.search(error_text)
    return match.group(1) if match else None


def _query_from_failed_identifier(ident: str) -> str:
    """Split a snake_case/CamelCase identifier into a LeanSearch query."""
    if not ident:
        return ""
    parts = [p for p in ident.replace(".", "_").split("_") if p]
    if not parts:
        return ""
    joined = " ".join(parts)
    if not any(kw in joined.lower() for kw in ("theorem", "lemma", "prove")):
        joined = f"{joined} theorem"
    return joined[:200]


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


def _count_standalone_sorries(code: str) -> int:
    return sum(1 for line in code.splitlines() if line.strip() == "sorry")


def _replace_last_sorry(code: str, replacement: str) -> str:
    lines = code.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if line.strip() != "sorry":
            continue
        indent = line[: len(line) - len(line.lstrip())]
        replacement_lines = [f"{indent}{part}" for part in replacement.splitlines()]
        return "\n".join(lines[:index] + replacement_lines + lines[index + 1 :]) + "\n"
    raise ValueError("No standalone `sorry` found.")


def _replace_named_theorem_body(code: str, theorem_name: str, replacement: str) -> str:
    declaration = re.search(rf"(?m)^(theorem|lemma)\s+{re.escape(theorem_name)}\b", code)
    if declaration is None:
        raise ValueError(f"Could not locate theorem `{theorem_name}`.")
    header = re.search(r":=\s*by\s*\n", code[declaration.start() :], re.DOTALL)
    if header is None:
        raise ValueError(f"Could not locate proof body for `{theorem_name}`.")
    body_start = declaration.start() + header.end()
    replacement_block = "\n".join(f"  {part}" for part in replacement.splitlines())
    return f"{code[:body_start]}{replacement_block}\n"


def _proof_body_fingerprint(code: str, theorem_name: str) -> str:
    try:
        body_code = _replace_named_theorem_body(code, theorem_name, "__FINGERPRINT__")
    except ValueError:
        return code[-_PROGRESS_CODE_WINDOW:]
    marker = "__FINGERPRINT__"
    if marker not in body_code:
        return code[-_PROGRESS_CODE_WINDOW:]
    return body_code.split(marker, 1)[1][:_PROGRESS_CODE_WINDOW]


def _extract_theorem_block(code: str) -> str:
    for marker in ("/--", "theorem ", "lemma "):
        match = re.search(rf"(?m)^{re.escape(marker)}", code)
        if match is not None:
            return code[match.start() :].strip() + "\n"
    return code.strip() + "\n"


def _inject_theorem_before_main(current_code: str, theorem_block: str) -> str:
    match = re.search(r"(?m)^(/--|theorem |lemma )", current_code)
    if match is None:
        return f"{theorem_block.rstrip()}\n\n{current_code.rstrip()}\n"
    return (
        current_code[: match.start()].rstrip()
        + "\n\n"
        + theorem_block.rstrip()
        + "\n\n"
        + current_code[match.start() :].lstrip()
    )


def _replace_subgoal_with_helper(current_code: str, subgoal_name: str, helper_name: str) -> str:
    pattern = re.compile(
        rf"(?m)^(\s*have\s+{re.escape(subgoal_name)}\s*:\s*.+?\s*:=\s*by\s*\n)(\s*)sorry\s*$"
    )
    match = pattern.search(current_code)
    if match is None:
        return current_code
    replacement = f"{match.group(1)}{match.group(2)}exact {helper_name}"
    return current_code[: match.start()] + replacement + current_code[match.end() :]


def _standalone_theorem_code(packet: FormalizationPacket, theorem_name: str, statement: str) -> str:
    lines = [*(f"import {module}" for module in packet.imports)]
    if packet.open_statements:
        lines.append("")
        lines.extend(f"open {statement_}" for statement_ in packet.open_statements)
    lines.append("")
    lines.append(f"theorem {theorem_name} : {statement} := by")
    lines.append("  sorry")
    lines.append("")
    return "\n".join(lines)


def _build_prompt(
    *,
    packet: FormalizationPacket,
    target: ProverTarget,
    current_code: str,
    tool_specs: list[dict[str, Any]],
    lean_feedback: list[str],
    goals: list[str],
    prior_trace: list[ProverTraceStep],
    examples: list[dict[str, Any]],
    turn_hints: list[str] | None = None,
) -> str:
    preferred_tactics = list(
        dict.fromkeys([*(turn_hints or []), *suggest_fast_path_tactics(current_code)])
    )
    recent_steps = [
        {
            "turn": step.turn,
            "backend": step.backend,
            "action_type": step.action_type,
            "tool_name": step.tool_name,
            "success": step.success,
            "tool_result": step.tool_result,
            "lean_feedback": step.lean_feedback,
        }
        for step in prior_trace[-3:]
    ]
    prompt_payload = {
        "claim": packet.claim,
        "theorem_name": packet.theorem_name,
        "claim_type": getattr(packet, "claim_type", None),
        "selected_preamble": packet.selected_preamble,
        "target": target.model_dump(mode="json"),
        "current_code": current_code,
        "lean_feedback": lean_feedback,
        "goals": goals,
        "memory_examples": examples,
        "tools": tool_specs,
        "recent_trace": recent_steps,
        "instructions": {
            "return_json_only": True,
            "action_type": ["tool", "decompose", "finish"],
            "preferred_tactics": preferred_tactics,
            "rules": [
                "All Lean actions must go through a registered tool.",
                "Prefer apply_tactic before rewriting full code.",
                "Do not repeat the same failed tool call twice.",
                "Use decomposition only when the target is stalled.",
            ],
        },
        "response_schema": {
            "action_type": "tool|decompose|finish",
            "rationale": "string",
            "tool": {"name": "registered tool", "arguments": {}},
            "decomposition_statement": "Lean proposition when action_type=decompose",
            "decomposition_name": "optional theorem name",
            "finish_reason": "string when action_type=finish",
        },
    }
    return json.dumps(prompt_payload, ensure_ascii=True, indent=2)


@dataclass
class _ActiveProofSession:
    """Mutable proof target state backed by LeanInteract when available."""

    code: str
    timeout: int
    repl: Any = None
    proof_path: Path | None = None
    materialize_code: Callable[[str], str] | None = None
    active_repl: bool = False
    goals: list[str] | None = None
    solved: bool = False

    def __post_init__(self) -> None:
        if self.proof_path is not None:
            self.proof_path.write_text(self.code, encoding="utf-8")
        self._restart_repl()

    def read_code(self) -> str:
        return self.code

    def write_code(self, code: str) -> None:
        self.code = code
        if self.proof_path is not None:
            self.proof_path.write_text(self.code, encoding="utf-8")
        self._restart_repl()

    def compile_current_code(self) -> dict[str, Any]:
        return compile_check(self.code, timeout=self.timeout)

    def run_code(self, code: str | None = None) -> dict[str, Any]:
        return lean_run_code(code or self.code, timeout=self.timeout)

    def get_goals(self) -> list[str]:
        return list(self.goals or [])

    def apply_tactic(self, tactic: str) -> tuple[bool, str]:
        if not self.active_repl or self.repl is None:
            return False, "Lean REPL is unavailable for the current code state."
        response = self.repl.apply_tactic(tactic, timeout=self.timeout)
        if isinstance(response, Exception):
            return False, str(response)
        if hasattr(response, "has_errors") and response.has_errors():
            errors = [
                message.data for message in response.get_errors() if getattr(message, "data", "")
            ]
            return False, "\n".join(errors) if errors else f"Tactic failed: {tactic}"
        materialized = self.repl.materialize_proof()
        self.code = (
            self.materialize_code(materialized)
            if self.materialize_code is not None
            else materialized
        )
        if self.proof_path is not None:
            self.proof_path.write_text(self.code, encoding="utf-8")
        self.goals = list(getattr(response, "goals", []) or [])
        self.solved = getattr(response, "proof_status", "") == "Completed" or not self.goals
        return True, "All goals solved." if self.solved else "\n".join(self.goals)

    def close(self) -> None:
        if self.repl is not None:
            try:
                self.repl.kill()
            except Exception:
                pass
        self.repl = None
        self.active_repl = False

    def _restart_repl(self) -> None:
        self.close()
        self.solved = False
        self.goals = []
        if LeanREPLSession is None or _count_standalone_sorries(self.code) != 1:
            return
        try:
            repl = LeanREPLSession(timeout=self.timeout)
            state = repl.start_proof(self.code, timeout=self.timeout)
        except Exception:
            return
        self.repl = repl
        self.active_repl = True
        self.goals = list(getattr(state, "goals", []) or [])
        self.solved = bool(getattr(state, "is_solved", False))


class Prover:
    """Main proving orchestrator with self-correction and bounded decomposition."""

    def __init__(
        self,
        *,
        backend: str = PROVER_BACKEND,
        huggingface_driver: ProverDriver | None = None,
        mistral_driver: ProverDriver | None = None,
        registry: ToolRegistry | None = None,
        file_controller: ProofFileController | None = None,
        trace_store: ProofTraceStore | None = None,
        budget_tracker: BudgetTracker | None = None,
        lsp_client: LeanLSPClient | None = None,
    ) -> None:
        self.registry = registry or build_default_registry()
        self.driver_registry = DriverRegistry()
        self.primary_backend = self.driver_registry.get(backend)
        fallback_name = PROVER_FALLBACK_BACKEND
        if fallback_name not in self.driver_registry.available():
            fallback_name = "leanstral"
        self.fallback_backend = self.driver_registry.get(fallback_name)
        self._drivers: dict[str, ProverDriver] = {
            "huggingface": huggingface_driver or HuggingFaceProverDriver(),
            "mistral": mistral_driver or MistralProverDriver(),
        }
        self.file_controller = file_controller or ProofFileController()
        self.trace_store = trace_store or default_trace_store
        self.budget_tracker = budget_tracker or BudgetTracker()
        self.memory_writer = ProverMemoryWriter(self.trace_store)
        self.lsp_client = lsp_client or default_lean_lsp_client
        self._extracted_lemmas = 0
        self._retrieval_events: list[dict[str, Any]] = []
        self._tool_usage_traces: list[dict[str, Any]] = []
        self._state_transitions: list[dict[str, Any]] = []
        self._progress_deltas: list[dict[str, Any]] = []
        # Stage 2-followup C: track which (claim_id, target_name) pairs have
        # already triggered a second-pass refined retrieval. Ensures we fire
        # at most once per target even after dropping the strict turn==1 gate.
        self._second_retrieval_targets: set[tuple[str, str]] = set()
        # Stage 2-followup D: track which (claim_id, target_name) pairs have
        # already had an unknown-identifier rescue retrieval, so the rescue
        # fires at most once per target.
        self._rescue_retrieval_targets: set[tuple[str, str]] = set()
        # Sprint 24: per-prove LSP outline/hover cache + premise enrichment.
        # The cache is constructed lazily via ``_get_lsp_cache`` because
        # ``self.lsp_client`` may be swapped by tests *after* ``__init__``.
        self._lsp_cache: LSPCache | None = None

    def _selected_preamble_entries(self, packet: FormalizationPacket) -> list[Any]:
        from src.preamble_library import PREAMBLE_LIBRARY

        entries: list[Any] = []
        for name in packet.selected_preamble:
            entry = PREAMBLE_LIBRARY.get(name)
            if entry is not None:
                entries.append(entry)
        return entries

    def _normalized_claim_type(self, packet: FormalizationPacket) -> ClaimType | None:
        claim_type = getattr(packet, "claim_type", None)
        if claim_type in _RECOGNIZED_CLAIM_TYPES:
            return claim_type
        return None

    def _direct_close_policy(self, packet: FormalizationPacket) -> DirectClosePolicy:
        # Keep claim-type handling centralized: mathlib-native claims may use a
        # small compile-checked direct-close budget, but Preamble-derived
        # shortcuts stay disabled so failures honestly reflect missing Mathlib
        # search strategy rather than accidental LeanEcon lemma reuse.
        claim_type = self._normalized_claim_type(packet)
        if claim_type == "mathlib_native":
            return DirectClosePolicy(
                claim_type=claim_type,
                claim_type_policy="mathlib_native_cap_2_no_preamble_shortcuts",
                attempt_cap=MATHLIB_NATIVE_DIRECT_CLOSE_LIMIT,
                preamble_shortcuts_enabled=False,
            )
        if claim_type == "preamble_definable":
            return DirectClosePolicy(
                claim_type=claim_type,
                claim_type_policy="preamble_definable_default",
                attempt_cap=MAX_DIRECT_CLOSURE_CANDIDATES,
                preamble_shortcuts_enabled=True,
            )
        return DirectClosePolicy(
            claim_type=None,
            claim_type_policy="default",
            attempt_cap=MAX_DIRECT_CLOSURE_CANDIDATES,
            preamble_shortcuts_enabled=True,
        )

    async def prove(
        self,
        packet: FormalizationPacket,
        job_id: str,
        *,
        max_turns: int = 8,
        timeout: int = 300,
        target_timeouts: ProverTargetTimeouts | None = None,
        allow_decomposition: bool = True,
        benchmark_mode: bool = False,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> ProverResult:
        telemetry = SpanRecorder()
        trace: list[ProverTraceStep] = []
        provider_usage: list[TokenUsage] = []
        audit_events: list[AuditEvent] = []
        targets = self._build_targets(packet)
        attempted_backends: list[str] = []
        working_code = packet.lean_code
        resolved_target_timeouts = self._resolve_target_timeouts(
            timeout=timeout, target_timeouts=target_timeouts
        )
        final_compile_timeout = self._final_compile_timeout(resolved_target_timeouts)
        max_recursion_depth = BENCHMARK_MAX_RECURSION_DEPTH if benchmark_mode else 3
        self._extracted_lemmas = 0
        self._reset_budget_tracker()
        # Sprint 23 Task 3: bump budgets for mathlib_native claims (preamble untouched).
        self._apply_budget_limits_for_packet(packet)
        self._retrieval_events = []
        self._tool_usage_traces = []
        self._state_transitions = []
        self._progress_deltas = []
        self._second_retrieval_targets = set()
        self._rescue_retrieval_targets = set()
        self.file_controller.initialize(job_id, working_code)

        try:
            failure: ProverFailure | None = None
            verified_via = "full_pipeline"

            shortcut = None
            if not benchmark_mode:
                shortcut = self._try_trivial_shortcut(
                    packet=packet,
                    current_code=working_code,
                    timeout=self._timeout_for_target(targets[-1], resolved_target_timeouts),
                )
            if shortcut is not None:
                verified_via = "trivial_shortcut"
                working_code = shortcut["code"]
                self._emit_progress(
                    on_progress,
                    "prover_tool",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message="Closed via trivial shortcut.",
                    metadata={"tool_name": "compile_check", "shortcut": shortcut["tactic"]},
                )
                for target in targets:
                    target.status = "proved"
                if not attempted_backends:
                    attempted_backends.append(self.primary_backend.name)
                shortcut_rationale = shortcut.get("rationale") or (
                    f"Closed via `{shortcut['tactic']}`."
                )
                trace.append(
                    ProverTraceStep(
                        turn=1,
                        backend=self.primary_backend.name,
                        target_name="theorem_body",
                        action_type="trivial_shortcut",
                        success=True,
                        rationale=shortcut_rationale,
                        tool_name="compile_check",
                        tool_result=f"All goals solved via shortcut tactic `{shortcut['tactic']}`.",
                        code_snapshot=working_code,
                    )
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="trivial_shortcut",
                        provider=self.primary_backend.provider,
                        model=self.primary_backend.model,
                        success=True,
                        metadata={
                            "hypothesis": shortcut["hypothesis"],
                            "tactic": shortcut["tactic"],
                            "targets_skipped": len(targets),
                        },
                    )
                )
                self.file_controller.write_current_code(job_id, working_code)
                targets_to_iterate: list[ProverTarget] = []
            else:
                targets_to_iterate = targets

            for index, target in enumerate(targets_to_iterate, start=1):
                target.status = "in_progress"
                self._emit_progress(
                    on_progress,
                    "prover_turn",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message=f"Starting target `{target.name}`.",
                    metadata={
                        "turn": index,
                        "target_name": target.name,
                        "target_kind": target.kind,
                    },
                )
                target_timeout = self._timeout_for_target(target, resolved_target_timeouts)
                if target.kind == "subgoal":
                    helper_name = f"proved_{packet.theorem_name}_{index}"
                    target.helper_theorem_name = helper_name
                    target_code = _standalone_theorem_code(packet, helper_name, target.statement)
                    proved, produced_code, target_failure = await self._prove_target(
                        packet=packet,
                        target=target,
                        current_code=target_code,
                        trace=trace,
                        job_id=job_id,
                        attempted_backends=attempted_backends,
                        max_turns=max_turns,
                        timeout=target_timeout,
                        target_timeouts=resolved_target_timeouts,
                        allow_decomposition=allow_decomposition,
                        max_recursion_depth=max_recursion_depth,
                        telemetry=telemetry,
                        provider_usage=provider_usage,
                        audit_events=audit_events,
                        on_progress=on_progress,
                    )
                    if not proved:
                        target.status = "failed"
                        failure = target_failure
                        break
                    theorem_block = _extract_theorem_block(produced_code)
                    working_code = _inject_theorem_before_main(working_code, theorem_block)
                    working_code = _replace_subgoal_with_helper(
                        working_code, target.name, helper_name
                    )
                    self.file_controller.write_current_code(job_id, working_code)
                    target.status = "proved"
                    continue

                proved, produced_code, target_failure = await self._prove_target(
                    packet=packet,
                    target=target,
                    current_code=working_code,
                    trace=trace,
                    job_id=job_id,
                    attempted_backends=attempted_backends,
                    max_turns=max_turns,
                    timeout=target_timeout,
                    target_timeouts=resolved_target_timeouts,
                    allow_decomposition=allow_decomposition,
                    max_recursion_depth=max_recursion_depth,
                    telemetry=telemetry,
                    provider_usage=provider_usage,
                    audit_events=audit_events,
                    on_progress=on_progress,
                )
                if not proved:
                    target.status = "failed"
                    failure = target_failure
                    working_code = produced_code
                    break
                working_code = produced_code
                self.file_controller.write_current_code(job_id, working_code)
                target.status = "proved"

            compile_started_at = time.perf_counter()
            final_compile = compile_check(
                working_code,
                timeout=final_compile_timeout,
                filename=f"{job_id}_final.lean",
            )
            telemetry.record_lean(compile_started_at)
            self._enrich_trace_context(packet=packet, targets=targets, trace=trace)
            stage_usage = self._aggregate_stage_usage(provider_usage)
            timing_breakdown = {
                "prover_ms": telemetry.snapshot()["wall_clock_ms"],
                "total_ms": telemetry.snapshot()["wall_clock_ms"],
            }

            if failure is None and final_compile["success"]:
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="stage_completed",
                        provider=stage_usage.provider
                        if stage_usage is not None
                        else self.primary_backend.provider,
                        model=stage_usage.model
                        if stage_usage is not None
                        else self.primary_backend.model,
                        success=True,
                        metadata={
                            "termination_reason": "verified",
                            "attempted_backends": attempted_backends,
                            "benchmark_mode": benchmark_mode,
                            "target_timeouts": resolved_target_timeouts.model_dump(mode="json"),
                        },
                    )
                )
                result = ProverResult(
                    status="verified",
                    theorem_name=packet.theorem_name,
                    claim=packet.claim,
                    benchmark_mode=benchmark_mode,
                    verified_via=verified_via,
                    verified_code=working_code,
                    current_code=working_code,
                    trace=trace,
                    targets=targets,
                    failure=None,
                    termination_reason="verified",
                    repair_count=sum(1 for step in trace if not step.success),
                    preamble_names=list(packet.selected_preamble),
                    backend_used=attempted_backends[-1]
                    if attempted_backends
                    else self.primary_backend.name,
                    attempted_backends=attempted_backends,
                    tool_budget=self.budget_tracker.snapshot(),
                    telemetry=telemetry.snapshot(),
                    usage_by_stage={"prover": stage_usage.to_dict()}
                    if stage_usage is not None
                    else {},
                    timing_breakdown=timing_breakdown,
                    target_timeouts=resolved_target_timeouts,
                    audit_summary=self._audit_summary(audit_events),
                    retrieval_events=list(self._retrieval_events),
                    tool_usage_traces=list(self._tool_usage_traces),
                    state_transitions=list(self._state_transitions),
                    progress_deltas=list(self._progress_deltas),
                )
                log_event(
                    "prover.stage_completed",
                    stage="prover",
                    provider=stage_usage.provider
                    if stage_usage is not None
                    else self.primary_backend.provider,
                    model=stage_usage.model
                    if stage_usage is not None
                    else self.primary_backend.model,
                    latency_ms=timing_breakdown["prover_ms"],
                    input_tokens=stage_usage.input_tokens if stage_usage is not None else None,
                    output_tokens=stage_usage.output_tokens if stage_usage is not None else None,
                    estimated_cost_usd=stage_usage.estimated_cost_usd
                    if stage_usage is not None
                    else None,
                )
                if not benchmark_mode:
                    self.memory_writer.record(packet, result)
                return result

            if failure is None:
                failure = ProverFailure(
                    reason="final_compile_failed",
                    error_code="compile_failed",
                    message="Proof search ended, but the final code did not compile cleanly.",
                    target_name="theorem_body",
                    backend=attempted_backends[-1]
                    if attempted_backends
                    else self.primary_backend.name,
                    lean_feedback=failure_feedback_messages(final_compile),
                )
            if self._normalized_claim_type(packet) == "mathlib_native":
                failure = self._normalize_mathlib_progress_failure(failure)
            audit_events.append(
                AuditEvent(
                    stage="prover",
                    event_type="stage_failed",
                    provider=stage_usage.provider
                    if stage_usage is not None
                    else self.primary_backend.provider,
                    model=stage_usage.model
                    if stage_usage is not None
                    else self.primary_backend.model,
                    success=False,
                    error_code=failure.error_code or failure.reason,
                    error_message=failure.message,
                    metadata={
                        "termination_reason": failure.reason,
                        "attempted_backends": attempted_backends,
                        "benchmark_mode": benchmark_mode,
                        "target_timeouts": resolved_target_timeouts.model_dump(mode="json"),
                    },
                )
            )

            result = ProverResult(
                status="failed",
                theorem_name=packet.theorem_name,
                claim=packet.claim,
                benchmark_mode=benchmark_mode,
                verified_via="full_pipeline",
                verified_code=None,
                current_code=working_code,
                trace=trace,
                targets=targets,
                failure=failure,
                termination_reason=failure.reason,
                repair_count=sum(1 for step in trace if not step.success),
                preamble_names=list(packet.selected_preamble),
                backend_used=attempted_backends[-1]
                if attempted_backends
                else self.primary_backend.name,
                attempted_backends=attempted_backends,
                tool_budget=self.budget_tracker.snapshot(),
                telemetry=telemetry.snapshot(),
                usage_by_stage={"prover": stage_usage.to_dict()} if stage_usage is not None else {},
                timing_breakdown=timing_breakdown,
                target_timeouts=resolved_target_timeouts,
                audit_summary=self._audit_summary(audit_events),
                retrieval_events=list(self._retrieval_events),
                tool_usage_traces=list(self._tool_usage_traces),
                state_transitions=list(self._state_transitions),
                progress_deltas=list(self._progress_deltas),
            )
            log_event(
                "prover.stage_failed",
                stage="prover",
                provider=stage_usage.provider
                if stage_usage is not None
                else self.primary_backend.provider,
                model=stage_usage.model if stage_usage is not None else self.primary_backend.model,
                latency_ms=timing_breakdown["prover_ms"],
                error_code=failure.error_code or failure.reason,
                input_tokens=stage_usage.input_tokens if stage_usage is not None else None,
                output_tokens=stage_usage.output_tokens if stage_usage is not None else None,
                estimated_cost_usd=stage_usage.estimated_cost_usd
                if stage_usage is not None
                else None,
            )
            if not benchmark_mode:
                self.memory_writer.record(packet, result)
            return result
        finally:
            if benchmark_mode:
                self.file_controller.cleanup(job_id)

    async def _prove_target(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        current_code: str,
        trace: list[ProverTraceStep],
        job_id: str,
        attempted_backends: list[str],
        max_turns: int,
        timeout: int,
        target_timeouts: ProverTargetTimeouts,
        allow_decomposition: bool,
        max_recursion_depth: int,
        telemetry: SpanRecorder,
        provider_usage: list[TokenUsage],
        audit_events: list[AuditEvent],
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[bool, str, ProverFailure | None]:
        theorem_name = self._target_theorem_name(packet, target)
        direct_close_policy = self._direct_close_policy(packet)
        # Keep a single explicit mode flag here so the next sprint can branch mathlib-native
        # search behavior without re-deriving it from raw claim metadata throughout the loop.
        mathlib_native_mode = direct_close_policy.claim_type == "mathlib_native"
        if mathlib_native_mode:
            self.budget_tracker.record_mathlib_native_mode_use()
        direct_close_budget = (
            {"remaining": direct_close_policy.attempt_cap} if mathlib_native_mode else None
        )
        if direct_close_policy.claim_type is not None:
            self._record_claim_type_awareness(
                trace=trace,
                audit_events=audit_events,
                backend=self.primary_backend,
                target=target,
                turn=1,
                job_id=job_id,
                on_progress=on_progress,
                policy=direct_close_policy,
                mathlib_native_mode=mathlib_native_mode,
            )
        direct_close, direct_close_summary = self._try_direct_definable_closure(
            packet=packet,
            target=target,
            current_code=current_code,
            timeout=timeout,
            job_id=job_id,
            on_progress=on_progress,
            attempt_budget=direct_close_budget,
        )
        if direct_close is not None:
            self._emit_progress(
                on_progress,
                "prover_tool",
                job_id=job_id,
                stage="prover",
                status="running_prover",
                message="Closed via direct definable closure.",
                metadata={
                    "target_name": target.name,
                    "tool_name": "compile_check",
                    "proof": direct_close["proof"],
                },
            )
            self._record_direct_definable_closure(
                trace=trace,
                audit_events=audit_events,
                backend=self.primary_backend,
                target=target,
                turn=1,
                current_code=direct_close["code"],
                lean_feedback=[],
                proof=direct_close["proof"],
                source=direct_close["source"],
                rationale=direct_close["rationale"],
                policy=direct_close_policy,
            )
            return True, direct_close["code"], None

        session = _ActiveProofSession(
            current_code,
            timeout,
            proof_path=self.file_controller.proof_path(job_id),
            materialize_code=lambda code: self.file_controller.build_final_code(job_id, code),
        )
        failed_turns = 0
        invalid_output_count = 0
        active_backend = self.primary_backend
        soft_repair_used = False
        no_progress_streak = 0
        last_progress_fingerprint: tuple[str, tuple[str, ...], tuple[str, ...], str] | None = None
        last_structural_state: tuple[str, tuple[str, ...]] | None = None
        structural_stall_streak = 0
        force_deterministic_recovery = False
        seen_failures: dict[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str], int] = {}
        scaffold_attempts: set[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str]] = set()
        branch_tactic_attempts: set[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str]] = (
            set()
        )
        exhausted_direct_closure_states: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        pending_direct_close_exhaustion = (
            direct_close_summary if direct_close_summary.exhausted else None
        )
        last_failure_signature: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str] | None = (
            None
        )
        repeated_failure_streak = 0
        last_schema_invalid_signature: (
            tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str] | None
        ) = None
        schema_invalid_repeats = 0
        lsp_search_attempted_states: set[tuple[str, tuple[str, ...]]] = set()

        try:
            for turn in range(1, max_turns + 1):
                if not self.budget_tracker.can_continue():
                    return (
                        False,
                        session.read_code(),
                        ProverFailure(
                            reason="tool_budget_exhausted",
                            message="Tool budget exhausted before the proof converged.",
                            target_name=target.name,
                            turn=turn,
                            backend=active_backend.name,
                        ),
                    )

                current_code = session.read_code()
                compile_result = session.compile_current_code()
                lean_feedback = failure_feedback_messages(compile_result)
                goals = session.get_goals()
                structural_state = self._structural_state_fingerprint(
                    session=session,
                    theorem_name=theorem_name,
                )
                if last_structural_state is None:
                    last_structural_state = structural_state
                if pending_direct_close_exhaustion is not None:
                    exhausted_direct_closure_states.setdefault(
                        structural_state,
                        pending_direct_close_exhaustion.metadata(),
                    )
                    pending_direct_close_exhaustion = None

                if compile_result["success"] and (not session.active_repl or not goals):
                    return True, current_code, None

                if not compile_result["success"]:
                    repeated_solved = self._repeated_solved_repl_tactic(trace=trace, target=target)
                    if repeated_solved:
                        if soft_repair_used:
                            recovered, recovery_summary = self._try_repl_compile_recovery(
                                packet=packet,
                                target=target,
                                current_code=self.file_controller.build_final_code(
                                    job_id, session.read_code()
                                ),
                                timeout=timeout,
                                include_fallback_tactics=force_deterministic_recovery,
                                attempt_budget=direct_close_budget,
                            )
                            if recovered is not None:
                                session.write_code(recovered["code"])
                                self._record_direct_definable_closure(
                                    trace=trace,
                                    audit_events=audit_events,
                                    backend=active_backend,
                                    target=target,
                                    turn=turn,
                                    current_code=session.read_code(),
                                    lean_feedback=lean_feedback,
                                    proof=recovered["proof"],
                                    source=recovered["source"],
                                    rationale=recovered["rationale"],
                                    policy=direct_close_policy,
                                )
                                return True, session.read_code(), None
                            if recovery_summary.exhausted:
                                exhausted_direct_closure_states[structural_state] = (
                                    recovery_summary.metadata()
                                )
                            disagreement_fail = self._detect_repl_compile_disagreement(
                                trace=trace,
                                target=target,
                                turn=turn,
                                backend=active_backend,
                                lean_feedback=lean_feedback,
                            )
                            if disagreement_fail is not None:
                                return False, session.read_code(), disagreement_fail
                        else:
                            repaired_code = session.read_code()
                            if repeated_solved.startswith("exact "):
                                repaired_code = _replace_named_theorem_body(
                                    repaired_code,
                                    self._target_theorem_name(packet, target),
                                    repeated_solved,
                                )
                            repaired_code = self.file_controller.build_final_code(
                                job_id, repaired_code
                            )
                            session.write_code(repaired_code)
                            soft_repair_used = True
                            trace.append(
                                ProverTraceStep(
                                    turn=turn,
                                    backend=active_backend.name,
                                    target_name=target.name,
                                    action_type="repl_compile_soft_repair",
                                    success=True,
                                    rationale="Rebuild the theorem context before treating the disagreement as fatal.",
                                    tool_name="apply_tactic",
                                    tool_arguments={"tactic": repeated_solved},
                                    tool_result=f"Soft-repaired REPL/global compile disagreement after `{repeated_solved}`.",
                                    lean_feedback=lean_feedback,
                                    goals=goals,
                                    code_snapshot=session.read_code(),
                                )
                            )
                            failed_turns = 0
                            continue

                has_targeted_recovery = (
                    structural_state not in exhausted_direct_closure_states
                    and self._has_direct_candidates(
                        packet=packet,
                        current_code=current_code,
                    )
                ) or self._has_targeted_fast_path(current_code)
                recovery_forced = force_deterministic_recovery
                repaired, repaired_code, repaired_failure = self._apply_deterministic_repair(
                    packet=packet,
                    target=target,
                    session=session,
                    trace=trace,
                    audit_events=audit_events,
                    backend=active_backend,
                    turn=turn,
                    timeout=timeout,
                    lean_feedback=lean_feedback,
                    include_fallback_tactics=force_deterministic_recovery
                    or failed_turns > 0
                    or no_progress_streak > 0,
                    scaffold_attempts=scaffold_attempts,
                    branch_tactic_attempts=branch_tactic_attempts,
                    exhausted_direct_closure_states=exhausted_direct_closure_states,
                    direct_close_budget=direct_close_budget,
                )
                force_deterministic_recovery = False
                if repaired_failure is not None:
                    return False, session.read_code(), repaired_failure
                if recovery_forced and repaired_code is None:
                    repeated_error_code = "unsolved_goals"
                    if last_failure_signature is not None and last_failure_signature[1].startswith(
                        "schema_invalid:"
                    ):
                        repeated_error_code = "schema_invalid"
                    return (
                        False,
                        session.read_code(),
                        ProverFailure(
                            reason="no_progress_stall",
                            message="Deterministic recovery could not change the repeated failing proof state.",
                            error_code=repeated_error_code,
                            target_name=target.name,
                            turn=turn,
                            backend=active_backend.name,
                            lean_feedback=lean_feedback,
                        ),
                    )
                if repaired_code is not None:
                    if repaired:
                        last_failure_signature = None
                        repeated_failure_streak = 0
                        return True, repaired_code, None
                    latest_step = trace[-1]
                    if latest_step.success:
                        failed_turns = 0
                        last_failure_signature = None
                        repeated_failure_streak = 0
                    else:
                        failed_turns += 1
                    fingerprint = self._progress_fingerprint(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        outcome=latest_step.error_code or latest_step.action_type,
                        lean_feedback=lean_feedback,
                    )
                    no_progress_streak = (
                        no_progress_streak + 1 if fingerprint == last_progress_fingerprint else 0
                    )
                    last_progress_fingerprint = fingerprint
                    structural_state = self._structural_state_fingerprint(
                        session=session,
                        theorem_name=theorem_name,
                    )
                    if structural_state == last_structural_state:
                        structural_stall_streak += 1
                    else:
                        structural_stall_streak = 0
                    last_structural_state = structural_state
                    continue

                if mathlib_native_mode and structural_state not in lsp_search_attempted_states:
                    lsp_search_attempted_states.add(structural_state)
                    harness_closed, harness_failure = self._try_mathlib_native_harness_loop(
                        packet=packet,
                        target=target,
                        session=session,
                        trace=trace,
                        audit_events=audit_events,
                        backend=active_backend,
                        attempted_backends=attempted_backends,
                        turn=turn,
                        timeout=timeout,
                        telemetry=telemetry,
                        provider_usage=provider_usage,
                        lean_feedback=lean_feedback,
                        goals=goals,
                        job_id=job_id,
                        on_progress=on_progress,
                    )
                    if harness_failure is not None:
                        return False, session.read_code(), harness_failure
                    if harness_closed is not None:
                        if active_backend.name not in attempted_backends:
                            attempted_backends.append(active_backend.name)
                        session.write_code(harness_closed)
                        return True, session.read_code(), None
                    lsp_closed = self._try_mathlib_native_lsp_search(
                        packet=packet,
                        target=target,
                        session=session,
                        trace=trace,
                        audit_events=audit_events,
                        backend=active_backend,
                        turn=turn,
                        timeout=timeout,
                        lean_feedback=lean_feedback,
                        goals=goals,
                        job_id=job_id,
                        on_progress=on_progress,
                    )
                    if lsp_closed is not None:
                        if active_backend.name not in attempted_backends:
                            attempted_backends.append(active_backend.name)
                        session.write_code(lsp_closed)
                        return True, session.read_code(), None

                prompt = _build_prompt(
                    packet=packet,
                    target=target,
                    current_code=current_code,
                    tool_specs=self._tool_specs_for_prompt(packet),
                    lean_feedback=lean_feedback,
                    goals=goals,
                    prior_trace=trace,
                    examples=self._memory_examples(packet),
                    turn_hints=self._first_turn_hints(packet) if turn == 1 else None,
                )

                try:
                    provider_started_at = time.perf_counter()
                    raw_action = self._drivers[active_backend.provider].next_action(
                        backend=active_backend,
                        prompt=prompt,
                    )
                    telemetry.record_provider(provider_started_at)
                    action, metadata = _unwrap_action_response(raw_action)
                    usage = complete_usage(
                        stage="prover",
                        provider=active_backend.provider,
                        model=active_backend.model,
                        latency_ms=(time.perf_counter() - provider_started_at) * 1000.0,
                        success=True,
                        metadata=metadata,
                        prompt_text=prompt,
                    )
                    provider_usage.append(usage)
                    audit_events.append(
                        AuditEvent(
                            stage="prover",
                            event_type="provider_turn",
                            provider=active_backend.provider,
                            model=active_backend.model,
                            success=True,
                            prompt_hash=stable_hash_text(
                                metadata.prompt_text if metadata is not None else prompt
                            ),
                            response_hash=stable_hash_text(
                                metadata.response_text if metadata is not None else None
                            ),
                            metadata={
                                "turn": turn,
                                "target_name": target.name,
                                "backend": active_backend.name,
                                "usage_source": usage.usage_source,
                            },
                        )
                    )
                    self._emit_progress(
                        on_progress,
                        "prover_turn",
                        job_id=job_id,
                        stage="prover",
                        status="running_prover",
                        message=f"Provider produced action `{action.action_type}`.",
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "backend": active_backend.name,
                            "action_type": action.action_type,
                        },
                    )
                except Exception as exc:
                    telemetry.record_provider(provider_started_at)
                    error_code = classify_exception(exc)
                    usage = complete_usage(
                        stage="prover",
                        provider=active_backend.provider,
                        model=active_backend.model,
                        latency_ms=(time.perf_counter() - provider_started_at) * 1000.0,
                        success=False,
                        error_code=error_code,
                        prompt_text=prompt,
                    )
                    provider_usage.append(usage)
                    audit_events.append(
                        AuditEvent(
                            stage="prover",
                            event_type="provider_turn",
                            provider=active_backend.provider,
                            model=active_backend.model,
                            success=False,
                            error_code=error_code,
                            error_message=str(exc),
                            prompt_hash=stable_hash_text(prompt),
                            metadata={
                                "turn": turn,
                                "target_name": target.name,
                                "backend": active_backend.name,
                            },
                        )
                    )
                    invalid_output_count += 1
                    if active_backend.name not in attempted_backends:
                        attempted_backends.append(active_backend.name)
                    if (
                        invalid_output_count >= 2
                        and active_backend.name != self.fallback_backend.name
                    ):
                        active_backend = self.fallback_backend
                        invalid_output_count = 0
                    trace.append(
                        ProverTraceStep(
                            turn=turn,
                            backend=active_backend.name,
                            target_name=target.name,
                            action_type="provider_error",
                            success=False,
                            rationale="Provider invocation failed.",
                            tool_result=str(exc),
                            lean_feedback=lean_feedback,
                            goals=goals,
                            code_snapshot=current_code,
                            error_code=error_code,
                        )
                    )
                    failed_turns += 1
                    failure_signature = self._failure_signature_key(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        lean_feedback=lean_feedback,
                        action_key=f"provider_error:{error_code}",
                    )
                    failure_repeats = self._register_failure_signature(
                        seen_failures=seen_failures,
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        lean_feedback=lean_feedback,
                        action_key=f"provider_error:{error_code}",
                    )
                    if failure_signature == last_failure_signature:
                        repeated_failure_streak += 1
                    else:
                        last_failure_signature = failure_signature
                        repeated_failure_streak = 1
                    failure_repeats = max(failure_repeats, repeated_failure_streak)
                    fingerprint = self._progress_fingerprint(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        outcome="provider_error",
                        lean_feedback=lean_feedback,
                    )
                    no_progress_streak = (
                        no_progress_streak + 1 if fingerprint == last_progress_fingerprint else 0
                    )
                    last_progress_fingerprint = fingerprint
                    if failure_repeats >= 3:
                        return (
                            False,
                            session.read_code(),
                            ProverFailure(
                                reason="no_progress_stall",
                                message="Provider failures repeated on the same proof state.",
                                error_code=error_code or "unsolved_goals",
                                target_name=target.name,
                                turn=turn,
                                backend=active_backend.name,
                                lean_feedback=lean_feedback,
                            ),
                        )
                    if failure_repeats == 2:
                        force_deterministic_recovery = True
                    continue

                if active_backend.name not in attempted_backends:
                    attempted_backends.append(active_backend.name)
                last_failure_signature = None
                repeated_failure_streak = 0

                validation_error = validate_action(action, self.registry)
                if validation_error is not None:
                    invalid_output_count += 1
                    trace.append(
                        ProverTraceStep(
                            turn=turn,
                            backend=active_backend.name,
                            target_name=target.name,
                            action_type=action.action_type,
                            success=False,
                            rationale=action.rationale,
                            tool_name=action.tool.name if action.tool is not None else None,
                            tool_arguments=action.tool.arguments if action.tool is not None else {},
                            tool_result=validation_error,
                            lean_feedback=lean_feedback,
                            goals=goals,
                            code_snapshot=current_code,
                            error_code="schema_invalid",
                        )
                    )
                    audit_events.append(
                        AuditEvent(
                            stage="prover",
                            event_type="validation_failed",
                            provider=active_backend.provider,
                            model=active_backend.model,
                            success=False,
                            error_code="schema_invalid",
                            error_message=validation_error,
                            metadata={
                                "turn": turn,
                                "target_name": target.name,
                                "backend": active_backend.name,
                            },
                        )
                    )
                    failed_turns += 1
                    if (
                        invalid_output_count >= 2
                        and active_backend.name != self.fallback_backend.name
                    ):
                        active_backend = self.fallback_backend
                        invalid_output_count = 0
                    failure_signature = self._failure_signature_key(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        lean_feedback=lean_feedback,
                        action_key=f"schema_invalid:{validation_error}",
                    )
                    failure_repeats = self._register_failure_signature(
                        seen_failures=seen_failures,
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        lean_feedback=lean_feedback,
                        action_key=f"schema_invalid:{validation_error}",
                    )
                    if failure_signature == last_failure_signature:
                        repeated_failure_streak += 1
                    else:
                        last_failure_signature = failure_signature
                        repeated_failure_streak = 1
                    failure_repeats = max(failure_repeats, repeated_failure_streak)
                    if failure_signature == last_schema_invalid_signature:
                        schema_invalid_repeats += 1
                    else:
                        last_schema_invalid_signature = failure_signature
                        schema_invalid_repeats = 1
                    fingerprint = self._progress_fingerprint(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        outcome="schema_invalid",
                        lean_feedback=lean_feedback,
                    )
                    no_progress_streak = (
                        no_progress_streak + 1 if fingerprint == last_progress_fingerprint else 0
                    )
                    last_progress_fingerprint = fingerprint
                    if failure_repeats >= 3 or schema_invalid_repeats >= 3:
                        return (
                            False,
                            session.read_code(),
                            ProverFailure(
                                reason="no_progress_stall",
                                message="Provider repeated schema-invalid actions on the same proof state.",
                                error_code="schema_invalid",
                                target_name=target.name,
                                turn=turn,
                                backend=active_backend.name,
                                lean_feedback=lean_feedback,
                            ),
                        )
                    if failure_repeats == 2:
                        force_deterministic_recovery = True
                    continue
                last_schema_invalid_signature = None
                schema_invalid_repeats = 0

                if repeated_noop_action(trace, action):
                    return (
                        False,
                        current_code,
                        ProverFailure(
                            reason="no_progress_stall",
                            message="The prover repeated a failed action without changing code or goals.",
                            error_code="unsolved_goals",
                            target_name=target.name,
                            turn=turn,
                            backend=active_backend.name,
                            lean_feedback=lean_feedback,
                            repeated_action=True,
                        ),
                    )

                if should_decompose(
                    failed_turns_for_target=failed_turns,
                    action=action,
                    allow_decomposition=allow_decomposition,
                    current_depth=target.recursion_depth,
                    total_extracted=self._extracted_lemmas,
                    no_progress_streak=no_progress_streak,
                    direct_candidates_available=has_targeted_recovery,
                    max_recursion_depth=max_recursion_depth,
                ):
                    decomposed, new_code = await self._run_decomposition(
                        packet=packet,
                        target=target,
                        session=session,
                        trace=trace,
                        attempted_backends=attempted_backends,
                        turn=turn,
                        target_timeouts=target_timeouts,
                        max_turns=max_turns,
                        action=action,
                        job_id=job_id,
                        max_recursion_depth=max_recursion_depth,
                        telemetry=telemetry,
                        provider_usage=provider_usage,
                        audit_events=audit_events,
                        on_progress=on_progress,
                    )
                    if decomposed:
                        target.status = "proved"
                        return True, new_code, None
                    return (
                        False,
                        session.read_code(),
                        ProverFailure(
                            reason="decomposition_limit_reached",
                            message="Decomposition did not produce a verified proof.",
                            error_code="unsolved_goals",
                            target_name=target.name,
                            turn=turn,
                            backend=active_backend.name,
                            lean_feedback=lean_feedback,
                        ),
                    )

                if action.action_type == "finish":
                    return (
                        False,
                        current_code,
                        ProverFailure(
                            reason="provider_finished_without_proof",
                            message=action.finish_reason
                            or "Provider stopped before the proof compiled.",
                            error_code="unsolved_goals",
                            target_name=target.name,
                            turn=turn,
                            backend=active_backend.name,
                            lean_feedback=lean_feedback,
                        ),
                    )

                assert action.tool is not None
                pre_tool_structural_state = structural_state
                tool_result = self._execute_tool(
                    session=session,
                    tool=action.tool,
                    packet=packet,
                    target=target,
                )
                step = ProverTraceStep(
                    turn=turn,
                    backend=active_backend.name,
                    target_name=target.name,
                    action_type=action.action_type,
                    success=not tool_result.is_error,
                    rationale=action.rationale,
                    tool_name=action.tool.name,
                    tool_arguments=action.tool.arguments,
                    tool_result=tool_result.content,
                    lean_feedback=lean_feedback,
                    goals=session.get_goals(),
                    code_snapshot=session.read_code(),
                    error_code=self._tool_error_code(action.tool.name, tool_result.content)
                    if tool_result.is_error
                    else None,
                    repl_local_solved=(
                        action.tool.name == "apply_tactic"
                        and not tool_result.is_error
                        and bool(session.solved)
                    ),
                )
                trace.append(step)
                self._emit_progress(
                    on_progress,
                    "prover_tool",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message=f"Tool `{action.tool.name}` executed.",
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        "tool_name": action.tool.name,
                        "success": not tool_result.is_error,
                        "error_code": step.error_code,
                    },
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="tool_result",
                        provider=active_backend.provider,
                        model=active_backend.model,
                        success=not tool_result.is_error,
                        error_code=step.error_code,
                        error_message=tool_result.content if tool_result.is_error else None,
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "backend": active_backend.name,
                            "tool_name": action.tool.name,
                        },
                    )
                )
                shallow_loop_pattern = self._detect_shallow_loop_pattern(trace=trace, target=target)
                if shallow_loop_pattern is not None:
                    if pre_tool_structural_state in exhausted_direct_closure_states:
                        return (
                            False,
                            session.read_code(),
                            self._direct_close_stall_failure(
                                target=target,
                                turn=turn,
                                backend=active_backend,
                                lean_feedback=session.get_goals(),
                                exhaustion=exhausted_direct_closure_states[
                                    pre_tool_structural_state
                                ],
                                error_code=step.error_code or "unsolved_goals",
                                loop_pattern=shallow_loop_pattern,
                            ),
                        )
                    return (
                        False,
                        session.read_code(),
                        ProverFailure(
                            reason="no_progress_stall",
                            message=f"Shallow tool loop detected: {shallow_loop_pattern}.",
                            error_code=step.error_code or "unsolved_goals",
                            target_name=target.name,
                            turn=turn,
                            backend=active_backend.name,
                            lean_feedback=session.get_goals(),
                        ),
                    )
                if tool_result.is_error:
                    failed_turns += 1
                    failure_action_key = f"{action.tool.name}:{action.tool.arguments}:{step.error_code or tool_result.content}"
                    failure_signature = self._failure_signature_key(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        lean_feedback=lean_feedback,
                        action_key=failure_action_key,
                    )
                    failure_repeats = self._register_failure_signature(
                        seen_failures=seen_failures,
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        lean_feedback=lean_feedback,
                        action_key=failure_action_key,
                    )
                    if failure_signature == last_failure_signature:
                        repeated_failure_streak += 1
                    else:
                        last_failure_signature = failure_signature
                        repeated_failure_streak = 1
                    failure_repeats = max(failure_repeats, repeated_failure_streak)
                    fingerprint = self._progress_fingerprint(
                        session=session,
                        theorem_name=self._target_theorem_name(packet, target),
                        outcome=step.error_code or action.tool.name,
                        lean_feedback=lean_feedback,
                    )
                    no_progress_streak = (
                        no_progress_streak + 1 if fingerprint == last_progress_fingerprint else 0
                    )
                    last_progress_fingerprint = fingerprint
                    structural_state = self._structural_state_fingerprint(
                        session=session,
                        theorem_name=theorem_name,
                    )
                    if structural_state == last_structural_state:
                        structural_stall_streak += 1
                    else:
                        structural_stall_streak = 0
                    last_structural_state = structural_state
                    if "no_progress_stall:" in tool_result.content:
                        if pre_tool_structural_state in exhausted_direct_closure_states:
                            return (
                                False,
                                session.read_code(),
                                self._direct_close_stall_failure(
                                    target=target,
                                    turn=turn,
                                    backend=active_backend,
                                    lean_feedback=session.get_goals(),
                                    exhaustion=exhausted_direct_closure_states[
                                        pre_tool_structural_state
                                    ],
                                    error_code=step.error_code or "unsolved_goals",
                                ),
                            )
                        return (
                            False,
                            session.read_code(),
                            ProverFailure(
                                reason="no_progress_stall",
                                message="REPL tactics stopped making progress on the active goal.",
                                error_code="unsolved_goals",
                                target_name=target.name,
                                turn=turn,
                                backend=active_backend.name,
                                lean_feedback=session.get_goals(),
                            ),
                        )
                    if (
                        structural_state in exhausted_direct_closure_states
                        and structural_stall_streak >= POST_DIRECT_CLOSURE_STALL_LIMIT
                    ):
                        return (
                            False,
                            session.read_code(),
                            self._direct_close_stall_failure(
                                target=target,
                                turn=turn,
                                backend=active_backend,
                                lean_feedback=session.get_goals(),
                                exhaustion=exhausted_direct_closure_states[structural_state],
                                error_code=step.error_code or "unsolved_goals",
                            ),
                        )
                    if failure_repeats >= 3:
                        return (
                            False,
                            session.read_code(),
                            ProverFailure(
                                reason="no_progress_stall",
                                message="The same failed tactic repeated on the same proof state.",
                                error_code=step.error_code or "unsolved_goals",
                                target_name=target.name,
                                turn=turn,
                                backend=active_backend.name,
                                lean_feedback=session.get_goals(),
                            ),
                        )
                    if failure_repeats == 2:
                        force_deterministic_recovery = True
                    continue
                failed_turns = 0
                last_failure_signature = None
                repeated_failure_streak = 0
                fingerprint = self._progress_fingerprint(
                    session=session,
                    theorem_name=self._target_theorem_name(packet, target),
                    outcome=action.tool.name,
                    lean_feedback=lean_feedback,
                )
                no_progress_streak = (
                    no_progress_streak + 1 if fingerprint == last_progress_fingerprint else 0
                )
                last_progress_fingerprint = fingerprint
                structural_state = self._structural_state_fingerprint(
                    session=session,
                    theorem_name=theorem_name,
                )
                if structural_state == last_structural_state:
                    structural_stall_streak += 1
                else:
                    structural_stall_streak = 0
                last_structural_state = structural_state
                if (
                    pre_tool_structural_state in exhausted_direct_closure_states
                    and structural_state == pre_tool_structural_state
                ):
                    return (
                        False,
                        session.read_code(),
                        self._direct_close_stall_failure(
                            target=target,
                            turn=turn,
                            backend=active_backend,
                            lean_feedback=session.get_goals(),
                            exhaustion=exhausted_direct_closure_states[pre_tool_structural_state],
                        ),
                    )
                if (
                    structural_state in exhausted_direct_closure_states
                    and structural_stall_streak >= POST_DIRECT_CLOSURE_STALL_LIMIT
                ):
                    return (
                        False,
                        session.read_code(),
                        self._direct_close_stall_failure(
                            target=target,
                            turn=turn,
                            backend=active_backend,
                            lean_feedback=session.get_goals(),
                            exhaustion=exhausted_direct_closure_states[structural_state],
                        ),
                    )

            return (
                False,
                session.read_code(),
                ProverFailure(
                    reason="max_turns_exhausted",
                    message="Prover hit the configured maximum number of turns.",
                    error_code="max_turns_exhausted",
                    target_name=target.name,
                    turn=max_turns,
                    backend=attempted_backends[-1]
                    if attempted_backends
                    else self.primary_backend.name,
                    lean_feedback=session.get_goals(),
                ),
            )
        finally:
            session.close()

    async def _run_decomposition(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        session: _ActiveProofSession,
        trace: list[ProverTraceStep],
        attempted_backends: list[str],
        turn: int,
        target_timeouts: ProverTargetTimeouts,
        max_turns: int,
        action: ProverAction,
        job_id: str,
        max_recursion_depth: int,
        telemetry: SpanRecorder,
        provider_usage: list[TokenUsage],
        audit_events: list[AuditEvent],
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[bool, str]:
        if self._extracted_lemmas >= 3 or target.recursion_depth >= max_recursion_depth:
            return False, session.read_code()

        lemma_name = (
            action.decomposition_name
            or f"apollo_{packet.theorem_name}_{self._extracted_lemmas + 1}"
        )
        lemma_statement = action.decomposition_statement or target.statement
        self._extracted_lemmas += 1
        lemma_target = ProverTarget(
            name=lemma_name,
            statement=lemma_statement,
            kind="apollo_lemma",
            recursion_depth=target.recursion_depth + 1,
            helper_theorem_name=lemma_name,
        )
        lemma_timeout = self._timeout_for_target(lemma_target, target_timeouts)
        lemma_code = _standalone_theorem_code(packet, lemma_name, lemma_statement)
        proved, produced_code, _failure = await self._prove_target(
            packet=packet,
            target=lemma_target,
            current_code=lemma_code,
            trace=trace,
            job_id=job_id,
            attempted_backends=attempted_backends,
            max_turns=max_turns,
            timeout=lemma_timeout,
            target_timeouts=target_timeouts,
            allow_decomposition=True,
            max_recursion_depth=max_recursion_depth,
            telemetry=telemetry,
            provider_usage=provider_usage,
            audit_events=audit_events,
            on_progress=on_progress,
        )
        if not proved:
            return False, session.read_code()

        theorem_block = _extract_theorem_block(produced_code)
        target_code = session.read_code()
        rewritten = _inject_theorem_before_main(target_code, theorem_block)
        target_theorem_name = (
            packet.theorem_name
            if target.kind == "theorem_body"
            else target.helper_theorem_name or target.name
        )
        rewritten = _replace_named_theorem_body(
            rewritten, target_theorem_name, f"exact {lemma_name}"
        )
        session.write_code(rewritten)
        trace.append(
            ProverTraceStep(
                turn=turn,
                backend=attempted_backends[-1] if attempted_backends else self.primary_backend.name,
                target_name=target.name,
                action_type="decompose",
                success=True,
                rationale=action.rationale,
                tool_result=f"Introduced {lemma_name} and reassembled the target via exact.",
                goals=session.get_goals(),
                code_snapshot=session.read_code(),
                decomposition_theorem=lemma_name,
            )
        )
        return True, session.read_code()

    def _emit_progress(
        self,
        callback: Callable[[str, dict[str, Any]], None] | None,
        event: str,
        *,
        job_id: str,
        stage: str,
        status: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if callback is None:
            return
        callback(
            event,
            build_progress_event(
                event,
                job_id=job_id,
                stage=stage,
                status=status,
                message=message,
                metadata=metadata,
            ),
        )

    def _memory_examples(self, packet: FormalizationPacket) -> list[dict[str, Any]]:
        examples = self.trace_store.query_similar(
            list(packet.selected_preamble),
            limit=2,
            outcome="verified",
        )
        return [
            {
                "claim_text": trace.claim_text,
                "preamble_names": trace.preamble_names,
                "tactic_sequence": trace.tactic_sequence[:4],
                "lesson_summary": trace.lesson_summary,
                "outcome": trace.outcome,
            }
            for trace in examples
        ]

    def _tool_specs_for_prompt(self, packet: FormalizationPacket) -> list[dict[str, Any]]:
        mathlib_native_mode = self._normalized_claim_type(packet) == "mathlib_native"
        specs = []
        for spec in self.registry.list():
            # LSP search tools are exposed to the model only for mathlib-native
            # claims; preamble-definable claims should first exercise the local
            # indexed lemmas and bounded compile checks.
            if spec.name in MATHLIB_NATIVE_PROMPT_ONLY_TOOLS and not mathlib_native_mode:
                continue
            specs.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "args": spec.args,
                }
            )
        return specs

    def _try_mathlib_native_harness_loop(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        session: _ActiveProofSession,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        attempted_backends: list[str],
        turn: int,
        timeout: int,
        telemetry: SpanRecorder,
        provider_usage: list[TokenUsage],
        lean_feedback: list[str],
        goals: list[str],
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
    ) -> tuple[str | None, ProverFailure | None]:
        before_state = self._mathlib_harness_state(session=session, goals=goals)
        claim_id = packet.theorem_name
        # Stage 2-followup A: when the harness's probe shows an empty goal
        # state, running the model produces a redundant tactic that the stall
        # detector then flags as failure. Yield to the outer loop's LSP-search
        # fallback (the path that historically closed claims like
        # t2_contraction_mapping_fixed_point) instead of claiming closure —
        # signalling closure here would bypass the final compile check.
        if not (before_state.get("goals") or []):
            self._emit_progress(
                on_progress,
                "harness_skipped_empty_goals",
                job_id=job_id,
                stage="prover",
                status="running_prover",
                message="Mathlib harness saw empty goal state; deferring to LSP search fallback.",
                metadata={
                    "turn": turn,
                    "target_name": target.name,
                    "claim_type": "mathlib_native",
                    "mathlib_native_mode": True,
                    "claim_id": claim_id,
                },
            )
            audit_events.append(
                AuditEvent(
                    stage="prover",
                    event_type="mathlib_native_harness_skipped_empty_goals",
                    provider=backend.provider,
                    model=backend.model,
                    success=True,
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        "claim_id": claim_id,
                    },
                )
            )
            return None, None
        retrieval_event = self._retrieve_mathlib_premises(
            before_state.get("goals") or [], k=5, claim_id=claim_id
        )
        retrieval_payload = retrieval_event.to_dict()
        self._retrieval_events.append(retrieval_payload)
        self._emit_progress(
            on_progress,
            "retrieval_event",
            job_id=job_id,
            stage="prover",
            status="running_prover",
            message="Retrieved mathlib premises for the active goal.",
            metadata={
                "turn": turn,
                "target_name": target.name,
                "claim_type": "mathlib_native",
                "mathlib_native_mode": True,
                "RetrievalEvent": retrieval_payload,
            },
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="RetrievalEvent",
                provider=backend.provider,
                model=backend.model,
                success=True,
                metadata={"turn": turn, "target_name": target.name, **retrieval_payload},
            )
        )
        # ── LeanSearch (hybrid second pass) ───────────────────────────────────
        ls_query = self._mathlib_native_search_query(
            packet=packet,
            goals=before_state.get("goals") or goals,
            current_code=session.read_code(),
        )
        ls_event = self._retrieve_lean_search_premises(
            ls_query, k=5, state=before_state, claim_id=claim_id
        )
        ls_payload = ls_event.to_dict()
        self._retrieval_events.append(ls_payload)
        self._emit_progress(
            on_progress,
            "retrieval_event",
            job_id=job_id,
            stage="prover",
            status="running_prover",
            message="LeanSearch retrieval for harness context.",
            metadata={
                "turn": turn,
                "target_name": target.name,
                "claim_type": "mathlib_native",
                "mathlib_native_mode": True,
                "RetrievalEvent": ls_payload,
            },
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="RetrievalEvent",
                provider=backend.provider,
                model=backend.model,
                success=True,
                metadata={"turn": turn, "target_name": target.name, **ls_payload},
            )
        )
        merged_premises = self._merge_retrieval_premises(
            retrieval_event.retrieved_premises,
            ls_event.retrieved_premises,
            k=8,
        )
        # Sprint 23 Task 3 + Stage 2-followup C: stall-recovery — if last turn
        # made no progress and we still have search budget, do a refined
        # leansearch keyed on the current unsolved subgoal text and merge
        # those premises in. Per-target idempotence prevents re-firing.
        prev_delta = self._last_progress_delta_obj()
        target_key = (claim_id or "", target.name or "")
        if (
            target_key not in self._second_retrieval_targets
            and self._should_do_second_retrieval(
                last_delta=prev_delta,
                budget_remaining_frac=self._budget_remaining_frac(),
            )
        ):
            refined_query = self._refined_leansearch_query(before_state)
            if refined_query and refined_query != ls_query:
                self._second_retrieval_targets.add(target_key)
                ls2_event = self._retrieve_lean_search_premises(
                    refined_query,
                    k=5,
                    retrieval_pass=2,
                    state=before_state,
                    claim_id=claim_id,
                )
                ls2_payload = ls2_event.to_dict()
                self._retrieval_events.append(ls2_payload)
                self._emit_progress(
                    on_progress,
                    "retrieval_event",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message="Refined LeanSearch (second pass) after stalled turn.",
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        "claim_type": "mathlib_native",
                        "mathlib_native_mode": True,
                        "RetrievalEvent": ls2_payload,
                    },
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="RetrievalEvent",
                        provider=backend.provider,
                        model=backend.model,
                        success=True,
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "retrieval_pass": 2,
                            **ls2_payload,
                        },
                    )
                )
                merged_premises = self._merge_retrieval_premises(
                    merged_premises,
                    ls2_event.retrieved_premises,
                    k=10,
                )
        # Stage 2-followup D: rescue retrieval after a hallucinated identifier.
        # When the model's prior tactic raised "unknown identifier 'X'", run an
        # extra LeanSearch keyed on the snake/camel parts of X — those tokens
        # are usually the right concept (e.g. monotone_bddAbove_converges →
        # "monotone bddAbove converges theorem") even when the name itself is
        # made up. Fires at most once per target.
        if (
            target_key not in self._rescue_retrieval_targets
            and self._budget_remaining_frac() > 0.20
        ):
            rescue_query = self._rescue_query_from_recent_trace(trace, target.name)
            if rescue_query and rescue_query != ls_query:
                self._rescue_retrieval_targets.add(target_key)
                rescue_event = self._retrieve_lean_search_premises(
                    rescue_query,
                    k=5,
                    retrieval_pass=2,
                    state=before_state,
                    claim_id=claim_id,
                )
                rescue_payload = rescue_event.to_dict()
                self._retrieval_events.append(rescue_payload)
                self._emit_progress(
                    on_progress,
                    "retrieval_event",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message="Rescue LeanSearch after unknown-identifier error.",
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        "claim_type": "mathlib_native",
                        "mathlib_native_mode": True,
                        "rescue_query": rescue_query,
                        "RetrievalEvent": rescue_payload,
                    },
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="RetrievalEvent",
                        provider=backend.provider,
                        model=backend.model,
                        success=True,
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "retrieval_pass": 2,
                            "rescue": True,
                            "rescue_query": rescue_query,
                            **rescue_payload,
                        },
                    )
                )
                merged_premises = self._merge_retrieval_premises(
                    merged_premises,
                    rescue_event.retrieved_premises,
                    k=10,
                )
        if not merged_premises:
            if _contains_lsp_unavailable(
                before_state.get("diagnostics")
            ) or _contains_lsp_unavailable(before_state.get("code_actions")):
                self._record_mathlib_native_lsp_summary(
                    trace=trace,
                    audit_events=audit_events,
                    backend=backend,
                    target=target,
                    turn=turn,
                    success=False,
                    error_code="lsp_unavailable",
                    message="lsp_unavailable: priority LSP context was unavailable before harness fallback.",
                    lean_feedback=lean_feedback,
                    goals=goals,
                    code_snapshot=session.read_code(),
                    metadata={
                        "claim_type": "mathlib_native",
                        "mathlib_native_mode": True,
                        "lsp_tool_name": "lean_diagnostic_messages",
                        "candidate_count": 0,
                        "compiled_candidate_count": 0,
                        "selected_lemma": None,
                        "search_query": None,
                        "RetrievalEvent": retrieval_payload,
                    },
                )
            trace.append(
                ProverTraceStep(
                    turn=turn,
                    backend=backend.name,
                    target_name=target.name,
                    action_type="mathlib_native_harness_retrieval",
                    success=False,
                    rationale="Hybrid retrieval (local RAG + LeanSearch) returned no premises; falling back to bounded LSP search.",
                    tool_name="retrieve_premises",
                    tool_arguments={"RetrievalEvent": retrieval_payload, "LeanSearchRetrievalEvent": ls_payload},
                    tool_result="No retrieved premises.",
                    lean_feedback=lean_feedback,
                    goals=goals,
                    code_snapshot=session.read_code(),
                    error_code="retrieval_empty",
                )
            )
            return None, None

        diagnostics = before_state.get("diagnostics")
        code_actions = before_state.get("code_actions")
        prompt = self._build_mathlib_harness_prompt(
            packet=packet,
            target=target,
            state=before_state,
            retrieved_premises=merged_premises,
            diagnostics=diagnostics,
            code_actions=code_actions,
            prior_trace=trace,
        )
        provider_started_at = time.perf_counter()
        try:
            raw_action = self._drivers[backend.provider].next_action(
                backend=backend,
                prompt=prompt,
            )
            telemetry.record_provider(provider_started_at)
            action, metadata = _unwrap_action_response(raw_action)
            usage = complete_usage(
                stage="prover",
                provider=backend.provider,
                model=backend.model,
                latency_ms=(time.perf_counter() - provider_started_at) * 1000.0,
                success=True,
                metadata=metadata,
                prompt_text=prompt,
            )
            provider_usage.append(usage)
            audit_events.append(
                AuditEvent(
                    stage="prover",
                    event_type="mathlib_native_harness_provider_turn",
                    provider=backend.provider,
                    model=backend.model,
                    success=True,
                    prompt_hash=stable_hash_text(
                        metadata.prompt_text if metadata is not None else prompt
                    ),
                    response_hash=stable_hash_text(
                        metadata.response_text if metadata is not None else None
                    ),
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        "usage_source": usage.usage_source,
                    },
                )
            )
        except Exception as exc:
            telemetry.record_provider(provider_started_at)
            error_code = classify_exception(exc)
            provider_usage.append(
                complete_usage(
                    stage="prover",
                    provider=backend.provider,
                    model=backend.model,
                    latency_ms=(time.perf_counter() - provider_started_at) * 1000.0,
                    success=False,
                    error_code=error_code,
                    prompt_text=prompt,
                )
            )
            trace.append(
                ProverTraceStep(
                    turn=turn,
                    backend=backend.name,
                    target_name=target.name,
                    action_type="mathlib_native_harness_loop",
                    success=False,
                    rationale="Harness provider invocation failed; falling back to bounded LSP search.",
                    tool_name="provider_turn",
                    tool_arguments={"RetrievalEvent": retrieval_payload},
                    tool_result=str(exc),
                    lean_feedback=lean_feedback,
                    goals=goals,
                    code_snapshot=session.read_code(),
                    error_code=error_code,
                )
            )
            return None, None

        if backend.name not in attempted_backends:
            attempted_backends.append(backend.name)

        if (
            action.action_type != "tool"
            or action.tool is None
            or action.tool.name != "apply_tactic"
        ):
            trace.append(
                ProverTraceStep(
                    turn=turn,
                    backend=backend.name,
                    target_name=target.name,
                    action_type="mathlib_native_harness_loop",
                    success=False,
                    rationale="Harness loop only accepts provider `apply_tactic` actions.",
                    tool_name=action.tool.name if action.tool is not None else None,
                    tool_arguments={
                        "RetrievalEvent": retrieval_payload,
                        "provider_action": action.model_dump(mode="json"),
                    },
                    tool_result="Rejected non-apply_tactic harness action.",
                    lean_feedback=lean_feedback,
                    goals=goals,
                    code_snapshot=session.read_code(),
                    error_code="schema_invalid",
                )
            )
            return None, None

        before_hash = str(before_state["state_hash"])
        tool_result = self._execute_tool(
            session=session,
            tool=action.tool,
            packet=packet,
            target=target,
        )
        after_state = self._mathlib_harness_state(session=session, goals=session.get_goals())
        after_hash = str(after_state["state_hash"])
        progress_delta = self._progress_delta_from_states(before_state, after_state)
        state_transition = StateTransition(
            goal_count_before=len(before_state.get("goals") or []),
            goal_count_after=len(after_state.get("goals") or []),
            progress_delta=progress_delta,
            state_hash_before=before_hash,
            state_hash_after=after_hash,
            turn_index=turn,
        )
        tool_usage = ToolUsageTrace(
            tool_name=action.tool.name,
            args=action.tool.arguments,
            result=tool_result.content,
            state_hash_before=before_hash,
            state_hash_after=after_hash,
            success=not tool_result.is_error,
        )
        progress_payload = progress_delta.to_dict()
        transition_payload = state_transition.to_dict()
        tool_payload = tool_usage.to_dict()
        self._tool_usage_traces.append(tool_payload)
        self._state_transitions.append(transition_payload)
        self._progress_deltas.append(progress_payload)
        trace.append(
            ProverTraceStep(
                turn=turn,
                backend=backend.name,
                target_name=target.name,
                action_type="mathlib_native_harness_loop",
                success=not tool_result.is_error,
                rationale=action.rationale,
                tool_name=action.tool.name,
                tool_arguments={
                    **action.tool.arguments,
                    "RetrievalEvent": retrieval_payload,
                    "ToolUsageTrace": tool_payload,
                    "StateTransition": transition_payload,
                    "ProgressDelta": progress_payload,
                    "retrieved_premises": retrieval_event.retrieved_premises,
                },
                tool_result=tool_result.content,
                lean_feedback=lean_feedback,
                goals=session.get_goals(),
                code_snapshot=session.read_code(),
                error_code=self._tool_error_code(action.tool.name, tool_result.content)
                if tool_result.is_error
                else None,
                repl_local_solved=not tool_result.is_error and bool(session.solved),
            )
        )
        for event_name, payload in (
            ("tool_usage_trace", tool_payload),
            ("state_transition", transition_payload),
            ("progress_delta", progress_payload),
        ):
            self._emit_progress(
                on_progress,
                event_name,
                job_id=job_id,
                stage="prover",
                status="running_prover",
                message=f"Recorded {payload['event_type']}.",
                metadata={"turn": turn, "target_name": target.name, payload["event_type"]: payload},
            )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="ProgressDelta",
                provider=backend.provider,
                model=backend.model,
                success=not progress_delta.stall_detected,
                metadata={"turn": turn, "target_name": target.name, **progress_payload},
            )
        )
        if progress_delta.stall_detected:
            return None, ProverFailure(
                reason="progress_stall",
                message="Harness apply_tactic left the mathlib-native state and goals unchanged.",
                error_code="unsolved_goals",
                target_name=target.name,
                turn=turn,
                backend=backend.name,
                lean_feedback=session.get_goals(),
            )
        if not tool_result.is_error and session.solved:
            return session.read_code(), None
        return None, None

    def _mathlib_harness_state(
        self,
        *,
        session: _ActiveProofSession,
        goals: list[str],
    ) -> dict[str, Any]:
        code = session.read_code()
        proof_path = session.proof_path
        diagnostics: Any = None
        code_actions: Any = None
        file_outline: Any = None
        if proof_path is not None:
            proof_line = self._active_proof_line(code)
            try:
                diagnostics = self.lsp_client.lean_diagnostic_messages(
                    proof_path,
                    severity="error",
                    start_line=max(1, proof_line - 2),
                    end_line=proof_line + 2,
                )
            except LeanLSPUnavailableError as exc:
                diagnostics = {"error": f"lsp_unavailable: {exc}"}
            try:
                code_actions = self.lsp_client.lean_code_actions(proof_path, line=proof_line)
            except LeanLSPUnavailableError as exc:
                code_actions = {"error": f"lsp_unavailable: {exc}"}
            try:
                file_outline = self.lsp_client.lean_file_outline(proof_path, max_declarations=40)
            except (AttributeError, LeanLSPUnavailableError) as exc:
                file_outline = {"error": f"lsp_unavailable: {exc}"}
        code_hash = stable_hash_text(code)
        state_hash = stable_hash_text(
            json.dumps(
                {
                    "code_hash": code_hash,
                    "goals": goals,
                    "diagnostics": diagnostics,
                },
                sort_keys=True,
                ensure_ascii=True,
                default=str,
            )
        )
        return {
            "code": code,
            "code_hash": code_hash,
            "goals": list(goals),
            "diagnostics": diagnostics,
            "code_actions": code_actions,
            "file_outline": file_outline,
            "state_hash": state_hash,
        }

    def _retrieve_mathlib_premises(
        self,
        goals: list[str],
        *,
        k: int,
        claim_id: str | None = None,
    ) -> RetrievalEvent:
        started_at = time.perf_counter()
        premises: list[dict[str, Any]] = []
        error_code: str | None = None
        try:
            from src.retrieval.mathlib_rag import retrieve_premises

            raw_premises = retrieve_premises("\n".join(goals), k=k)
        except Exception as exc:  # Stage 2 H.2: surface RAG failures as audit events.
            self._handle_lsp_error(
                "mathlib_rag", exc, context="\n".join(goals)[:120]
            )
            error_code = "mathlib_rag_unavailable"
            raw_premises = []
        for premise in list(raw_premises or [])[:k]:
            if hasattr(premise, "to_dict"):
                payload = premise.to_dict()
            elif hasattr(premise, "__dict__"):
                payload = dict(premise.__dict__)
            elif isinstance(premise, dict):
                payload = dict(premise)
            else:
                payload = {"name": str(premise)}
            premises.append(payload)
        scores = []
        for premise in premises:
            try:
                scores.append(float(premise.get("score", 0.0)))
            except (TypeError, ValueError):
                scores.append(0.0)
        return RetrievalEvent(
            retrieved_premises=premises,
            scores=scores,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            k=k,
            claim_id=claim_id,
            error_code=error_code,
        )

    def _handle_lsp_error(
        self,
        tool_name: str,
        exc: BaseException,
        *,
        context: str = "",
    ) -> None:
        """Record an LSP tool failure as a structured audit event.

        Why: prior to Sprint 24 these failures were swallowed by bare
        ``except Exception: pass`` blocks, making outages invisible in traces.
        """
        log_event(
            AuditEvent(
                stage="prover",
                event_type="lsp_tool_error",
                provider=self.primary_backend.provider,
                model=self.primary_backend.model,
                success=False,
                error_code="lsp_unavailable",
                error_message=f"{tool_name}: {exc}",
                metadata={"tool": tool_name, "context": context},
            )
        )

    def _retrieve_lean_search_premises(
        self,
        query: str,
        *,
        k: int,
        retrieval_pass: int = 1,
        state: dict[str, Any] | None = None,
        claim_id: str | None = None,
    ) -> RetrievalEvent:
        """Enhanced with observable LeanSearchFailureEvent on 0-results or exceptions,
        plus one retry using refined subgoal query from state (backwards-compatible).
        Preserves exact success path, budget recording, and enrichment.
        """
        started_at = time.perf_counter()
        original_query = query
        used_query = query
        refined_query: str | None = None
        retry_attempted = False
        premises: list[dict[str, Any]] = []
        error: Exception | None = None

        if not self.budget_tracker.can_search():
            error = RuntimeError("search budget exhausted")
        else:
            for attempt in range(2):  # exactly one retry
                if attempt > 0:
                    retry_attempted = True
                    if state is not None:
                        refined_query = self._refined_leansearch_query(state)
                        if refined_query and refined_query != used_query:
                            used_query = refined_query
                            premises = []  # reset for retry
                    else:
                        # fallback refinement from query text (subgoal-like)
                        refined_query = (
                            self._refined_leansearch_query({"goals": [used_query]})
                            or used_query[:240]
                        )
                        if refined_query != used_query:
                            used_query = refined_query
                            premises = []

                try:
                    payload = self.lsp_client.lean_leansearch(
                        used_query, num_results=k
                    )
                    # Record budget only on a successful round-trip so that retries on
                    # LSP outage do not exhaust the search budget for the run.
                    self.budget_tracker.record("lean_leansearch")
                    items = (payload or {}).get("items") or []
                    for item in items[:k]:
                        name = str(
                            item.get("name") or item.get("theorem_name") or ""
                        )
                        if not name:
                            continue
                        premises.append(
                            {
                                "name": name,
                                "score": 0.80,
                                "statement": item.get("type")
                                or item.get("statement"),
                                "docstring": item.get("docstring"),
                                "file_path": item.get("module"),
                                "tags": [],
                                "dependencies": [],
                                "source": "lean_leansearch",
                            }
                        )
                    if premises:
                        break  # success
                    # successful call but empty = failure for retry
                    if attempt == 0:
                        continue
                except Exception as exc:
                    error = exc
                    self._handle_lsp_error(
                        "lean_leansearch", exc, context=used_query[:120]
                    )
                    if attempt == 0:
                        continue
                    break

        # Make failures observable with structured event (visible in JSONL/audit)
        if not premises or error is not None:
            failure_event = LeanSearchFailureEvent(
                query=original_query,
                refined_query=refined_query,
                error_code=(
                    "no_results"
                    if not premises and error is None
                    else "lsp_error"
                ),
                error_message=str(error) if error else "lean_leansearch returned 0 results",
                retry_attempted=retry_attempted,
                hit=bool(premises),
                latency_ms=(time.perf_counter() - started_at) * 1000.0,
                retrieval_pass=retrieval_pass,
                claim_id=claim_id,
            )
            backend = getattr(self, "primary_backend", None)
            log_event(
                AuditEvent(
                    stage="prover",
                    event_type="LeanSearchFailureEvent",
                    provider=backend.provider if backend is not None else "unknown",
                    model=backend.model if backend is not None else "unknown",
                    success=False,
                    error_code=failure_event.error_code,
                    error_message=failure_event.error_message,
                    metadata=failure_event.to_dict(),
                )
            )

        # Sprint 23 Task 2: enrich each leansearch premise with outline+hover.
        enriched_count = self._enrich_leansearch_premises(premises)
        scores = [float(p.get("score", 0.0)) for p in premises]
        return RetrievalEvent(
            retrieved_premises=premises,
            scores=scores,
            latency_ms=(time.perf_counter() - started_at) * 1000.0,
            k=k,
            source="lean_leansearch",
            query=used_query,
            enriched_count=enriched_count,
            retrieval_pass=retrieval_pass,
            claim_id=claim_id,
        )

    def _get_lsp_cache(self) -> LSPCache:
        """Return the per-prove LSP cache, rebinding if ``lsp_client`` changed.

        Tests may monkey-patch ``self.lsp_client`` after the cache was first
        constructed; we detect that and refresh the cache so it points at the
        active client. This costs O(1) and only allocates when the client
        identity changes.
        """
        cache = self._lsp_cache
        if cache is None or cache.lsp_client is not self.lsp_client:
            cache = LSPCache(
                self.lsp_client,
                on_error=lambda tool, exc, ctx: self._handle_lsp_error(
                    tool, exc, context=ctx
                ),
            )
            self._lsp_cache = cache
        return cache

    def _enrich_leansearch_premises(self, premises: list[dict[str, Any]]) -> int:
        return self._get_lsp_cache().enrich_premises(premises)

    @staticmethod
    def _merge_retrieval_premises(
        local: list[dict[str, Any]],
        remote: list[dict[str, Any]],
        k: int,
    ) -> list[dict[str, Any]]:
        by_name: dict[str, dict[str, Any]] = {}
        for p in local:
            name = p.get("name", "")
            if name and (name not in by_name or p.get("score", 0.0) > by_name[name].get("score", 0.0)):
                by_name[name] = p
        for p in remote:
            name = p.get("name", "")
            if name and (name not in by_name or p.get("score", 0.0) > by_name[name].get("score", 0.0)):
                by_name[name] = p
        merged = sorted(by_name.values(), key=lambda p: float(p.get("score", 0.0)), reverse=True)
        return merged[:k]

    def _build_mathlib_harness_prompt(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        state: dict[str, Any],
        retrieved_premises: list[dict[str, Any]],
        diagnostics: Any,
        code_actions: Any,
        prior_trace: list[ProverTraceStep],
    ) -> str:
        recent_steps = [
            {
                "turn": step.turn,
                "action_type": step.action_type,
                "tool_name": step.tool_name,
                "success": step.success,
                "tool_result": step.tool_result,
            }
            for step in prior_trace[-3:]
        ]
        # Sprint 23 Task 2: prefer enriched hover signature over thin leansearch payload.
        prompt_premises = [
            self._project_premise_for_prompt(p) for p in (retrieved_premises or [])
        ]
        # Stage 1 Task 2: strengthened decomposition hint (actionable tactics) + concise
        # multi-step patterns (consider-this guidance). Aligns with lean4_proving skill:
        # structural decomposition only; trust harness premises; no long inventories.
        rules = [
            "Return one apply_tactic action.",
            "Prefer tactics that reference retrieved Mathlib premises.",
            "Do not rewrite the theorem body in this harness loop.",
        ]
        if self._goals_need_decomposition_hint(state.get("goals")):
            rules.append(
                "If goal has quantifiers (∀/∃) or conjuncts (∧/↔), start with "
                "intro (add hypotheses), obtain/cases (existentials), refine (premise "
                "with holes), or constructor (split) before retrieved premises."
            )
            rules.append(
                "Consider patterns: 1. Quantified: `intro h; obtain ⟨x,hx⟩:=premise h; "
                "refine ⟨x,hx,_⟩`. 2. Conjunctive: `constructor; · exact p1; · refine p2`. "
                "3. Nested: `cases h with h1 h2; refine ...` (use Mathlib lemmas). "
                "4. ExistsMembership-Conjunctive: `obtain ⟨x, hxs, hP⟩ := premise hyps; "
                "exact ⟨x, hxs, hP, hQ⟩`."
            )
        # Stage 2 P1.B: premise-utilization protocol — teaches the model how to
        # consume the enriched fields the harness already supplies (full type
        # signature, detailed docstring, declaration location). General; no
        # claim-specific guidance.
        premise_utilization = [
            "If a premise's full_type_signature ends in your goal shape, prefer "
            "`exact <name> ...` or `apply <name>`.",
            "If a premise has form `(h : P) → ∃ x, Q x` and your goal is `∃ x, Q x`, "
            "use `obtain ⟨x, hx⟩ := <name> <hyp>` then close.",
            "Match a premise by detailed_docstring keywords against the operators "
            "and types in your current goal before guessing.",
            "Prefer premises whose declaration_location lies in a Mathlib namespace "
            "matching your goal's types.",
        ]
        return json.dumps(
            {
                "claim": packet.claim,
                "theorem_name": packet.theorem_name,
                "claim_type": "mathlib_native",
                "target": target.model_dump(mode="json"),
                "current_code": state.get("code"),
                "goals": state.get("goals"),
                "diagnostics": diagnostics,
                "code_actions": code_actions,
                "file_outline": state.get("file_outline"),
                "retrieved_premises": prompt_premises,
                "recent_trace": recent_steps,
                "instructions": {
                    "return_json_only": True,
                    "only_allowed_tool": "apply_tactic",
                    "use_retrieved_premises": True,
                    "rules": rules,
                    "premise_utilization": premise_utilization,
                },
                "response_schema": {
                    "action_type": "tool",
                    "rationale": "string",
                    "tool": {"name": "apply_tactic", "arguments": {"tactic": "Lean tactic"}},
                },
            },
            ensure_ascii=True,
            indent=2,
            default=str,
        )

    @staticmethod
    def _project_premise_for_prompt(premise: dict[str, Any]) -> dict[str, Any]:
        """Prefer ``full_type_signature``/``detailed_docstring`` over thin leansearch fields."""
        out = dict(premise)
        full_sig = premise.get("full_type_signature")
        if full_sig:
            out["statement"] = full_sig
        detailed_doc = premise.get("detailed_docstring")
        if detailed_doc and not premise.get("docstring"):
            out["docstring"] = detailed_doc
        return out

    @staticmethod
    def _goals_need_decomposition_hint(goals: Any) -> bool:
        if not goals:
            return False
        if isinstance(goals, str):
            text = goals
        else:
            try:
                text = "\n".join(str(g) for g in goals)
            except TypeError:
                text = str(goals)
        return any(marker in text for marker in ("∀", "∃", "∧", "↔"))

    @staticmethod
    def _should_do_second_retrieval(
        *,
        last_delta: ProgressDelta | None,
        budget_remaining_frac: float,
        turn: int | None = None,  # accepted for backwards compatibility, ignored
    ) -> bool:
        """Stall-recovery heuristic for a second leansearch pass.

        Stage 2-followup C: dropped the strict ``turn == 1`` gate. Fires on
        any turn where the previous turn produced no progress and budget is
        ample. Per-target idempotence is enforced at the call site via the
        ``_second_retrieval_targets`` set, so this stays a pure heuristic.
        """
        del turn  # Backwards-compat parameter, no longer load-bearing.
        if last_delta is None:
            return False
        if last_delta.goals_reduced:
            return False
        return budget_remaining_frac > 0.30

    @staticmethod
    def _refined_leansearch_query(state: dict[str, Any]) -> str | None:
        """Build a refined leansearch query from the current unsolved subgoal text.

        Stage 2 P1.A: walks the full goal text (hypotheses + ⊢) and pulls
        Mathlib-style CamelCase identifiers (IsCompact, Monotone, BddAbove,
        IsMaxOn, ContinuousOn, Tendsto, …). These dominate Mathlib lemma names
        so a query built from them retrieves better than raw goal text.
        Falls back to the previous goal-line behaviour when no identifiers
        are detected.
        """
        goals = state.get("goals") or []
        if not goals:
            return None
        first = goals[0] if isinstance(goals, list) else goals
        text = str(first or "").strip()
        if not text:
            return None
        idents = _extract_mathlib_idents(text)
        if idents:
            joined = " ".join(idents[:4])
            if not any(kw in joined.lower() for kw in ("theorem", "lemma", "prove")):
                joined = f"{joined} theorem"
            return joined[:200]
        # Fallback: original goal-only behaviour for non-Mathlib goals.
        first_line = text.splitlines()[0].strip()
        if not first_line:
            first_line = text
        key_part = first_line.split("⊢")[-1].strip() if "⊢" in first_line else first_line
        if not any(kw in key_part.lower() for kw in ["theorem", "lemma", "prove"]):
            key_part += " theorem"
        return key_part[:200]

    @staticmethod
    def _rescue_query_from_recent_trace(
        trace: list[ProverTraceStep],
        target_name: str | None,
    ) -> str | None:
        """Stage 2-followup D: scan the recent harness trace for an
        ``unknown identifier '<X>'`` error from a prior tactic on this target,
        and turn it into a concept-token query for LeanSearch.
        """
        if not trace:
            return None
        for step in reversed(trace):
            if target_name and step.target_name and step.target_name != target_name:
                continue
            if step.action_type != "mathlib_native_harness_loop":
                continue
            if not step.tool_result:
                continue
            ident = _extract_unknown_identifier(step.tool_result)
            if ident:
                query = _query_from_failed_identifier(ident)
                return query or None
        return None

    def _last_progress_delta_obj(self) -> ProgressDelta | None:
        """Return the most recent ProgressDelta as a typed object (None if empty)."""
        if not self._progress_deltas:
            return None
        last = self._progress_deltas[-1]
        try:
            return ProgressDelta(
                goals_reduced=bool(last.get("goals_reduced", False)),
                complexity_reduced=bool(last.get("complexity_reduced", False)),
                stall_detected=bool(last.get("stall_detected", False)),
                goal_count_before=int(last.get("goal_count_before", 0)),
                goal_count_after=int(last.get("goal_count_after", 0)),
                complexity_before=int(last.get("complexity_before", 0)),
                complexity_after=int(last.get("complexity_after", 0)),
            )
        except (TypeError, ValueError):
            return None

    def _budget_remaining_frac(self) -> float:
        max_search = max(1, int(self.budget_tracker.max_search_tool_calls))
        used = int(self.budget_tracker.search_tool_calls)
        return max(0.0, 1.0 - used / max_search)

    def _apply_budget_limits_for_packet(self, packet: FormalizationPacket) -> None:
        """Sprint 23 Task 3: hybrid budget bump for mathlib_native claims only."""
        from src.config import (
            MAX_PROVE_STEPS,
            MAX_PROVE_STEPS_HYBRID,
            MAX_SEARCH_TOOL_CALLS,
            MAX_SEARCH_TOOL_CALLS_HYBRID,
            MAX_TOTAL_TOOL_CALLS,
        )

        # Restore defaults first so a prior mathlib_native call doesn't leak.
        self.budget_tracker.max_search_tool_calls = MAX_SEARCH_TOOL_CALLS
        self.budget_tracker.max_total_tool_calls = MAX_TOTAL_TOOL_CALLS
        if self._normalized_claim_type(packet) == "mathlib_native":
            self.budget_tracker.max_search_tool_calls = MAX_SEARCH_TOOL_CALLS_HYBRID
            # Total is "search + everything else"; keep the existing total ceiling
            # but allow the hybrid prove-step extension via the env-overridable
            # constant for callers that consult it directly. (Steps are governed
            # by max_turns, so the constant is informational for now.)
            _ = MAX_PROVE_STEPS, MAX_PROVE_STEPS_HYBRID  # exported for tests/config inspection

    def _progress_delta_from_states(
        self,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
    ) -> ProgressDelta:
        before_goals = list(before_state.get("goals") or [])
        after_goals = list(after_state.get("goals") or [])
        before_complexity = sum(len(str(goal).strip()) for goal in before_goals)
        after_complexity = sum(len(str(goal).strip()) for goal in after_goals)
        return ProgressDelta(
            goals_reduced=len(after_goals) < len(before_goals),
            complexity_reduced=after_complexity < before_complexity,
            stall_detected=(
                before_goals == after_goals
                and (
                    before_state.get("state_hash") == after_state.get("state_hash")
                    or after_complexity >= before_complexity
                )
            ),
            goal_count_before=len(before_goals),
            goal_count_after=len(after_goals),
            complexity_before=before_complexity,
            complexity_after=after_complexity,
        )

    def _try_mathlib_native_lsp_search(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        session: _ActiveProofSession,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        turn: int,
        timeout: int,
        lean_feedback: list[str],
        goals: list[str],
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
    ) -> str | None:
        if session.proof_path is None:
            self._record_mathlib_native_lsp_summary(
                trace=trace,
                audit_events=audit_events,
                backend=backend,
                target=target,
                turn=turn,
                success=False,
                error_code="lsp_unavailable",
                message="lsp_unavailable: no proof file is attached to the session.",
                lean_feedback=lean_feedback,
                goals=goals,
                code_snapshot=session.read_code(),
                metadata={"mathlib_native_mode": True},
            )
            return None

        code = session.read_code()
        policy = self._direct_close_policy(packet)
        self.budget_tracker.record_native_search_attempt()
        proof_line = self._active_proof_line(code)
        proof_column = self._hover_column_for_line(code, proof_line)
        lsp_payloads: dict[str, Any] = {}
        first_error_code: str | None = None

        def call_lsp(tool_name: str, callback: Callable[[], Any]) -> Any | None:
            nonlocal first_error_code
            self.budget_tracker.record(tool_name)
            metadata = {
                "target_name": target.name,
                "tool_name": tool_name,
                "lsp_tool_name": tool_name,
                "claim_type": policy.claim_type,
                "claim_type_policy": policy.claim_type_policy,
                "target_kind": target.kind,
                "mathlib_native_mode": True,
                "candidate_count": 0,
                "compiled_candidate_count": 0,
                "selected_lemma": None,
                "search_query": None,
            }
            try:
                payload = callback()
            except LeanLSPUnavailableError as exc:
                error_code = (
                    "lsp_search_exhausted"
                    if "no results" in str(exc).lower()
                    else "lsp_unavailable"
                )
                first_error_code = first_error_code or error_code
                self._emit_progress(
                    on_progress,
                    "prover_tool",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message=f"LSP tool `{tool_name}` failed.",
                    metadata={**metadata, "success": False, "error_code": error_code},
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="mathlib_native_lsp_tool",
                        provider=backend.provider,
                        model=backend.model,
                        success=False,
                        error_code=error_code,
                        error_message=str(exc),
                        metadata={"turn": turn, **metadata},
                    )
                )
                return None
            self._emit_progress(
                on_progress,
                "prover_tool",
                job_id=job_id,
                stage="prover",
                status="running_prover",
                message=f"LSP tool `{tool_name}` executed.",
                metadata={**metadata, "success": True, "error_code": None},
            )
            audit_events.append(
                AuditEvent(
                    stage="prover",
                    event_type="mathlib_native_lsp_tool",
                    provider=backend.provider,
                    model=backend.model,
                    success=True,
                    metadata={"turn": turn, **metadata},
                )
            )
            return payload

        lsp_payloads["diagnostics"] = call_lsp(
            "lean_diagnostic_messages",
            lambda: self.lsp_client.lean_diagnostic_messages(
                session.proof_path,
                severity="error",
                start_line=max(1, proof_line - 2),
                end_line=proof_line + 2,
            ),
        )
        lsp_payloads["goal"] = call_lsp(
            "lean_goal",
            lambda: self.lsp_client.lean_goal(
                session.proof_path, line=proof_line, column=proof_column
            ),
        )
        lsp_payloads["code_actions"] = call_lsp(
            "lean_code_actions",
            lambda: self.lsp_client.lean_code_actions(session.proof_path, line=proof_line),
        )
        lsp_payloads["hover"] = call_lsp(
            "lean_hover_info",
            lambda: self.lsp_client.lean_hover_info(
                session.proof_path, line=proof_line, column=proof_column
            ),
        )

        search_query = self._mathlib_native_search_query(
            packet=packet, goals=goals, current_code=code
        )
        lsp_payloads["leansearch"] = call_lsp(
            "lean_leansearch",
            lambda: self.lsp_client.lean_leansearch(
                search_query,
                num_results=MATHLIB_NATIVE_LSP_SEARCH_RESULTS,
            ),
        )
        candidates = self._mathlib_native_lsp_candidates(
            packet=packet,
            current_code=code,
            code_actions=lsp_payloads.get("code_actions"),
            search_results=lsp_payloads.get("leansearch"),
        )
        attempt_timeout = min(timeout, SHORTCUT_ATTEMPT_TIMEOUT_SECONDS)
        theorem_name = self._target_theorem_name(packet, target)
        compiled_candidate_count = 0
        selected_lemma: str | None = None
        for proof, source, lemma_name in candidates[:MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT]:
            try:
                candidate_code = _replace_named_theorem_body(code, theorem_name, proof)
            except ValueError:
                continue
            compiled_candidate_count += 1
            self.budget_tracker.record("compile_check")
            try:
                result = compile_check(candidate_code, timeout=attempt_timeout)
            except Exception:
                continue
            if result.get("success"):
                selected_lemma = lemma_name
                metadata = {
                    "claim_type": policy.claim_type,
                    "claim_type_policy": policy.claim_type_policy,
                    "target_kind": target.kind,
                    "mathlib_native_mode": True,
                    "lsp_tool_name": source,
                    "candidate_count": len(candidates),
                    "compiled_candidate_count": compiled_candidate_count,
                    "selected_lemma": selected_lemma,
                    "search_query": search_query,
                }
                self._emit_progress(
                    on_progress,
                    "prover_tool",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message="Closed via mathlib-native LSP-assisted search.",
                    metadata={
                        "target_name": target.name,
                        "tool_name": "mathlib_native_lsp_search",
                        "success": True,
                        **metadata,
                    },
                )
                self._record_mathlib_native_lsp_summary(
                    trace=trace,
                    audit_events=audit_events,
                    backend=backend,
                    target=target,
                    turn=turn,
                    success=True,
                    error_code=None,
                    message=f"Closed via `{proof.splitlines()[0]}`.",
                    lean_feedback=lean_feedback,
                    goals=[],
                    code_snapshot=candidate_code,
                    metadata={**metadata, "proof": proof},
                )
                return candidate_code

        error_code = first_error_code or "lsp_search_exhausted"
        metadata = {
            "claim_type": policy.claim_type,
            "claim_type_policy": policy.claim_type_policy,
            "target_kind": target.kind,
            "mathlib_native_mode": True,
            "lsp_tool_name": "lean_leansearch",
            "candidate_count": len(candidates),
            "compiled_candidate_count": compiled_candidate_count,
            "selected_lemma": selected_lemma,
            "search_query": search_query,
        }
        self._emit_progress(
            on_progress,
            "prover_tool",
            job_id=job_id,
            stage="prover",
            status="running_prover",
            message="Mathlib-native LSP-assisted search did not close the target.",
            metadata={
                "target_name": target.name,
                "tool_name": "mathlib_native_lsp_search",
                "success": False,
                "error_code": error_code,
                **metadata,
            },
        )
        self._record_mathlib_native_lsp_summary(
            trace=trace,
            audit_events=audit_events,
            backend=backend,
            target=target,
            turn=turn,
            success=False,
            error_code=error_code,
            message="LSP-assisted search exhausted compile-validated candidates.",
            lean_feedback=lean_feedback,
            goals=goals,
            code_snapshot=code,
            metadata=metadata,
        )
        return None

    def _record_mathlib_native_lsp_summary(
        self,
        *,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        target: ProverTarget,
        turn: int,
        success: bool,
        error_code: str | None,
        message: str,
        lean_feedback: list[str],
        goals: list[str],
        code_snapshot: str,
        metadata: dict[str, Any],
    ) -> None:
        trace.append(
            ProverTraceStep(
                turn=turn,
                backend=backend.name,
                target_name=target.name,
                action_type="mathlib_native_lsp_search",
                success=success,
                rationale="Use bounded lean-lsp-mcp search for a mathlib-native claim.",
                tool_name="mathlib_native_lsp_search",
                tool_arguments=metadata,
                tool_result=message,
                lean_feedback=lean_feedback,
                goals=goals,
                code_snapshot=code_snapshot,
                error_code=error_code,
            )
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="mathlib_native_lsp_search",
                provider=backend.provider,
                model=backend.model,
                success=success,
                error_code=error_code,
                error_message=None if success else message,
                metadata={"turn": turn, "target_name": target.name, **metadata},
            )
        )

    def _active_proof_line(self, code: str) -> int:
        lines = code.splitlines()
        for index, line in enumerate(lines, start=1):
            if line.strip() in {"sorry", "exact?", "by"} or "sorry" in line:
                return index
        for index in range(len(lines), 0, -1):
            if lines[index - 1].strip():
                return index
        return 1

    def _hover_column_for_line(self, code: str, line: int) -> int:
        lines = code.splitlines()
        if not lines:
            return 1
        text = lines[max(0, min(line - 1, len(lines) - 1))]
        match = re.search(r"[A-Za-z_][A-Za-z0-9_'.]*", text)
        return (
            (match.start() + 1) if match is not None else max(1, len(text) - len(text.lstrip()) + 1)
        )

    def _mathlib_native_search_query(
        self,
        *,
        packet: FormalizationPacket,
        goals: list[str],
        current_code: str,
    ) -> str:
        theorem_goal = theorem_goal_statement(current_code) or ""
        # Stage 2-followup B: prefer Mathlib-style CamelCase identifiers when
        # we can find them in the goal/theorem text. Falls back to the original
        # verbose join when no idents are present, preserving existing behaviour
        # on natural-language-only claims.
        ident_source = "\n".join(
            chunk for chunk in (theorem_goal, *(goals[:1])) if chunk
        )
        idents = _extract_mathlib_idents(ident_source)
        if idents:
            joined = " ".join(idents[:5])
            if not any(kw in joined.lower() for kw in ("theorem", "lemma", "prove")):
                joined = f"{joined} theorem"
            return joined[:200]
        chunks = [packet.claim, theorem_goal, *(goals[:1])]
        return " ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip())[:900]

    def _extract_code_action_tactics(self, payload: Any) -> list[str]:
        tactics: list[str] = []

        def add(value: str) -> None:
            tactic = value.strip()
            if tactic.startswith("Try this:"):
                tactic = tactic.removeprefix("Try this:").strip()
            if not tactic or tactic == "sorry" or "\n\n" in tactic:
                return
            if tactic not in tactics:
                tactics.append(tactic)

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                new_text = value.get("new_text")
                if isinstance(new_text, str):
                    add(new_text)
                title = value.get("title")
                if isinstance(title, str) and title.startswith("Try this:"):
                    add(title)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return tactics

    def _extract_search_item_names(self, payload: Any) -> list[str]:
        names: list[str] = []
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return names
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
        return names

    def _mathlib_native_lsp_candidates(
        self,
        *,
        packet: FormalizationPacket,
        current_code: str,
        code_actions: Any,
        search_results: Any,
    ) -> list[tuple[str, str, str | None]]:
        candidates: list[tuple[str, str, str | None]] = []
        theorem_names = self._extract_search_item_names(search_results)
        candidates.extend(
            self._mathlib_native_heuristic_candidates(
                current_code=current_code, theorem_names=theorem_names
            )
        )
        for tactic in self._extract_code_action_tactics(code_actions):
            candidates.append((tactic, "lean_code_actions", None))
        for name in theorem_names:
            candidates.append((f"exact {name}", "lean_leansearch", name))
            candidates.append((f"simpa using {name}", "lean_leansearch", name))

        deduped: list[tuple[str, str, str | None]] = []
        seen: set[str] = set()
        for proof, source, lemma_name in candidates:
            normalized = proof.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append((normalized, source, lemma_name))
        return deduped

    def _mathlib_native_heuristic_candidates(
        self,
        *,
        current_code: str,
        theorem_names: list[str],
    ) -> list[tuple[str, str, str | None]]:
        candidates: list[tuple[str, str, str | None]] = []
        names = set(theorem_names)
        is_contraction = self._is_contraction_context(current_code)
        if is_contraction is not None:
            hf, intro_prefix = is_contraction
            if "∃!" in current_code or "ExistsUnique" in current_code:
                candidates.append(
                    (
                        f"{intro_prefix}exact contraction_has_unique_fixedPoint {hf}",
                        "lean_leansearch",
                        "contraction_has_unique_fixedPoint",
                    )
                )
            candidates.append(
                (
                    f"{intro_prefix}exact contraction_has_fixedPoint {hf}",
                    "lean_leansearch",
                    "contraction_has_fixedPoint",
                )
            )

        contracting = self._contracting_hypothesis(current_code)
        if contracting is not None and (
            "ContractingWith.fixedPoint_isFixedPt" in names
            or "ContractingWith.fixedPoint_unique" in names
            or "ContractingWith.fixedPoint" in names
        ):
            hf, f_name = contracting
            if "∃!" in current_code or "ExistsUnique" in current_code:
                candidates.append(
                    (
                        "\n".join(
                            [
                                f"refine ⟨ContractingWith.fixedPoint {f_name} {hf}, ?_, ?_⟩",
                                f"· exact ContractingWith.fixedPoint_isFixedPt (f := {f_name}) {hf}",
                                "· intro y hy",
                                f"  exact (ContractingWith.fixedPoint_unique (f := {f_name}) {hf} hy).symm",
                            ]
                        ),
                        "lean_leansearch",
                        "ContractingWith.fixedPoint_unique",
                    )
                )
            candidates.append(
                (
                    f"exact ContractingWith.fixedPoint_isFixedPt (f := {f_name}) {hf}",
                    "lean_leansearch",
                    "ContractingWith.fixedPoint_isFixedPt",
                )
            )
            candidates.append(
                (
                    f"exact ⟨ContractingWith.fixedPoint {f_name} {hf}, ContractingWith.fixedPoint_isFixedPt (f := {f_name}) {hf}⟩",
                    "lean_leansearch",
                    "ContractingWith.fixedPoint_isFixedPt",
                )
            )

        compact = self._compact_extreme_value_context(current_code)
        if compact is not None:
            hcompact, hnonempty, hcontinuous, intro_prefix = compact
            if "IsConstrainedMaximum" in current_code:
                candidates.append(
                    (
                        f"{intro_prefix}exact exists_isConstrainedMaximum_of_isCompact_continuousOn {hcompact} {hnonempty} {hcontinuous}",
                        "lean_leansearch",
                        "exists_isConstrainedMaximum_of_isCompact_continuousOn",
                    )
                )
            if (
                "IsCompact.exists_isMaxOn" not in names
                and "IsCompact.exists_sSup_image_eq_and_ge" not in names
            ):
                return candidates
            candidates.append(
                (
                    f"{intro_prefix}exact IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                    "lean_leansearch",
                    "IsCompact.exists_isMaxOn",
                )
            )
            candidates.append(
                (
                    "\n".join(
                        [
                            f"{intro_prefix}obtain ⟨x, hx, hmax⟩ := IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                            "exact ⟨x, hx, hmax⟩",
                        ]
                    ),
                    "lean_leansearch",
                    "IsCompact.exists_isMaxOn",
                )
            )
            candidates.append(
                (
                    "\n".join(
                        [
                            f"{intro_prefix}obtain ⟨x, hx, _hsup, hmax⟩ := IsCompact.exists_sSup_image_eq_and_ge {hcompact} {hnonempty} {hcontinuous}",
                            "exact ⟨x, hx, hmax⟩",
                        ]
                    ),
                    "lean_leansearch",
                    "IsCompact.exists_sSup_image_eq_and_ge",
                )
            )

        monotone = self._monotone_convergence_context(current_code)
        if monotone is not None and "tendsto_of_monotone" in names:
            hmono, hbdd = monotone
            candidates.append(
                (
                    "exact (tendsto_of_monotone " + hmono + ").resolve_left " + hbdd,
                    "lean_leansearch",
                    "tendsto_of_monotone",
                )
            )
            candidates.append(
                (
                    "rcases tendsto_of_monotone "
                    + hmono
                    + " with htop | hconv\n· exfalso\n  exact "
                    + hbdd
                    + ".not_tendsto_atTop htop\n· exact hconv",
                    "lean_leansearch",
                    "tendsto_of_monotone",
                )
            )
        return candidates

    def _is_contraction_context(self, code: str) -> tuple[str, str] | None:
        named = re.search(
            r"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*IsContraction\s+[A-Za-z_][A-Za-z0-9_']*\s*\)",
            code,
        )
        if named is not None:
            return named.group(1), ""
        if re.search(r"IsContraction\s+[A-Za-z_][A-Za-z0-9_']*\s*→", code):
            return "hf", "intro α _ _ _ f hf\n"
        return None

    def _contracting_hypothesis(self, code: str) -> tuple[str, str] | None:
        match = re.search(
            r"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*ContractingWith\s+[^()\n]+?\s+([A-Za-z_][A-Za-z0-9_']*)\s*\)",
            code,
        )
        if match is None:
            return None
        return match.group(1), match.group(2)

    def _compact_extreme_value_context(self, code: str) -> tuple[str, str, str, str] | None:
        compact = re.search(
            r"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*IsCompact\s+([A-Za-z_][A-Za-z0-9_']*)\s*\)", code
        )
        if compact is not None:
            set_name = compact.group(2)
            nonempty = re.search(
                rf"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*{re.escape(set_name)}\.Nonempty\s*\)", code
            )
            continuous = re.search(
                rf"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*ContinuousOn\s+[A-Za-z_][A-Za-z0-9_']*\s+{re.escape(set_name)}\s*\)",
                code,
            )
            if nonempty is None or continuous is None:
                return None
            return compact.group(1), nonempty.group(1), continuous.group(1), ""
        if (
            re.search(r"IsCompact\s+[A-Za-z_][A-Za-z0-9_']*\s*→", code)
            and re.search(r"[A-Za-z_][A-Za-z0-9_']*\.Nonempty\s*→", code)
            and re.search(
                r"ContinuousOn\s+[A-Za-z_][A-Za-z0-9_']*\s+[A-Za-z_][A-Za-z0-9_']*\s*→", code
            )
        ):
            return (
                "hcompact",
                "hne",
                "hcontinuous",
                "intro α _ _ _ _ f feasible hcompact hne hcontinuous\n",
            )
        return None

    def _monotone_convergence_context(self, code: str) -> tuple[str, str] | None:
        monotone = re.search(
            r"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*Monotone\s+([A-Za-z_][A-Za-z0-9_']*)\s*\)", code
        )
        if monotone is None:
            return None
        seq_name = monotone.group(2)
        bdd = re.search(
            rf"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*BddAbove\s+\((?:Set\.)?range\s+{re.escape(seq_name)}\)\s*\)",
            code,
        )
        if bdd is None:
            bdd = re.search(r"\(\s*([A-Za-z_][A-Za-z0-9_']*)\s*:\s*BddAbove\s+[^)]*\)", code)
        if bdd is None:
            return None
        return monotone.group(1), bdd.group(1)

    def _lemma_application_candidates(
        self,
        lemma_name: str,
        hypothesis_arg_names: list[str],
        explicit_goal_arg_names: list[str],
    ) -> list[str]:
        expressions = [lemma_name]
        prefix = lemma_name
        for name in hypothesis_arg_names:
            prefix = f"{prefix} {name}"
            expressions.append(prefix)

        seeded = list(expressions)
        for base in seeded:
            suffix = base
            for name in explicit_goal_arg_names:
                suffix = f"{suffix} {name}"
                expressions.append(suffix)

        expressions.sort(key=lambda expression: (-len(expression.split()), len(expression)))
        deduped: list[str] = []
        for expression in expressions:
            if expression not in deduped:
                deduped.append(expression)
        return deduped

    def _direct_candidate_proofs(
        self,
        *,
        packet: FormalizationPacket,
        current_code: str,
        include_fallback_tactics: bool = False,
    ) -> list[tuple[str, str, str]]:
        from src.planner.retrieval import _entry_tactic_hints, _load_metadata

        candidates: list[tuple[str, str, str]] = []
        policy = self._direct_close_policy(packet)
        theorem_goal = theorem_goal_statement(current_code) or ""
        goal_shape = classify_goal_shape(theorem_goal)
        parameter_names = theorem_parameter_names(current_code)
        explicit_parameter_names = theorem_explicit_parameter_names(current_code)
        intro_names = intro_names_from_body(current_code)
        hypothesis_arg_names: list[str] = []
        for name in [*parameter_names, *intro_names]:
            if name in hypothesis_arg_names:
                continue
            if name.startswith("h"):
                hypothesis_arg_names.append(name)
        explicit_goal_arg_names: list[str] = []
        for name in explicit_parameter_names:
            if name in hypothesis_arg_names or not name[:1].islower():
                continue
            if name not in explicit_goal_arg_names:
                explicit_goal_arg_names.append(name)
        hypothesis = direct_hypothesis_name(current_code)
        if hypothesis:
            candidates.extend(
                [
                    (f"exact {hypothesis}", "direct_hypothesis", f"Goal matches `{hypothesis}`."),
                    (
                        f"simpa using {hypothesis}",
                        "direct_hypothesis",
                        f"Normalize using `{hypothesis}`.",
                    ),
                ]
            )

        if policy.preamble_shortcuts_enabled:
            for entry in self._selected_preamble_entries(packet):
                metadata = _load_metadata(entry)
                hint_candidates = [
                    (hint, entry.name, f"Metadata tactic hint from `{entry.name}`.")
                    for hint in _entry_tactic_hints(entry, metadata)
                ]
                lemma_candidates: list[tuple[str, str, str]] = []
                rewrite_tokens = [str(name) for name in entry.definitions if str(name).strip()]
                for lemma_name in entry.planner_proven_lemmas:
                    for expression in self._lemma_application_candidates(
                        lemma_name,
                        hypothesis_arg_names,
                        explicit_goal_arg_names,
                    ):
                        lemma_candidates.append(
                            (
                                f"exact {expression}",
                                entry.name,
                                f"Exact preamble lemma `{expression}` closes the goal.",
                            )
                        )
                        lemma_candidates.append(
                            (
                                f"simpa using {expression}",
                                entry.name,
                                f"Simplify using preamble lemma `{expression}`.",
                            )
                        )
                        if rewrite_tokens:
                            lemma_candidates.append(
                                (
                                    f"simpa [{', '.join(rewrite_tokens)}] using {expression}",
                                    entry.name,
                                    f"Normalize definitional wrappers before using `{expression}`.",
                                )
                            )
                        if goal_shape.wrapper in _WRAPPER_SIMPA_SHAPES:
                            lemma_candidates.append(
                                (
                                    f"simpa [{goal_shape.wrapper}] using {expression}",
                                    entry.name,
                                    f"Normalize the `{goal_shape.wrapper}` wrapper before using `{expression}`.",
                                )
                            )
                if entry.name in _HINT_FIRST_PREAMBLES:
                    candidates.extend(hint_candidates)
                    candidates.extend(lemma_candidates)
                else:
                    candidates.extend(lemma_candidates)
                    candidates.extend(hint_candidates)

        if include_fallback_tactics:
            for tactic, rationale in SHORTCUT_FALLBACK_TACTICS:
                candidates.append((tactic, "fallback_tactic", rationale))

        deduped: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for proof, source, rationale in candidates:
            normalized = proof.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append((normalized, source, rationale))
        return deduped

    def _has_direct_candidates(self, *, packet: FormalizationPacket, current_code: str) -> bool:
        return bool(
            self._direct_candidate_proofs(
                packet=packet,
                current_code=current_code,
                include_fallback_tactics=False,
            )
        )

    def _has_targeted_fast_path(self, current_code: str) -> bool:
        if direct_hypothesis_name(current_code):
            return True
        if classify_goal_shape(theorem_goal_statement(current_code) or "").kind != "other":
            return True
        normalized = current_code.lower()
        return (
            any(char.isdigit() for char in normalized)
            or "nkpc" in normalized
            or any(token in normalized for token in ("field", "div", "/", "+", "-", "*", "^"))
        )

    def _try_direct_definable_closure(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        current_code: str,
        timeout: int,
        include_fallback_tactics: bool = False,
        job_id: str | None = None,
        on_progress: Callable[[str, dict[str, Any]], None] | None = None,
        attempt_budget: dict[str, int] | None = None,
    ) -> tuple[dict[str, Any] | None, DirectCloseAttemptSummary]:
        attempt_timeout = min(timeout, SHORTCUT_ATTEMPT_TIMEOUT_SECONDS)
        theorem_name = self._target_theorem_name(packet, target)
        policy = self._direct_close_policy(packet)
        mathlib_native_mode = policy.claim_type == "mathlib_native"
        candidates = self._direct_candidate_proofs(
            packet=packet,
            current_code=current_code,
            include_fallback_tactics=include_fallback_tactics,
        )
        remaining_budget = attempt_budget.get("remaining") if attempt_budget is not None else None
        attempt_cap = (
            policy.attempt_cap
            if remaining_budget is None
            else min(policy.attempt_cap, max(remaining_budget, 0))
        )
        attempt_limit = min(len(candidates), attempt_cap)
        attempts_used = 0
        for index, (proof, source, rationale) in enumerate(candidates[:attempt_limit], start=1):
            attempts_used = index
            if job_id is not None:
                self._emit_progress(
                    on_progress,
                    "prover_tool",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message=f"Direct closure attempt {index}/{attempt_limit}.",
                    metadata={
                        "target_name": target.name,
                        "tool_name": "compile_check",
                        "proof": proof,
                        "source": source,
                        "attempt_index": index,
                        "attempt_limit": attempt_limit,
                        "candidate_count": len(candidates),
                        "compile_timeout_seconds": attempt_timeout,
                        "claim_type": policy.claim_type,
                        "claim_type_policy": policy.claim_type_policy,
                        "direct_close_attempt_cap": policy.attempt_cap,
                        "preamble_shortcuts_enabled": policy.preamble_shortcuts_enabled,
                        "mathlib_native_mode": mathlib_native_mode,
                    },
                )
            try:
                candidate_code = _replace_named_theorem_body(current_code, theorem_name, proof)
            except ValueError:
                return None, DirectCloseAttemptSummary(
                    candidate_count=len(candidates),
                    attempt_limit=attempt_limit,
                    attempts_used=attempts_used,
                    claim_type=policy.claim_type,
                    claim_type_policy=policy.claim_type_policy,
                    preamble_shortcuts_enabled=policy.preamble_shortcuts_enabled,
                )
            try:
                result = compile_check(candidate_code, timeout=attempt_timeout)
            except Exception:
                continue
            if result.get("success"):
                if attempt_budget is not None and remaining_budget is not None:
                    attempt_budget["remaining"] = max(remaining_budget - attempts_used, 0)
                return (
                    {
                        "code": candidate_code,
                        "proof": proof,
                        "source": source,
                        "rationale": rationale,
                    },
                    DirectCloseAttemptSummary(
                        candidate_count=len(candidates),
                        attempt_limit=attempt_limit,
                        attempts_used=attempts_used,
                        claim_type=policy.claim_type,
                        claim_type_policy=policy.claim_type_policy,
                        preamble_shortcuts_enabled=policy.preamble_shortcuts_enabled,
                    ),
                )
        if attempt_budget is not None and remaining_budget is not None:
            attempt_budget["remaining"] = max(remaining_budget - attempts_used, 0)
        return None, DirectCloseAttemptSummary(
            candidate_count=len(candidates),
            attempt_limit=attempt_limit,
            attempts_used=attempts_used,
            claim_type=policy.claim_type,
            claim_type_policy=policy.claim_type_policy,
            preamble_shortcuts_enabled=policy.preamble_shortcuts_enabled,
        )

    def _record_direct_definable_closure(
        self,
        *,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        target: ProverTarget,
        turn: int,
        current_code: str,
        lean_feedback: list[str],
        proof: str,
        source: str,
        rationale: str,
        policy: DirectClosePolicy | None = None,
    ) -> None:
        claim_type = policy.claim_type if policy is not None else None
        claim_type_policy = policy.claim_type_policy if policy is not None else "default"
        preamble_shortcuts_enabled = (
            policy.preamble_shortcuts_enabled if policy is not None else True
        )
        mathlib_native_mode = claim_type == "mathlib_native"
        trace.append(
            ProverTraceStep(
                turn=turn,
                backend=backend.name,
                target_name=target.name,
                action_type="direct_definable_closure",
                success=True,
                rationale=rationale,
                tool_name="compile_check",
                tool_arguments={
                    "proof": proof,
                    "claim_type": claim_type,
                    "claim_type_policy": claim_type_policy,
                    "preamble_shortcuts_enabled": preamble_shortcuts_enabled,
                    "mathlib_native_mode": mathlib_native_mode,
                },
                tool_result=f"Closed via `{proof.splitlines()[0]}` using `{source}`.",
                lean_feedback=lean_feedback,
                goals=[],
                code_snapshot=current_code,
            )
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="direct_definable_closure",
                provider=backend.provider,
                model=backend.model,
                success=True,
                metadata={
                    "turn": turn,
                    "target_name": target.name,
                    "proof": proof,
                    "source": source,
                    "claim_type": claim_type,
                    "claim_type_policy": claim_type_policy,
                    "preamble_shortcuts_enabled": preamble_shortcuts_enabled,
                    "mathlib_native_mode": mathlib_native_mode,
                },
            )
        )

    def _try_compile_normalization_pass(
        self,
        *,
        theorem_name: str,
        current_code: str,
        timeout: int,
    ) -> dict[str, str] | None:
        attempt_timeout = min(timeout, SHORTCUT_ATTEMPT_TIMEOUT_SECONDS)
        for tactic in suggest_fast_path_tactics(current_code):
            try:
                candidate_code = _replace_named_theorem_body(current_code, theorem_name, tactic)
            except ValueError:
                return None
            try:
                result = compile_check(candidate_code, timeout=attempt_timeout)
            except Exception:
                continue
            if result.get("success"):
                return {
                    "code": candidate_code,
                    "proof": tactic,
                    "rationale": f"Recovered via low-cost normalization tactic `{tactic}`.",
                }
        return None

    def _try_repl_compile_recovery(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        current_code: str,
        timeout: int,
        include_fallback_tactics: bool = False,
        attempt_budget: dict[str, int] | None = None,
    ) -> tuple[dict[str, str] | None, DirectCloseAttemptSummary]:
        direct_close, direct_close_summary = self._try_direct_definable_closure(
            packet=packet,
            target=target,
            current_code=current_code,
            timeout=timeout,
            include_fallback_tactics=include_fallback_tactics,
            attempt_budget=attempt_budget,
        )
        if direct_close is not None:
            return direct_close, direct_close_summary
        normalization = self._try_compile_normalization_pass(
            theorem_name=self._target_theorem_name(packet, target),
            current_code=current_code,
            timeout=timeout,
        )
        if normalization is not None:
            return (
                {
                    "code": normalization["code"],
                    "proof": normalization["proof"],
                    "source": "compile_normalization",
                    "rationale": normalization["rationale"],
                },
                direct_close_summary,
            )
        return None, direct_close_summary

    def _metadata_tactic_hints(self, packet: FormalizationPacket) -> list[str]:
        from src.planner.retrieval import _entry_tactic_hints, _load_metadata

        hints: list[str] = []
        for entry in self._selected_preamble_entries(packet):
            metadata = _load_metadata(entry)
            for hint in _entry_tactic_hints(entry, metadata):
                normalized = hint.strip()
                if normalized and normalized not in hints:
                    hints.append(normalized)
        return hints

    def _state_fingerprint(
        self,
        *,
        session: _ActiveProofSession,
        theorem_name: str,
        lean_feedback: list[str],
    ) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        return (
            *self._structural_state_fingerprint(
                session=session,
                theorem_name=theorem_name,
            ),
            normalized_diagnostic_signature(lean_feedback),
        )

    def _structural_state_fingerprint(
        self,
        *,
        session: _ActiveProofSession,
        theorem_name: str,
    ) -> tuple[str, tuple[str, ...]]:
        return (
            _proof_body_fingerprint(session.read_code(), theorem_name),
            tuple(session.get_goals()),
        )

    def _progress_fingerprint(
        self,
        *,
        session: _ActiveProofSession,
        theorem_name: str,
        outcome: str,
        lean_feedback: list[str],
    ) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        state = self._state_fingerprint(
            session=session,
            theorem_name=theorem_name,
            lean_feedback=lean_feedback,
        )
        return (*state, outcome)

    def _failure_signature_key(
        self,
        *,
        session: _ActiveProofSession,
        theorem_name: str,
        lean_feedback: list[str],
        action_key: str,
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str]:
        return (
            self._state_fingerprint(
                session=session,
                theorem_name=theorem_name,
                lean_feedback=lean_feedback,
            ),
            action_key,
        )

    def _register_failure_signature(
        self,
        *,
        seen_failures: dict[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str], int],
        session: _ActiveProofSession,
        theorem_name: str,
        lean_feedback: list[str],
        action_key: str,
    ) -> int:
        signature = self._failure_signature_key(
            session=session,
            theorem_name=theorem_name,
            lean_feedback=lean_feedback,
            action_key=action_key,
        )
        seen_failures[signature] = seen_failures.get(signature, 0) + 1
        return seen_failures[signature]

    def _active_goal_matches_theorem_goal(self, session: _ActiveProofSession) -> bool:
        goals = session.get_goals()
        if not goals:
            return False
        theorem_goal = theorem_goal_statement(session.read_code())
        if theorem_goal is None:
            return False
        return normalized_goal_text(goals[0]) == normalized_goal_text(theorem_goal)

    def _deterministic_branch_tactics(
        self,
        *,
        packet: FormalizationPacket,
        current_code: str,
        include_fallback_tactics: bool,
    ) -> list[str]:
        intro_names = set(intro_names_from_body(current_code))
        ranked: list[tuple[tuple[int, int, int, int], str]] = []
        for proof, _source, rationale in self._direct_candidate_proofs(
            packet=packet,
            current_code=current_code,
            include_fallback_tactics=include_fallback_tactics,
        ):
            prefix_rank = 0 if proof.startswith(("exact ", "simpa ")) else 1
            intro_rank = 0 if any(name in proof for name in intro_names) else 1
            hint_rank = (
                0
                if "Metadata tactic hint" in rationale
                and proof.startswith(("exact ", "simpa ", "refine "))
                else 1
            )
            ranked.append(((prefix_rank, intro_rank, hint_rank, len(proof)), proof))
        tactics: list[str] = []
        for _score, proof in sorted(ranked, key=lambda item: item[0]):
            if proof not in tactics:
                tactics.append(proof)
        return tactics

    def _apply_deterministic_repair(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        session: _ActiveProofSession,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        turn: int,
        timeout: int,
        lean_feedback: list[str],
        include_fallback_tactics: bool,
        scaffold_attempts: set[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str]],
        branch_tactic_attempts: set[tuple[tuple[str, tuple[str, ...], tuple[str, ...]], str]],
        exhausted_direct_closure_states: dict[tuple[str, tuple[str, ...]], dict[str, Any]],
        direct_close_budget: dict[str, int] | None,
    ) -> tuple[bool, str | None, ProverFailure | None]:
        theorem_name = self._target_theorem_name(packet, target)
        policy = self._direct_close_policy(packet)
        goals = session.get_goals()
        if goals:
            state_key = self._state_fingerprint(
                session=session,
                theorem_name=theorem_name,
                lean_feedback=lean_feedback,
            )
            goal_shape = goal_shape_scaffold(
                goals[0],
                tactic_hints=self._metadata_tactic_hints(packet),
            )
            scaffold_tactic = goal_shape.scaffold_tactic if goal_shape is not None else None
            if (
                scaffold_tactic is not None
                and (state_key, scaffold_tactic) not in scaffold_attempts
            ):
                scaffold_attempts.add((state_key, scaffold_tactic))
                tool = ProverToolInvocation(
                    name="apply_tactic", arguments={"tactic": scaffold_tactic}
                )
                tool_result = self._execute_tool(
                    session=session,
                    tool=tool,
                    packet=packet,
                    target=target,
                )
                trace.append(
                    ProverTraceStep(
                        turn=turn,
                        backend=backend.name,
                        target_name=target.name,
                        action_type="deterministic_scaffold",
                        success=not tool_result.is_error,
                        rationale=f"Apply a generic `{goal_shape.kind}` scaffold before another provider turn.",
                        tool_name="apply_tactic",
                        tool_arguments={"tactic": scaffold_tactic},
                        tool_result=tool_result.content,
                        lean_feedback=lean_feedback,
                        goals=session.get_goals(),
                        code_snapshot=session.read_code(),
                        error_code=self._tool_error_code("apply_tactic", tool_result.content)
                        if tool_result.is_error
                        else None,
                    )
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="deterministic_scaffold",
                        provider=backend.provider,
                        model=backend.model,
                        success=not tool_result.is_error,
                        error_code=self._tool_error_code("apply_tactic", tool_result.content)
                        if tool_result.is_error
                        else None,
                        error_message=tool_result.content if tool_result.is_error else None,
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "tactic": scaffold_tactic,
                        },
                    )
                )
                if tool_result.is_error and "no_progress_stall:" in tool_result.content:
                    return (
                        False,
                        session.read_code(),
                        ProverFailure(
                            reason="no_progress_stall",
                            message="Deterministic scaffolding did not change the active goal.",
                            error_code="unsolved_goals",
                            target_name=target.name,
                            turn=turn,
                            backend=backend.name,
                            lean_feedback=session.get_goals(),
                        ),
                    )
                return False, session.read_code(), None

        structural_state = self._structural_state_fingerprint(
            session=session,
            theorem_name=theorem_name,
        )
        can_retry_direct_recovery = structural_state not in exhausted_direct_closure_states and (
            include_fallback_tactics
            or self._has_direct_candidates(packet=packet, current_code=session.read_code())
        )
        if can_retry_direct_recovery:
            recovery, recovery_summary = self._try_repl_compile_recovery(
                packet=packet,
                target=target,
                current_code=session.read_code(),
                timeout=timeout,
                include_fallback_tactics=include_fallback_tactics,
                attempt_budget=direct_close_budget,
            )
            if recovery is not None:
                session.write_code(recovery["code"])
                self._record_direct_definable_closure(
                    trace=trace,
                    audit_events=audit_events,
                    backend=backend,
                    target=target,
                    turn=turn,
                    current_code=session.read_code(),
                    lean_feedback=lean_feedback,
                    proof=recovery["proof"],
                    source=recovery["source"],
                    rationale=recovery["rationale"],
                    policy=policy,
                )
                return True, session.read_code(), None
            if recovery_summary.exhausted:
                exhausted_direct_closure_states[structural_state] = recovery_summary.metadata()

        if session.get_goals() and not self._active_goal_matches_theorem_goal(session):
            state_key = self._state_fingerprint(
                session=session,
                theorem_name=theorem_name,
                lean_feedback=lean_feedback,
            )
            attempted_branch_tactic = False
            for tactic in self._deterministic_branch_tactics(
                packet=packet,
                current_code=session.read_code(),
                include_fallback_tactics=include_fallback_tactics,
            ):
                if (state_key, tactic) in branch_tactic_attempts:
                    continue
                attempted_branch_tactic = True
                branch_tactic_attempts.add((state_key, tactic))
                tool = ProverToolInvocation(name="apply_tactic", arguments={"tactic": tactic})
                tool_result = self._execute_tool(
                    session=session,
                    tool=tool,
                    packet=packet,
                    target=target,
                )
                trace.append(
                    ProverTraceStep(
                        turn=turn,
                        backend=backend.name,
                        target_name=target.name,
                        action_type="deterministic_branch_tactic",
                        success=not tool_result.is_error,
                        rationale="Apply a metadata-backed branch tactic before another provider turn.",
                        tool_name="apply_tactic",
                        tool_arguments={"tactic": tactic},
                        tool_result=tool_result.content,
                        lean_feedback=lean_feedback,
                        goals=session.get_goals(),
                        code_snapshot=session.read_code(),
                        error_code=self._tool_error_code("apply_tactic", tool_result.content)
                        if tool_result.is_error
                        else None,
                        repl_local_solved=not tool_result.is_error and bool(session.solved),
                    )
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="deterministic_branch_tactic",
                        provider=backend.provider,
                        model=backend.model,
                        success=not tool_result.is_error,
                        error_code=self._tool_error_code("apply_tactic", tool_result.content)
                        if tool_result.is_error
                        else None,
                        error_message=tool_result.content if tool_result.is_error else None,
                        metadata={"turn": turn, "target_name": target.name, "tactic": tactic},
                    )
                )
                if tool_result.is_error and "no_progress_stall:" in tool_result.content:
                    return (
                        False,
                        session.read_code(),
                        ProverFailure(
                            reason="no_progress_stall",
                            message="Deterministic branch tactics stopped making progress on the active goal.",
                            error_code="unsolved_goals",
                            target_name=target.name,
                            turn=turn,
                            backend=backend.name,
                            lean_feedback=session.get_goals(),
                        ),
                    )
                if not tool_result.is_error:
                    return False, session.read_code(), None
            if attempted_branch_tactic:
                return False, session.read_code(), None

        return False, None, None

    def _first_turn_hints(self, packet: FormalizationPacket) -> list[str]:
        from src.planner.retrieval import _entry_tactic_hints, _load_metadata
        from src.preamble_library import PREAMBLE_LIBRARY

        hints: list[str] = []
        for name in ("fixed_point_theorem", "value_function"):
            if name not in packet.selected_preamble:
                continue
            entry = PREAMBLE_LIBRARY.get(name)
            if entry is None:
                continue
            for hint in _entry_tactic_hints(entry, _load_metadata(entry)):
                if hint not in hints:
                    hints.append(hint)
        return hints

    def _target_theorem_name(self, packet: FormalizationPacket, target: ProverTarget) -> str:
        return (
            packet.theorem_name
            if target.kind == "theorem_body"
            else target.helper_theorem_name or target.name
        )

    def _record_claim_type_awareness(
        self,
        *,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        target: ProverTarget,
        turn: int,
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
        policy: DirectClosePolicy,
        mathlib_native_mode: bool,
    ) -> None:
        metadata = {
            "claim_type": policy.claim_type,
            "claim_type_policy": policy.claim_type_policy,
            "direct_close_attempt_cap": policy.attempt_cap,
            "preamble_shortcuts_enabled": policy.preamble_shortcuts_enabled,
            "mathlib_native_mode": mathlib_native_mode,
            "target_kind": target.kind,
        }
        if mathlib_native_mode:
            message = "claim_type = mathlib_native; mathlib_native_mode=True; skipping preamble-derived direct-close candidates."
            rationale = "Use the benchmark claim type to avoid spending prover turns on preamble-only shortcuts."
            tool_result = (
                "claim_type = mathlib_native; mathlib_native_mode=True; "
                "skipping preamble-derived direct-close candidates and capping direct-close attempts."
            )
        else:
            message = "claim_type = preamble_definable; mathlib_native_mode=False; preamble-derived direct-close candidates remain enabled."
            rationale = "Use the benchmark claim type to keep preamble-backed direct-close candidates enabled."
            tool_result = (
                "claim_type = preamble_definable; mathlib_native_mode=False; "
                "preamble-derived direct-close candidates remain enabled."
            )
        self._emit_progress(
            on_progress,
            "prover_turn",
            job_id=job_id,
            stage="prover",
            status="running_prover",
            message=message,
            metadata={"target_name": target.name, **metadata},
        )
        trace.append(
            ProverTraceStep(
                turn=turn,
                backend=backend.name,
                target_name=target.name,
                action_type="claim_type_awareness",
                success=True,
                rationale=rationale,
                tool_name="claim_type_policy",
                tool_arguments=metadata,
                tool_result=tool_result,
                code_snapshot="",
            )
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="claim_type_awareness",
                provider=backend.provider,
                model=backend.model,
                success=True,
                metadata={"turn": turn, "target_name": target.name, **metadata},
            )
        )

    def _normalize_mathlib_progress_failure(self, failure: ProverFailure) -> ProverFailure:
        if failure.reason != "no_progress_stall":
            return failure
        if not any(bool(delta.get("stall_detected")) for delta in self._progress_deltas):
            goal_count = len(failure.lean_feedback)
            self._progress_deltas.append(
                ProgressDelta(
                    goals_reduced=False,
                    complexity_reduced=False,
                    stall_detected=True,
                    goal_count_before=goal_count,
                    goal_count_after=goal_count,
                    complexity_before=sum(len(goal) for goal in failure.lean_feedback),
                    complexity_after=sum(len(goal) for goal in failure.lean_feedback),
                ).to_dict()
            )
        return ProverFailure(
            reason="progress_stall",
            message=failure.message,
            error_code=failure.error_code,
            target_name=failure.target_name,
            turn=failure.turn,
            backend=failure.backend,
            lean_feedback=list(failure.lean_feedback),
            repeated_action=failure.repeated_action,
        )

    def _direct_close_stall_failure(
        self,
        *,
        target: ProverTarget,
        turn: int,
        backend: ProverBackend,
        lean_feedback: list[str],
        exhaustion: dict[str, Any],
        error_code: str = "unsolved_goals",
        loop_pattern: str | None = None,
    ) -> ProverFailure:
        claim_type = exhaustion.get("claim_type")
        claim_type_note = f" under claim type `{claim_type}`" if isinstance(claim_type, str) else ""
        loop_note = f" Observed loop pattern: {loop_pattern}." if loop_pattern is not None else ""
        return ProverFailure(
            reason="no_progress_stall",
            message=(
                "Direct-close candidates were exhausted"
                f"{claim_type_note}, and the subsequent tool action did not change the proof state."
                f"{loop_note}"
            ),
            error_code=error_code,
            target_name=target.name,
            turn=turn,
            backend=backend.name,
            lean_feedback=lean_feedback,
        )

    def _recent_tool_steps(
        self,
        *,
        trace: list[ProverTraceStep],
        target: ProverTarget,
        limit: int = SHALLOW_LOOP_WINDOW,
    ) -> list[ProverTraceStep]:
        return [
            step for step in trace if step.target_name == target.name and step.tool_name is not None
        ][-limit:]

    def _detect_shallow_loop_pattern(
        self,
        *,
        trace: list[ProverTraceStep],
        target: ProverTarget,
    ) -> str | None:
        recent = self._recent_tool_steps(trace=trace, target=target)
        if len(recent) < SHALLOW_LOOP_WINDOW:
            return None

        names = [step.tool_name for step in recent]
        if names == ["apply_tactic", "get_goals", "apply_tactic", "get_goals"]:
            if all(step.success for step in recent):
                goal_signatures = {
                    tuple(step.goals) for step in recent if step.tool_name == "get_goals"
                }
                tactics = [
                    str(step.tool_arguments.get("tactic") or "")
                    for step in recent
                    if step.tool_name == "apply_tactic"
                ]
                if len(goal_signatures) == 1 and len(set(tactics)) == 1:
                    return "repeated `apply_tactic` -> `get_goals` cycle without changing the active goal"

        if names == [
            "write_current_code",
            "compile_current_code",
            "write_current_code",
            "compile_current_code",
        ]:
            compile_steps = [step for step in recent if step.tool_name == "compile_current_code"]
            if len(compile_steps) == 2 and all(not step.success for step in compile_steps):
                compile_failures = {step.tool_result for step in compile_steps}
                goal_signatures = {tuple(step.goals) for step in compile_steps}
                if len(compile_failures) == 1 and len(goal_signatures) == 1:
                    return "repeated `write_current_code` -> `compile_current_code` cycle with the same compile failure"

        return None

    def _repeated_solved_repl_tactic(
        self,
        *,
        trace: list[ProverTraceStep],
        target: ProverTarget,
    ) -> str | None:
        relevant = [
            step
            for step in trace
            if step.target_name == target.name
            and step.tool_name == "apply_tactic"
            and step.repl_local_solved
        ]
        if len(relevant) < 2:
            return None
        tactics = [str(step.tool_arguments.get("tactic") or "") for step in relevant[-2:]]
        return tactics[0] if tactics[0] and tactics[0] == tactics[1] else None

    def _build_targets(self, packet: FormalizationPacket) -> list[ProverTarget]:
        targets = [
            ProverTarget(name=subgoal.name, statement=subgoal.statement, kind="subgoal")
            for subgoal in packet.subgoals
        ]
        targets.append(
            ProverTarget(name="theorem_body", statement=packet.theorem_name, kind="theorem_body")
        )
        return targets

    def _try_trivial_shortcut(
        self,
        *,
        packet: FormalizationPacket,
        current_code: str,
        timeout: int,
    ) -> dict[str, Any] | None:
        shortcut, _summary = self._try_direct_definable_closure(
            packet=packet,
            target=ProverTarget(
                name="theorem_body", statement=packet.theorem_name, kind="theorem_body"
            ),
            current_code=current_code,
            timeout=timeout,
            include_fallback_tactics=True,
            attempt_budget=None,
        )
        if shortcut is None:
            return None
        return {
            "code": shortcut["code"],
            "tactic": shortcut["proof"],
            "hypothesis": shortcut["source"],
            "rationale": shortcut["rationale"],
        }

    def _resolve_target_timeouts(
        self,
        *,
        timeout: int,
        target_timeouts: ProverTargetTimeouts | None,
    ) -> ProverTargetTimeouts:
        overrides = target_timeouts or ProverTargetTimeouts()
        return ProverTargetTimeouts(
            theorem_body=overrides.theorem_body or timeout,
            subgoal=overrides.subgoal or timeout,
            apollo_lemma=overrides.apollo_lemma or timeout,
        )

    def _timeout_for_target(
        self, target: ProverTarget, target_timeouts: ProverTargetTimeouts
    ) -> int:
        value = getattr(target_timeouts, target.kind)
        assert value is not None
        return int(value)

    def _final_compile_timeout(self, target_timeouts: ProverTargetTimeouts) -> int:
        values = [
            int(value)
            for value in (
                target_timeouts.theorem_body,
                target_timeouts.subgoal,
                target_timeouts.apollo_lemma,
            )
            if value is not None
        ]
        return max(values) if values else 300

    def _reset_budget_tracker(self) -> None:
        self.budget_tracker.search_tool_calls = 0
        self.budget_tracker.total_tool_calls = 0
        self.budget_tracker.lsp_tool_calls = 0
        self.budget_tracker.native_search_attempts = 0
        self.budget_tracker.mathlib_native_mode_uses = 0
        self.budget_tracker.sub_agent_calls = 0
        self.budget_tracker.tool_history.clear()
        self.budget_tracker.sub_agent_history.clear()
        # Sprint 24: clear per-invocation enrichment caches via the LSP cache.
        if self._lsp_cache is not None:
            self._lsp_cache.clear()

    def _enrich_trace_context(
        self,
        *,
        packet: FormalizationPacket,
        targets: list[ProverTarget],
        trace: list[ProverTraceStep],
    ) -> None:
        policy = self._direct_close_policy(packet)
        target_kinds = {target.name: target.kind for target in targets}
        mathlib_native_mode = policy.claim_type == "mathlib_native"
        for step in trace:
            step.claim_type = step.claim_type or policy.claim_type
            step.claim_type_policy = step.claim_type_policy or policy.claim_type_policy
            step.mathlib_native_mode = bool(step.mathlib_native_mode or mathlib_native_mode)
            step.target_kind = step.target_kind or target_kinds.get(step.target_name)
            tool_name = step.tool_name or ""
            if tool_name in LSP_TOOL_NAMES or step.action_type == "mathlib_native_lsp_search":
                step.lsp_tool_call = True
            if (
                tool_name in NATIVE_SEARCH_TOOL_NAMES
                or step.action_type == "mathlib_native_lsp_search"
            ):
                step.native_search_attempt = True

    def _aggregate_stage_usage(self, provider_usage: list[TokenUsage]) -> TokenUsage | None:
        if not provider_usage:
            return None
        latest = provider_usage[-1]
        return TokenUsage(
            stage="prover",
            provider=latest.provider,
            model=latest.model,
            input_tokens=sum(usage.input_tokens or 0 for usage in provider_usage),
            output_tokens=sum(usage.output_tokens or 0 for usage in provider_usage),
            estimated_cost_usd=sum(usage.estimated_cost_usd or 0.0 for usage in provider_usage),
            latency_ms=sum(usage.latency_ms or 0.0 for usage in provider_usage),
            success=all(usage.success for usage in provider_usage),
            usage_source=latest.usage_source,
            error_code=next(
                (usage.error_code for usage in reversed(provider_usage) if usage.error_code), None
            ),
        )

    def _audit_summary(self, audit_events: list[AuditEvent]) -> dict[str, Any]:
        failure_counts: dict[str, int] = {}
        for event in audit_events:
            if event.error_code:
                failure_counts[event.error_code] = failure_counts.get(event.error_code, 0) + 1
        return {
            "event_count": len(audit_events),
            "latest_event": audit_events[-1].to_dict() if audit_events else None,
            "failure_counts": failure_counts,
            "events": [event.to_dict() for event in audit_events],
        }

    def _detect_repl_compile_disagreement(
        self,
        *,
        trace: list[ProverTraceStep],
        target: ProverTarget,
        turn: int,
        backend: ProverBackend,
        lean_feedback: list[str],
    ) -> ProverFailure | None:
        tactic = self._repeated_solved_repl_tactic(trace=trace, target=target)
        if tactic is None:
            return None
        return ProverFailure(
            reason="repl_compile_disagreement",
            message=(
                f"REPL reported tactic `{tactic}` closed the goal on two consecutive "
                "turns, but the global compile still fails. The local proof does not "
                "integrate with the surrounding theorem context."
            ),
            error_code="repl_compile_disagreement",
            target_name=target.name,
            turn=turn,
            backend=backend.name,
            lean_feedback=lean_feedback,
        )

    def _tool_error_code(self, tool_name: str, content: str) -> str | None:
        lowered = content.lower()
        if tool_name.startswith("lean_") and "unsupported" in lowered:
            return "lsp_unavailable"
        if tool_name.startswith("lean_") and "lsp" in lowered:
            return "lsp_unavailable"
        if tool_name in {"compile_current_code", "lean_run_code", "apply_tactic"}:
            return "compile_failed"
        if "unknown tool" in lowered:
            return "unknown_tool"
        return None

    def _execute_tool(
        self,
        *,
        session: _ActiveProofSession,
        tool: ProverToolInvocation,
        packet: FormalizationPacket,
        target: ProverTarget,
    ) -> ToolResult:
        self.budget_tracker.record(tool.name)
        call = ToolCall(id=f"{target.name}:{tool.name}", name=tool.name, arguments=tool.arguments)
        if tool.name == "read_current_code":
            return ToolResult(call.id, session.read_code())
        if tool.name == "write_current_code":
            code = str(tool.arguments.get("code", ""))
            if session.read_code().strip() == code.strip():
                return ToolResult(
                    call.id,
                    "no_progress_stall: write_current_code did not change the proof.",
                    is_error=True,
                )
            session.write_code(code)
            return ToolResult(call.id, "Updated the current proof code.")
        if tool.name == "lean_run_code":
            result = session.run_code(
                str(tool.arguments.get("code")) if "code" in tool.arguments else None
            )
            return ToolResult(call.id, json.dumps(result, ensure_ascii=True))
        if tool.name == "compile_current_code":
            result = session.compile_current_code()
            return ToolResult(
                call.id, json.dumps(result, ensure_ascii=True), is_error=not result["success"]
            )
        if tool.name == "get_goals":
            goals = session.get_goals()
            return ToolResult(call.id, "\n".join(goals) if goals else "All goals solved.")
        if tool.name == "apply_tactic":
            before_code = session.read_code()
            before_goals = session.get_goals()
            success, message = session.apply_tactic(str(tool.arguments.get("tactic", "")))
            if success and not session.solved:
                if (
                    before_code.strip() == session.read_code().strip()
                    and before_goals == session.get_goals()
                ):
                    return ToolResult(
                        call.id,
                        f"no_progress_stall: `{tool.arguments.get('tactic', '')}` did not change code or goals.",
                        is_error=True,
                    )
            return ToolResult(call.id, message, is_error=not success)
        if tool.name == "memory_retrieve":
            payload = self._memory_examples(packet)
            return ToolResult(call.id, json.dumps(payload, ensure_ascii=True))
        if tool.name in {
            "lean_goal",
            "lean_code_actions",
            "lean_hover_info",
            "lean_diagnostic_messages",
            "lean_file_outline",
        }:
            if session.proof_path is None:
                return ToolResult(
                    call.id,
                    "lsp_unavailable: no proof file is attached to the session.",
                    is_error=True,
                )
            line = int(tool.arguments.get("line", max(1, len(session.read_code().splitlines()))))
            column = int(tool.arguments.get("column", 1))
            try:
                if tool.name == "lean_goal":
                    payload = self.lsp_client.lean_goal(
                        session.proof_path, line=line, column=column
                    )
                elif tool.name == "lean_code_actions":
                    payload = self.lsp_client.lean_code_actions(session.proof_path, line=line)
                elif tool.name == "lean_diagnostic_messages":
                    payload = self.lsp_client.lean_diagnostic_messages(
                        session.proof_path,
                        severity=tool.arguments.get("severity"),
                        start_line=tool.arguments.get("start_line"),
                        end_line=tool.arguments.get("end_line"),
                    )
                elif tool.name == "lean_file_outline":
                    payload = self.lsp_client.lean_file_outline(
                        session.proof_path,
                        max_declarations=tool.arguments.get("max_declarations"),
                    )
                else:
                    payload = self.lsp_client.lean_hover_info(
                        session.proof_path, line=line, column=column
                    )
            except LeanLSPUnavailableError as exc:
                return ToolResult(call.id, f"lsp_unavailable: {exc}", is_error=True)
            return ToolResult(call.id, json.dumps(payload, ensure_ascii=True))
        if tool.name in {"lean_leansearch", "lean_loogle", "lean_local_search"}:
            query = str(tool.arguments.get("query", "")).strip()
            if not query:
                return ToolResult(call.id, "Missing query.", is_error=True)
            try:
                if tool.name == "lean_leansearch":
                    num_results = int(
                        tool.arguments.get("num_results", MATHLIB_NATIVE_LSP_SEARCH_RESULTS)
                    )
                    payload = self.lsp_client.lean_leansearch(query, num_results=num_results)
                elif tool.name == "lean_local_search":
                    limit = int(tool.arguments.get("limit", MATHLIB_NATIVE_LSP_SEARCH_RESULTS))
                    payload = self.lsp_client.lean_local_search(query, limit=limit)
                else:
                    num_results = int(
                        tool.arguments.get("num_results", MATHLIB_NATIVE_LSP_SEARCH_RESULTS)
                    )
                    payload = self.lsp_client.lean_loogle(query, num_results=num_results)
            except LeanLSPUnavailableError as exc:
                return ToolResult(call.id, f"lsp_unavailable: {exc}", is_error=True)
            return ToolResult(call.id, json.dumps(payload, ensure_ascii=True))
        return ToolResult(call.id, f"Unknown tool: {tool.name}", is_error=True)


DEFAULT_PROVER = Prover()
