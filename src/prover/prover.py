"""Packet-aware prover with self-correction and bounded APOLLO decomposition."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from src.config import FORMALIZER_TIMEOUT, HF_TOKEN, MISTRAL_API_KEY, MISTRAL_BASE_URL, PROVER_BACKEND, PROVER_PROVIDER
from src.formalizer.models import FormalizationPacket
from src.lean import LeanREPLSession, compile_check, lean_run_code
from src.memory import ProofTraceStore, trace_store as default_trace_store
from src.observability import (
    AuditEvent,
    BudgetTracker,
    LeanLSPClient,
    LeanLSPUnavailableError,
    ProviderCallMetadata,
    SpanRecorder,
    TokenUsage,
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
    ProverToolInvocation,
    ProverTraceStep,
)
from src.prover.tactics import (
    failure_feedback_messages,
    repeated_noop_action,
    should_decompose,
    suggest_fast_path_tactics,
    validate_action,
)
from src.providers import normalize_huggingface_provider
from src.tools import ToolCall, ToolRegistry, ToolResult, build_default_registry


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
                notes="Fallback proving backend when Goedel stalls or fails.",
            ),
        }

    def get(self, name: str) -> ProverBackend:
        if name not in self._backends:
            raise KeyError(name)
        return self._backends[name]


def _extract_json_payload(raw_text: str) -> dict[str, object]:
    stripped = raw_text.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ProverDriverError("Prover backend did not return a JSON object.")
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise ProverDriverError(f"Prover backend returned invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ProverDriverError("Prover backend returned non-object JSON.")
    return payload


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
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ProverDriverError("Prover chat-completion response did not contain text content.")
        usage = getattr(raw, "usage", None)
        return ProverAction.model_validate(_extract_json_payload(content)), ProviderCallMetadata(
            input_tokens=int(usage.prompt_tokens) if getattr(usage, "prompt_tokens", None) is not None else None,
            output_tokens=int(usage.completion_tokens) if getattr(usage, "completion_tokens", None) is not None else None,
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
        return ProverAction.model_validate(_extract_json_payload(response_text)), ProviderCallMetadata(
            input_tokens=len(getattr(details, "prefill", []) or []) if details is not None else None,
            output_tokens=getattr(details, "generated_tokens", None) if details is not None else None,
            usage_source="provider" if details is not None else "estimated_chars",
            prompt_text=prompt,
            response_text=response_text,
        )

    def _should_fallback_to_text_generation(self, error: Exception) -> bool:
        message = str(error).lower()
        return "supported task: text-generation" in message or "supported task: text generation" in message

    def next_action(
        self,
        *,
        backend: ProverBackend,
        prompt: str,
    ) -> ProverAction | tuple[ProverAction, ProviderCallMetadata]:
        try:
            from huggingface_hub import InferenceClient
        except Exception as error:
            raise ProverDriverError("huggingface_hub is required for Hugging Face prover backends.") from error

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
    """Mistral chat-completions driver for Leanstral proving fallback."""

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

    def next_action(
        self,
        *,
        backend: ProverBackend,
        prompt: str,
    ) -> ProverAction | tuple[ProverAction, ProviderCallMetadata]:
        if not self.api_key:
            raise ProverDriverError("Mistral API key is required for the Leanstral prover backend.")
        payload = json.dumps(
            {
                "model": backend.model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": "You are a Lean theorem prover. Return only JSON."},
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
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
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="ignore")
            raise ProverDriverError(f"Mistral prover request failed: {body or error.reason}") from error
        except urllib.error.URLError as error:
            raise ProverDriverError(f"Mistral prover request failed: {error.reason}") from error

        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProverDriverError("Mistral prover response did not contain choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise ProverDriverError("Mistral prover response did not contain text content.")
        usage = raw.get("usage", {}) if isinstance(raw.get("usage"), dict) else {}
        return ProverAction.model_validate(_extract_json_payload(content)), ProviderCallMetadata(
            input_tokens=int(usage.get("prompt_tokens")) if usage.get("prompt_tokens") is not None else None,
            output_tokens=int(usage.get("completion_tokens")) if usage.get("completion_tokens") is not None else None,
            usage_source="provider" if usage else "estimated_chars",
            prompt_text=prompt,
            response_text=content,
        )


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
) -> str:
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
            "preferred_tactics": suggest_fast_path_tactics(current_code),
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
                message.data
                for message in response.get_errors()
                if getattr(message, "data", "")
            ]
            return False, "\n".join(errors) if errors else f"Tactic failed: {tactic}"
        self.code = self.repl.materialize_proof()
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
        self.fallback_backend = self.driver_registry.get("leanstral")
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

    async def prove(
        self,
        packet: FormalizationPacket,
        job_id: str,
        *,
        max_turns: int = 8,
        timeout: int = 300,
        allow_decomposition: bool = True,
    ) -> ProverResult:
        telemetry = SpanRecorder()
        trace: list[ProverTraceStep] = []
        provider_usage: list[TokenUsage] = []
        audit_events: list[AuditEvent] = []
        targets = self._build_targets(packet)
        attempted_backends: list[str] = []
        working_code = packet.lean_code
        self._extracted_lemmas = 0
        self.file_controller.initialize(job_id, working_code)

        failure: ProverFailure | None = None

        for index, target in enumerate(targets, start=1):
            target.status = "in_progress"
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
                    timeout=timeout,
                    allow_decomposition=allow_decomposition,
                    telemetry=telemetry,
                    provider_usage=provider_usage,
                    audit_events=audit_events,
                )
                if not proved:
                    target.status = "failed"
                    failure = target_failure
                    break
                theorem_block = _extract_theorem_block(produced_code)
                working_code = _inject_theorem_before_main(working_code, theorem_block)
                working_code = _replace_subgoal_with_helper(working_code, target.name, helper_name)
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
                timeout=timeout,
                allow_decomposition=allow_decomposition,
                telemetry=telemetry,
                provider_usage=provider_usage,
                audit_events=audit_events,
            )
            if not proved:
                target.status = "failed"
                failure = target_failure
                working_code = produced_code
                break
            working_code = produced_code
            self.file_controller.write_current_code(job_id, working_code)
            target.status = "proved"

        compile_started_at = telemetry.started_at
        final_compile = compile_check(
            working_code,
            timeout=timeout,
            filename=f"{job_id}_final.lean",
        )
        telemetry.record_lean(compile_started_at)
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
                    provider=stage_usage.provider if stage_usage is not None else self.primary_backend.provider,
                    model=stage_usage.model if stage_usage is not None else self.primary_backend.model,
                    success=True,
                    metadata={"termination_reason": "verified", "attempted_backends": attempted_backends},
                )
            )
            result = ProverResult(
                status="verified",
                theorem_name=packet.theorem_name,
                claim=packet.claim,
                verified_code=working_code,
                current_code=working_code,
                trace=trace,
                targets=targets,
                failure=None,
                termination_reason="verified",
                repair_count=sum(1 for step in trace if not step.success),
                preamble_names=list(packet.selected_preamble),
                backend_used=attempted_backends[-1] if attempted_backends else self.primary_backend.name,
                attempted_backends=attempted_backends,
                tool_budget=self.budget_tracker.snapshot(),
                telemetry=telemetry.snapshot(),
                usage_by_stage={"prover": stage_usage.to_dict()} if stage_usage is not None else {},
                timing_breakdown=timing_breakdown,
                audit_summary=self._audit_summary(audit_events),
            )
            log_event(
                "prover.stage_completed",
                stage="prover",
                provider=stage_usage.provider if stage_usage is not None else self.primary_backend.provider,
                model=stage_usage.model if stage_usage is not None else self.primary_backend.model,
                latency_ms=timing_breakdown["prover_ms"],
                input_tokens=stage_usage.input_tokens if stage_usage is not None else None,
                output_tokens=stage_usage.output_tokens if stage_usage is not None else None,
                estimated_cost_usd=stage_usage.estimated_cost_usd if stage_usage is not None else None,
            )
            self.memory_writer.record(packet, result)
            return result

        if failure is None:
            failure = ProverFailure(
                reason="final_compile_failed",
                error_code="compile_failed",
                message="Proof search ended, but the final code did not compile cleanly.",
                target_name="theorem_body",
                backend=attempted_backends[-1] if attempted_backends else self.primary_backend.name,
                lean_feedback=failure_feedback_messages(final_compile),
            )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="stage_failed",
                provider=stage_usage.provider if stage_usage is not None else self.primary_backend.provider,
                model=stage_usage.model if stage_usage is not None else self.primary_backend.model,
                success=False,
                error_code=failure.error_code or failure.reason,
                error_message=failure.message,
                metadata={"termination_reason": failure.reason, "attempted_backends": attempted_backends},
            )
        )

        result = ProverResult(
            status="failed",
            theorem_name=packet.theorem_name,
            claim=packet.claim,
            verified_code=None,
            current_code=working_code,
            trace=trace,
            targets=targets,
            failure=failure,
            termination_reason=failure.reason,
            repair_count=sum(1 for step in trace if not step.success),
            preamble_names=list(packet.selected_preamble),
            backend_used=attempted_backends[-1] if attempted_backends else self.primary_backend.name,
            attempted_backends=attempted_backends,
            tool_budget=self.budget_tracker.snapshot(),
            telemetry=telemetry.snapshot(),
            usage_by_stage={"prover": stage_usage.to_dict()} if stage_usage is not None else {},
            timing_breakdown=timing_breakdown,
            audit_summary=self._audit_summary(audit_events),
        )
        log_event(
            "prover.stage_failed",
            stage="prover",
            provider=stage_usage.provider if stage_usage is not None else self.primary_backend.provider,
            model=stage_usage.model if stage_usage is not None else self.primary_backend.model,
            latency_ms=timing_breakdown["prover_ms"],
            error_code=failure.error_code or failure.reason,
            input_tokens=stage_usage.input_tokens if stage_usage is not None else None,
            output_tokens=stage_usage.output_tokens if stage_usage is not None else None,
            estimated_cost_usd=stage_usage.estimated_cost_usd if stage_usage is not None else None,
        )
        self.memory_writer.record(packet, result)
        return result

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
        allow_decomposition: bool,
        telemetry: SpanRecorder,
        provider_usage: list[TokenUsage],
        audit_events: list[AuditEvent],
    ) -> tuple[bool, str, ProverFailure | None]:
        session = _ActiveProofSession(
            current_code,
            timeout,
            proof_path=self.file_controller.proof_path(job_id),
        )
        failed_turns = 0
        invalid_output_count = 0
        active_backend = self.primary_backend

        try:
            for turn in range(1, max_turns + 1):
                if not self.budget_tracker.can_continue():
                    return False, session.read_code(), ProverFailure(
                        reason="tool_budget_exhausted",
                        message="Tool budget exhausted before the proof converged.",
                        target_name=target.name,
                        turn=turn,
                        backend=active_backend.name,
                    )

                current_code = session.read_code()
                compile_result = session.compile_current_code()
                lean_feedback = failure_feedback_messages(compile_result)
                goals = session.get_goals()

                if compile_result["success"] and (not session.active_repl or not goals):
                    return True, current_code, None

                prompt = _build_prompt(
                    packet=packet,
                    target=target,
                    current_code=current_code,
                    tool_specs=[
                        {
                            "name": spec.name,
                            "description": spec.description,
                            "args": spec.args,
                        }
                        for spec in self.registry.list()
                    ],
                    lean_feedback=lean_feedback,
                    goals=goals,
                    prior_trace=trace,
                    examples=self._memory_examples(packet),
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
                            prompt_hash=stable_hash_text(metadata.prompt_text if metadata is not None else prompt),
                            response_hash=stable_hash_text(metadata.response_text if metadata is not None else None),
                            metadata={
                                "turn": turn,
                                "target_name": target.name,
                                "backend": active_backend.name,
                                "usage_source": usage.usage_source,
                            },
                        )
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
                    if invalid_output_count >= 2 and active_backend.name != self.fallback_backend.name:
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
                    continue

                if active_backend.name not in attempted_backends:
                    attempted_backends.append(active_backend.name)

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
                            metadata={"turn": turn, "target_name": target.name, "backend": active_backend.name},
                        )
                    )
                    failed_turns += 1
                    if invalid_output_count >= 2 and active_backend.name != self.fallback_backend.name:
                        active_backend = self.fallback_backend
                        invalid_output_count = 0
                    continue

                if repeated_noop_action(trace, action):
                    return False, current_code, ProverFailure(
                        reason="repeated_noop_action",
                        message="The prover repeated the same failed action twice.",
                        error_code="unsolved_goals",
                        target_name=target.name,
                        turn=turn,
                        backend=active_backend.name,
                        lean_feedback=lean_feedback,
                        repeated_action=True,
                    )

                if should_decompose(
                    failed_turns_for_target=failed_turns,
                    action=action,
                    allow_decomposition=allow_decomposition,
                    current_depth=target.recursion_depth,
                    total_extracted=self._extracted_lemmas,
                ):
                    decomposed, new_code = await self._run_decomposition(
                        packet=packet,
                        target=target,
                        session=session,
                        trace=trace,
                        attempted_backends=attempted_backends,
                        turn=turn,
                        timeout=timeout,
                        max_turns=max_turns,
                        action=action,
                        job_id=job_id,
                        telemetry=telemetry,
                        provider_usage=provider_usage,
                        audit_events=audit_events,
                    )
                    if decomposed:
                        target.status = "proved"
                        return True, new_code, None
                    return False, session.read_code(), ProverFailure(
                        reason="decomposition_limit_reached",
                        message="Decomposition did not produce a verified proof.",
                        error_code="unsolved_goals",
                        target_name=target.name,
                        turn=turn,
                        backend=active_backend.name,
                        lean_feedback=lean_feedback,
                    )

                if action.action_type == "finish":
                    return False, current_code, ProverFailure(
                        reason="provider_finished_without_proof",
                        message=action.finish_reason or "Provider stopped before the proof compiled.",
                        error_code="unsolved_goals",
                        target_name=target.name,
                        turn=turn,
                        backend=active_backend.name,
                        lean_feedback=lean_feedback,
                    )

                assert action.tool is not None
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
                    error_code=self._tool_error_code(action.tool.name, tool_result.content) if tool_result.is_error else None,
                )
                trace.append(step)
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
                if tool_result.is_error:
                    failed_turns += 1
                    continue
                failed_turns = 0

            return False, session.read_code(), ProverFailure(
                reason="max_turns_exhausted",
                message="Prover hit the configured maximum number of turns.",
                error_code="timeout",
                target_name=target.name,
                turn=max_turns,
                backend=attempted_backends[-1] if attempted_backends else self.primary_backend.name,
                lean_feedback=session.get_goals(),
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
        timeout: int,
        max_turns: int,
        action: ProverAction,
        job_id: str,
        telemetry: SpanRecorder,
        provider_usage: list[TokenUsage],
        audit_events: list[AuditEvent],
    ) -> tuple[bool, str]:
        if self._extracted_lemmas >= 3 or target.recursion_depth >= 2:
            return False, session.read_code()

        lemma_name = action.decomposition_name or f"apollo_{packet.theorem_name}_{self._extracted_lemmas + 1}"
        lemma_statement = action.decomposition_statement or target.statement
        self._extracted_lemmas += 1
        lemma_target = ProverTarget(
            name=lemma_name,
            statement=lemma_statement,
            kind="apollo_lemma",
            recursion_depth=target.recursion_depth + 1,
            helper_theorem_name=lemma_name,
        )
        lemma_code = _standalone_theorem_code(packet, lemma_name, lemma_statement)
        proved, produced_code, _failure = await self._prove_target(
            packet=packet,
            target=lemma_target,
            current_code=lemma_code,
            trace=trace,
            job_id=job_id,
            attempted_backends=attempted_backends,
            max_turns=max_turns,
            timeout=timeout,
            allow_decomposition=True,
            telemetry=telemetry,
            provider_usage=provider_usage,
            audit_events=audit_events,
        )
        if not proved:
            return False, session.read_code()

        theorem_block = _extract_theorem_block(produced_code)
        target_code = session.read_code()
        if target.kind == "theorem_body":
            rewritten = _inject_theorem_before_main(target_code, theorem_block)
            rewritten = _replace_last_sorry(rewritten, f"exact {lemma_name}")
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

        rewritten = _inject_theorem_before_main(target_code, theorem_block)
        rewritten = _replace_last_sorry(rewritten, f"exact {lemma_name}")
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

    def _build_targets(self, packet: FormalizationPacket) -> list[ProverTarget]:
        targets = [
            ProverTarget(name=subgoal.name, statement=subgoal.statement, kind="subgoal")
            for subgoal in packet.subgoals
        ]
        targets.append(
            ProverTarget(name="theorem_body", statement=packet.theorem_name, kind="theorem_body")
        )
        return targets

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
            error_code=next((usage.error_code for usage in reversed(provider_usage) if usage.error_code), None),
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
            session.write_code(code)
            return ToolResult(call.id, "Updated the current proof code.")
        if tool.name == "lean_run_code":
            result = session.run_code(str(tool.arguments.get("code")) if "code" in tool.arguments else None)
            return ToolResult(call.id, json.dumps(result, ensure_ascii=True))
        if tool.name == "compile_current_code":
            result = session.compile_current_code()
            return ToolResult(call.id, json.dumps(result, ensure_ascii=True), is_error=not result["success"])
        if tool.name == "get_goals":
            goals = session.get_goals()
            return ToolResult(call.id, "\n".join(goals) if goals else "All goals solved.")
        if tool.name == "apply_tactic":
            success, message = session.apply_tactic(str(tool.arguments.get("tactic", "")))
            return ToolResult(call.id, message, is_error=not success)
        if tool.name == "memory_retrieve":
            payload = self._memory_examples(packet)
            return ToolResult(call.id, json.dumps(payload, ensure_ascii=True))
        if tool.name in {"lean_goal", "lean_code_actions", "lean_hover_info"}:
            if session.proof_path is None:
                return ToolResult(call.id, "lsp_unavailable: no proof file is attached to the session.", is_error=True)
            line = int(tool.arguments.get("line", max(1, len(session.read_code().splitlines()))))
            column = int(tool.arguments.get("column", 1))
            try:
                if tool.name == "lean_goal":
                    payload = self.lsp_client.lean_goal(session.proof_path, line=line, column=column)
                elif tool.name == "lean_code_actions":
                    payload = self.lsp_client.lean_code_actions(session.proof_path, line=line)
                else:
                    payload = self.lsp_client.lean_hover_info(session.proof_path, line=line, column=column)
            except LeanLSPUnavailableError as exc:
                return ToolResult(call.id, f"lsp_unavailable: {exc}", is_error=True)
            return ToolResult(call.id, json.dumps(payload, ensure_ascii=True))
        return ToolResult(call.id, f"Unknown tool: {tool.name}", is_error=True)


DEFAULT_PROVER = Prover()
