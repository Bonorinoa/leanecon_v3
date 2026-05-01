"""Execution loop and Lean tool application for Prover.

This module contains the legacy proving loop, direct closure attempts, LSP search
execution, deterministic repair, and tool dispatch. Logic is moved, not changed.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

from src.config import (
    BENCHMARK_MAX_RECURSION_DEPTH,
    MATHLIB_SYNTHESIS_BEST_OF_N,
    MATHLIB_SYNTHESIS_HELPER_LEMMA_ENABLED,
)
from src.formalizer.models import FormalizationPacket
from src.observability import (
    AuditEvent,
    CandidateTacticEvent,
    LeanLSPUnavailableError,
    PremiseResolutionEvent,
    SpanRecorder,
    StateTransition,
    SynthesisEvent,
    TokenUsage,
    ToolUsageTrace,
    build_progress_event,
    classify_exception,
    complete_usage,
    stable_hash_text,
)
from src.prover.synthesizer import ResolvedPremise, TacticCandidate
from src.prover.budget import (
    DirectCloseAttemptSummary,
    DirectClosePolicy,
    MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT,
    MATHLIB_NATIVE_LSP_SEARCH_RESULTS,
    MATHLIB_NATIVE_SUBGOAL_MAX_TURNS,
    POST_DIRECT_CLOSURE_STALL_LIMIT,
    SHORTCUT_ATTEMPT_TIMEOUT_SECONDS,
    SHORTCUT_FALLBACK_TACTICS,
    _HINT_FIRST_PREAMBLES,
    _PROGRESS_CODE_WINDOW,
    _WRAPPER_SIMPA_SHAPES,
)
from src.prover.drivers import ProverBackend, _unwrap_action_response
from src.prover.models import (
    ProverAction,
    ProverFailure,
    ProverResult,
    ProverTarget,
    ProverTargetTimeouts,
    ProverToolInvocation,
    ProverTraceStep,
)
from src.prover.retrieval import _contains_lsp_unavailable
from src.prover.synthesis import _build_prompt
from src.prover.tactics import (
    classify_goal_shape,
    direct_hypothesis_name,
    failure_feedback_messages,
    goal_shape_scaffold,
    intro_names_from_body,
    repeated_noop_action,
    should_decompose,
    suggest_fast_path_tactics,
    theorem_explicit_parameter_names,
    theorem_goal_statement,
    theorem_parameter_names,
    validate_action,
)
from src.tools import ToolCall, ToolResult


def _compat_prover_module() -> Any:
    from src.prover import prover as prover_module

    return prover_module


def _compat_compile_check(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _compat_prover_module().compile_check(*args, **kwargs)


def _compat_lean_run_code(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _compat_prover_module().lean_run_code(*args, **kwargs)


def _compat_lean_repl_session() -> Any:
    return _compat_prover_module().LeanREPLSession


def _compat_log_event(*args: Any, **kwargs: Any) -> Any:
    return _compat_prover_module().log_event(*args, **kwargs)

def _count_standalone_sorries(code: str) -> int:
    return sum(1 for line in code.splitlines() if line.strip() == "sorry")

def _ensure_mathlib_import(code: str) -> str:
    if "import Mathlib\n" in code or code.startswith("import Mathlib"):
        return code
    first_import = re.search(r"^import ", code, re.MULTILINE)
    if first_import:
        return code[: first_import.start()] + "import Mathlib\n" + code[first_import.start() :]
    return "import Mathlib\n\n" + code


def _fix_concaveon_arg_order(code: str) -> str:
    """Fix LLM error: (Strict)ConcaveOn ℝ f s → ℝ s f when f is a function parameter.

    Mathlib's ConcaveOn/StrictConcaveOn take (𝕜 s f) — set before function.
    LLMs regularly swap these, producing ill-typed theorem declarations.
    """
    func_vars: set[str] = set(
        re.findall(
            r"(?:\{|\()([A-Za-z_][A-Za-z0-9_']*)(?:\s*:[^})\n]+?→[^})\n]+?)(?:\}|\))",
            code,
        )
    )
    if not func_vars:
        return code

    def _swap(match: re.Match) -> str:
        prefix, id1, id2 = match.group(1), match.group(2), match.group(3)
        if id1 in func_vars and id2 not in func_vars:
            return f"{prefix} {id2} {id1}"
        return match.group(0)

    return re.sub(
        r"((?:Strict)?ConcaveOn\s+ℝ)\s+([A-Za-z_][A-Za-z0-9_']*)\s+([A-Za-z_][A-Za-z0-9_']*)",
        _swap,
        code,
    )

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

def _replace_named_have_body(code: str, have_name: str, replacement: str) -> str:
    lines = code.splitlines()
    header_index: int | None = None
    header_indent = ""
    pattern = re.compile(rf"^(\s*)have\s+{re.escape(have_name)}\s*:\s*.+:=\s*by\s*$")
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        header_index = index
        header_indent = match.group(1)
        break
    if header_index is None:
        raise ValueError(f"Could not locate subgoal `{have_name}`.")

    body_start = header_index + 1
    body_end = body_start
    base_width = len(header_indent)
    while body_end < len(lines):
        line = lines[body_end]
        stripped = line.strip()
        if stripped:
            indent_width = len(line) - len(line.lstrip())
            if indent_width <= base_width:
                break
        body_end += 1

    replacement_indent = f"{header_indent}  "
    replacement_lines = [f"{replacement_indent}{part}" for part in replacement.splitlines()]
    return "\n".join(lines[:body_start] + replacement_lines + lines[body_end:]) + "\n"

def _replace_named_theorem_body(code: str, theorem_name: str, replacement: str) -> str:
    declaration = re.search(rf"(?m)^(theorem|lemma)\s+{re.escape(theorem_name)}\b", code)
    if declaration is None:
        raise ValueError(f"Could not locate theorem `{theorem_name}`.")
    header = re.search(r":=\s*by\s*\n", code[declaration.start() :], re.DOTALL)
    if header is None:
        raise ValueError(f"Could not locate proof body for `{theorem_name}`.")
    body_start = declaration.start() + header.end()
    replacement_block = "\n".join(f"  {part}" for part in replacement.splitlines())
    next_decl = re.search(r"(?m)^(/--|theorem |lemma )", code[body_start:])
    body_end = body_start + next_decl.start() if next_decl is not None else len(code)
    return f"{code[:body_start]}{replacement_block}\n{code[body_end:].lstrip()}"

def _top_level_theorem_names(code: str) -> list[str]:
    return re.findall(r"(?m)^(?:theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)\b", code)

def _replace_single_theorem_body(code: str, replacement: str) -> str:
    names = _top_level_theorem_names(code)
    if len(names) != 1:
        raise ValueError("Expected exactly one theorem or lemma proof site.")
    return _replace_named_theorem_body(code, names[0], replacement)

def _replace_target_proof_site(
    code: str,
    *,
    theorem_name: str,
    target_name: str,
    replacement: str,
) -> str:
    # For theorem_body targets, replace the whole body even when subgoal sorries remain
    # from bypassed helpers (which leave the working_code with multiple standalone sorries).
    if target_name == "theorem_body":
        try:
            return _replace_named_theorem_body(code, theorem_name, replacement)
        except ValueError:
            pass
    if _count_standalone_sorries(code) > 0:
        return _replace_last_sorry(code, replacement)
    try:
        return _replace_named_theorem_body(code, theorem_name, replacement)
    except ValueError:
        pass
    try:
        return _replace_named_have_body(code, target_name, replacement)
    except ValueError:
        pass
    try:
        return _replace_single_theorem_body(code, replacement)
    except ValueError:
        pass
    raise ValueError(f"Could not locate proof site for `{target_name}`.")

def _has_target_proof_site(code: str, *, theorem_name: str, target_name: str) -> bool:
    if _count_standalone_sorries(code) > 0:
        return True
    if re.search(rf"(?m)^(theorem|lemma)\s+{re.escape(theorem_name)}\b", code):
        return True
    return (
        re.search(rf"(?m)^\s*have\s+{re.escape(target_name)}\s*:\s*.+:=\s*by\s*$", code)
        is not None
    )

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
    imports: list[str] = []
    for module in [*packet.imports, *packet.selected_imports]:
        if module and module not in imports:
            imports.append(module)
    if packet.claim_type == "mathlib_native" and "Mathlib" not in imports:
        imports.insert(0, "Mathlib")
    lines = [*(f"import {module}" for module in imports)]
    if packet.open_statements:
        lines.append("")
        lines.extend(f"open {statement_}" for statement_ in packet.open_statements)
    lines.append("")
    lines.append(f"theorem {theorem_name} : {statement} := by")
    lines.append("  sorry")
    lines.append("")
    return "\n".join(lines)

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
        return _compat_compile_check(self.code, timeout=self.timeout)

    def run_code(self, code: str | None = None) -> dict[str, Any]:
        return _compat_lean_run_code(code or self.code, timeout=self.timeout)

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
        if _compat_lean_repl_session() is None or _count_standalone_sorries(self.code) != 1:
            return
        try:
            repl = _compat_lean_repl_session()(timeout=self.timeout)
            state = repl.start_proof(self.code, timeout=self.timeout)
        except Exception:
            return
        self.repl = repl
        self.active_repl = True
        self.goals = list(getattr(state, "goals", []) or [])
        self.solved = bool(getattr(state, "is_solved", False))

class ProverExecutionMixin:

    """Mixin extracted from the legacy Prover monolith."""

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
        if self._normalized_claim_type(packet) == "mathlib_native":
            working_code = _ensure_mathlib_import(working_code)
            working_code = _fix_concaveon_arg_order(working_code)
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
        self._synthesis_events = []
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
                    target_max_turns = max_turns
                    if self._normalized_claim_type(packet) == "mathlib_native":
                        target_max_turns = min(max_turns, MATHLIB_NATIVE_SUBGOAL_MAX_TURNS)
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
                        max_turns=target_max_turns,
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
                        if (
                            self._normalized_claim_type(packet) == "mathlib_native"
                            and any(
                                remaining.kind == "theorem_body"
                                for remaining in targets_to_iterate[index:]
                            )
                        ):
                            target.status = "skipped"
                            self._emit_progress(
                                on_progress,
                                "prover_turn",
                                job_id=job_id,
                                stage="prover",
                                status="running_prover",
                                message=(
                                    "Mathlib-native subgoal failed; continuing to the main "
                                    "theorem body."
                                ),
                                metadata={
                                    "target_name": target.name,
                                    "target_kind": target.kind,
                                    "failure_code": target_failure.error_code
                                    if target_failure is not None
                                    else None,
                                    "fallback_target": "theorem_body",
                                },
                            )
                            audit_events.append(
                                AuditEvent(
                                    stage="prover",
                                    event_type="mathlib_native_subgoal_bypass",
                                    provider=self.primary_backend.provider,
                                    model=self.primary_backend.model,
                                    success=True,
                                    metadata={
                                        "target_name": target.name,
                                        "failure_code": target_failure.error_code
                                        if target_failure is not None
                                        else None,
                                        "fallback_target": "theorem_body",
                                    },
                                )
                            )
                            continue
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
            final_compile = _compat_compile_check(
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
                    synthesis_events=list(self._synthesis_events),
                )
                _compat_log_event(
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
                synthesis_events=list(self._synthesis_events),
            )
            _compat_log_event(
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
                    harness_closed, harness_failure = await self._try_mathlib_native_harness_loop(
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
                        target_timeouts=target_timeouts,
                        max_turns=max_turns,
                        allow_decomposition=allow_decomposition,
                        max_recursion_depth=max_recursion_depth,
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
                    if target.kind in {"subgoal", "apollo_lemma"}:
                        self._emit_progress(
                            on_progress,
                            "prover_turn",
                            job_id=job_id,
                            stage="prover",
                            status="running_prover",
                            message=(
                                "Mathlib-native helper search exhausted; skipping provider "
                                "fallback for this helper."
                            ),
                            metadata={
                                "turn": turn,
                                "target_name": target.name,
                                "target_kind": target.kind,
                                "claim_type": direct_close_policy.claim_type,
                                "mathlib_native_mode": True,
                            },
                        )
                        audit_events.append(
                            AuditEvent(
                                stage="prover",
                                event_type="mathlib_native_helper_search_exhausted",
                                provider=active_backend.provider,
                                model=active_backend.model,
                                success=False,
                                error_code="native_helper_search_exhausted",
                                metadata={
                                    "turn": turn,
                                    "target_name": target.name,
                                    "target_kind": target.kind,
                                },
                            )
                        )
                        return (
                            False,
                            session.read_code(),
                            ProverFailure(
                                reason="native_helper_search_exhausted",
                                message=(
                                    "Validated mathlib-native helper search exhausted before "
                                    "the provider fallback."
                                ),
                                error_code="native_helper_search_exhausted",
                                target_name=target.name,
                                turn=turn,
                                backend=active_backend.name,
                                lean_feedback=session.get_goals(),
                            ),
                        )

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
                if mathlib_native_mode and action.tool.name == "apply_tactic":
                    recent_premises: list[dict[str, Any]] = []
                    seen_premise_names: set[str] = set()
                    for retrieval_payload in reversed(self._retrieval_events):
                        for premise in retrieval_payload.get("retrieved_premises") or []:
                            if not isinstance(premise, dict):
                                continue
                            premise_name = str(premise.get("name") or "")
                            if premise_name in seen_premise_names:
                                continue
                            seen_premise_names.add(premise_name)
                            recent_premises.append(premise)
                        if len(recent_premises) >= 10:
                            break
                    premise_match = self._proof_synthesizer.premise_match(
                        str(action.tool.arguments.get("tactic") or ""),
                        recent_premises,
                    )
                    synthesis_payload = SynthesisEvent(
                        tactic=str(action.tool.arguments.get("tactic") or ""),
                        referenced_premises=premise_match.referenced_premises,
                        top3_match=premise_match.top3_match,
                        success=not tool_result.is_error,
                        target_name=target.name,
                        claim_id=packet.theorem_name,
                        decomposition_depth=target.recursion_depth,
                    ).to_dict()
                    self._synthesis_events.append(synthesis_payload)
                    step.tool_arguments["SynthesisEvent"] = synthesis_payload
                    self._emit_progress(
                        on_progress,
                        "synthesis_event",
                        job_id=job_id,
                        stage="prover",
                        status="running_prover",
                        message="Recorded SynthesisEvent.",
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "SynthesisEvent": synthesis_payload,
                        },
                    )
                    audit_events.append(
                        AuditEvent(
                            stage="prover",
                            event_type="SynthesisEvent",
                            provider=active_backend.provider,
                            model=active_backend.model,
                            success=bool(synthesis_payload.get("success"))
                            and bool(synthesis_payload.get("referenced_premises")),
                            metadata={
                                "turn": turn,
                                "target_name": target.name,
                                **synthesis_payload,
                            },
                        )
                    )
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
                if (
                    mathlib_native_mode
                    and action.tool.name == "apply_tactic"
                    and not tool_result.is_error
                    and session.solved
                ):
                    final_ok, _final_error = self._final_compile_validation(
                        session=session,
                        target=target,
                        turn=turn,
                        backend=active_backend,
                        audit_events=audit_events,
                        job_id=job_id,
                        on_progress=on_progress,
                    )
                    if final_ok:
                        return True, session.read_code(), None
                    session.write_code(current_code)
                    failed_turns += 1
                    force_deterministic_recovery = True
                    continue
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

    def _record_premise_resolution_events(
        self,
        *,
        resolved_premises: list[ResolvedPremise],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        turn: int,
        target: ProverTarget,
        claim_id: str | None,
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
    ) -> None:
        for premise in resolved_premises:
            event = PremiseResolutionEvent(
                raw_name=premise.raw_name,
                resolved_name=premise.lean_name,
                resolved=premise.resolved,
                source=premise.source,
                resolution_method=premise.resolution_method,
                failure_reason=premise.failure_reason,
            )
            payload = event.to_dict()
            self._emit_progress(
                on_progress,
                "premise_resolution_event",
                job_id=job_id,
                stage="prover",
                status="running_prover",
                message="Recorded PremiseResolutionEvent.",
                metadata={
                    "turn": turn,
                    "target_name": target.name,
                    "claim_id": claim_id,
                    "PremiseResolutionEvent": payload,
                },
            )
            audit_events.append(
                AuditEvent(
                    stage="prover",
                    event_type="PremiseResolutionEvent",
                    provider=backend.provider,
                    model=backend.model,
                    success=premise.resolved,
                    metadata={"turn": turn, "target_name": target.name, **payload},
                )
            )

    def _record_candidate_tactic_event(
        self,
        *,
        event: CandidateTacticEvent,
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        turn: int,
        target: ProverTarget,
        claim_id: str | None,
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        payload = event.to_dict()
        self._emit_progress(
            on_progress,
            "candidate_tactic_event",
            job_id=job_id,
            stage="prover",
            status="running_prover",
            message="Recorded CandidateTacticEvent.",
            metadata={
                "turn": turn,
                "target_name": target.name,
                "claim_id": claim_id,
                "CandidateTacticEvent": payload,
            },
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="CandidateTacticEvent",
                provider=backend.provider,
                model=backend.model,
                success=event.success and event.committed,
                metadata={"turn": turn, "target_name": target.name, **payload},
            )
        )
        return payload

    def _final_compile_validation(
        self,
        *,
        session: _ActiveProofSession,
        target: ProverTarget,
        turn: int,
        backend: ProverBackend,
        audit_events: list[AuditEvent],
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
    ) -> tuple[bool, str]:
        compile_result = session.compile_current_code()
        if bool(compile_result.get("success")) and not bool(compile_result.get("has_sorry")):
            return True, ""
        error_text = json.dumps(compile_result, ensure_ascii=True)
        self._emit_progress(
            on_progress,
            "repl_compile_disagreement",
            job_id=job_id,
            stage="prover",
            status="running_prover",
            message="REPL solved the local state, but final materialized compile failed.",
            metadata={
                "turn": turn,
                "target_name": target.name,
                "error": error_text,
                "code_hash": stable_hash_text(session.read_code()),
            },
        )
        audit_events.append(
            AuditEvent(
                stage="prover",
                event_type="repl_compile_disagreement",
                provider=backend.provider,
                model=backend.model,
                success=False,
                metadata={
                    "turn": turn,
                    "target_name": target.name,
                    "error": error_text,
                    "code_hash": stable_hash_text(session.read_code()),
                },
            )
        )
        return False, error_text

    def _try_resolved_candidate_tactics(
        self,
        *,
        packet: FormalizationPacket,
        target: ProverTarget,
        session: _ActiveProofSession,
        trace: list[ProverTraceStep],
        audit_events: list[AuditEvent],
        backend: ProverBackend,
        turn: int,
        before_state: dict[str, Any],
        candidates: list[TacticCandidate],
        merged_premises: list[dict[str, Any]],
        retrieval_payload: dict[str, Any],
        lean_feedback: list[str],
        claim_id: str | None,
        job_id: str,
        on_progress: Callable[[str, dict[str, Any]], None] | None,
    ) -> tuple[str | None, bool]:
        if not candidates:
            return None, False

        baseline_code = session.read_code()
        before_hash = str(before_state["state_hash"])
        target_theorem_name = self._target_theorem_name(packet, target)
        for candidate in candidates:
            session.write_code(baseline_code)
            tool = ProverToolInvocation(
                name="apply_tactic",
                arguments={
                    "tactic": candidate.tactic,
                    "candidate_origin": candidate.origin,
                    "candidate_premise_name": candidate.premise_name,
                },
            )
            used_compile_fallback = not session.active_repl
            if used_compile_fallback:
                self.budget_tracker.record("compile_check")
                try:
                    candidate_code = _replace_target_proof_site(
                        baseline_code,
                        theorem_name=target_theorem_name,
                        target_name=target.name,
                        replacement=candidate.tactic,
                    )
                except ValueError as exc:
                    compile_result = {"success": False, "errors": [str(exc)]}
                    candidate_code = baseline_code
                else:
                    compile_result = _compat_compile_check(
                        candidate_code,
                        timeout=session.timeout,
                    )
                compile_success = bool(compile_result.get("success")) and not bool(
                    compile_result.get("has_sorry")
                )
                if compile_success:
                    session.write_code(candidate_code)
                    session.solved = True
                    session.goals = []
                tool_result = ToolResult(
                    f"{target.name}:compile_check",
                    json.dumps(compile_result, ensure_ascii=True),
                    is_error=not compile_success,
                )
            else:
                tool_result = self._execute_tool(
                    session=session,
                    tool=tool,
                    packet=packet,
                    target=target,
                )
            after_state = self._mathlib_harness_state(session=session, goals=session.get_goals())
            after_hash = str(after_state["state_hash"])
            progress_delta = self._progress_delta_from_states(before_state, after_state)
            committed = not tool_result.is_error and (
                used_compile_fallback or not progress_delta.stall_detected
            )
            final_compile_error: str | None = None
            if committed and session.solved:
                final_ok, final_compile_error = self._final_compile_validation(
                    session=session,
                    target=target,
                    turn=turn,
                    backend=backend,
                    audit_events=audit_events,
                    job_id=job_id,
                    on_progress=on_progress,
                )
                committed = final_ok

            premise_match = self._proof_synthesizer.premise_match(
                candidate.tactic,
                merged_premises,
            )
            state_transition = StateTransition(
                goal_count_before=len(before_state.get("goals") or []),
                goal_count_after=len(after_state.get("goals") or []),
                progress_delta=progress_delta,
                state_hash_before=before_hash,
                state_hash_after=after_hash,
                turn_index=turn,
            )
            tool_usage = ToolUsageTrace(
                tool_name="compile_check" if used_compile_fallback else tool.name,
                args=tool.arguments,
                result=tool_result.content,
                state_hash_before=before_hash,
                state_hash_after=after_hash,
                success=not tool_result.is_error,
            )
            synthesis_event = SynthesisEvent(
                tactic=candidate.tactic,
                referenced_premises=premise_match.referenced_premises,
                top3_match=premise_match.top3_match,
                success=not tool_result.is_error,
                target_name=target.name,
                claim_id=claim_id,
                decomposition_depth=target.recursion_depth,
            )
            candidate_event = CandidateTacticEvent(
                tactic=candidate.tactic,
                origin=candidate.origin,
                premise_name=candidate.premise_name,
                success=not tool_result.is_error,
                committed=committed,
                progress_delta=progress_delta,
                error=final_compile_error
                or (tool_result.content if tool_result.is_error else None),
            )
            progress_payload = progress_delta.to_dict()
            transition_payload = state_transition.to_dict()
            tool_payload = tool_usage.to_dict()
            synthesis_payload = synthesis_event.to_dict()
            candidate_payload = self._record_candidate_tactic_event(
                event=candidate_event,
                audit_events=audit_events,
                backend=backend,
                turn=turn,
                target=target,
                claim_id=claim_id,
                job_id=job_id,
                on_progress=on_progress,
            )
            self._tool_usage_traces.append(tool_payload)
            self._state_transitions.append(transition_payload)
            self._progress_deltas.append(progress_payload)
            self._synthesis_events.append(synthesis_payload)
            trace.append(
                ProverTraceStep(
                    turn=turn,
                    backend=backend.name,
                    target_name=target.name,
                    action_type="mathlib_native_candidate_search",
                    success=committed,
                    rationale="Tried a tactic generated from a resolved retrieved premise.",
                    tool_name="compile_check" if used_compile_fallback else tool.name,
                    tool_arguments={
                        **tool.arguments,
                        "compile_fallback": used_compile_fallback,
                        "RetrievalEvent": retrieval_payload,
                        "ToolUsageTrace": tool_payload,
                        "StateTransition": transition_payload,
                        "ProgressDelta": progress_payload,
                        "SynthesisEvent": synthesis_payload,
                        "CandidateTacticEvent": candidate_payload,
                        "retrieved_premises": merged_premises,
                    },
                    tool_result=tool_result.content if final_compile_error is None else final_compile_error,
                    lean_feedback=lean_feedback,
                    goals=session.get_goals(),
                    code_snapshot=session.read_code(),
                    error_code=self._tool_error_code(tool.name, tool_result.content)
                    if tool_result.is_error
                    else ("repl_compile_disagreement" if final_compile_error else None),
                    repl_local_solved=not tool_result.is_error and bool(session.solved),
                )
            )
            for event_name, payload in (
                ("tool_usage_trace", tool_payload),
                ("state_transition", transition_payload),
                ("progress_delta", progress_payload),
                ("synthesis_event", synthesis_payload),
            ):
                self._emit_progress(
                    on_progress,
                    event_name,
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message=f"Recorded {payload['event_type']}.",
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        payload["event_type"]: payload,
                    },
                )

            if committed:
                if session.solved:
                    return session.read_code(), True
                return None, True
            session.write_code(baseline_code)

        session.write_code(baseline_code)
        return None, False

    async def _try_mathlib_native_harness_loop(
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
        target_timeouts: ProverTargetTimeouts,
        max_turns: int,
        allow_decomposition: bool,
        max_recursion_depth: int,
    ) -> tuple[str | None, ProverFailure | None]:
        before_state = self._mathlib_harness_state(session=session, goals=goals)
        claim_id = packet.theorem_name
        target_theorem_name = self._target_theorem_name(packet, target)
        has_proof_site = _has_target_proof_site(
            session.read_code(),
            theorem_name=target_theorem_name,
            target_name=target.name,
        )
        can_search_empty_subgoal = bool(
            target.kind == "subgoal" and has_proof_site and target.statement
        )
        # Stage 2-followup A: when the harness's probe shows an empty goal
        # state, running the model produces a redundant tactic that the stall
        # detector then flags as failure. Yield to the outer loop's LSP-search
        # fallback (the path that historically closed claims like
        # t2_contraction_mapping_fixed_point) instead of claiming closure —
        # signalling closure here would bypass the final compile check.
        if (
            not (before_state.get("goals") or [])
            and not goals
            and (session.solved or not can_search_empty_subgoal)
        ):
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
        effective_state = dict(before_state)
        if not (effective_state.get("goals") or []) and goals:
            effective_state["goals"] = list(goals)
        elif not (effective_state.get("goals") or []) and can_search_empty_subgoal:
            effective_state["goals"] = [target.statement]
        retrieval_event = self._retrieve_mathlib_premises(
            effective_state.get("goals") or [], k=5, claim_id=claim_id
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
            goals=effective_state.get("goals") or goals,
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
        resolved_premises = self._proof_synthesizer.resolve_premises(merged_premises)
        prompt_premises = self._proof_synthesizer.premise_prompt_records(
            merged_premises,
            resolved_premises,
        )
        self._record_premise_resolution_events(
            resolved_premises=resolved_premises,
            audit_events=audit_events,
            backend=backend,
            turn=turn,
            target=target,
            claim_id=claim_id,
            job_id=job_id,
            on_progress=on_progress,
        )
        proof_sketch = self._proof_synthesizer.build_sketch(
            packet=packet,
            target=target,
            state=effective_state,
            premises=prompt_premises,
        )
        tactic_candidates = self._proof_synthesizer.tactic_candidates(
            state=effective_state,
            premises=resolved_premises,
            limit=6,
        )
        candidate_code, candidate_committed = self._try_resolved_candidate_tactics(
            packet=packet,
            target=target,
            session=session,
            trace=trace,
            audit_events=audit_events,
            backend=backend,
            turn=turn,
            before_state=effective_state,
            candidates=tactic_candidates,
            merged_premises=prompt_premises,
            retrieval_payload=retrieval_payload,
            lean_feedback=lean_feedback,
            claim_id=claim_id,
            job_id=job_id,
            on_progress=on_progress,
        )
        if candidate_committed:
            return candidate_code, None
        prompt = self._build_mathlib_harness_prompt(
            packet=packet,
            target=target,
            state=effective_state,
            retrieved_premises=prompt_premises,
            diagnostics=diagnostics,
            code_actions=code_actions,
            prior_trace=trace,
            proof_sketch=proof_sketch.to_dict(),
        )
        provider_started_at = time.perf_counter()
        try:
            best_of_n = max(int(MATHLIB_SYNTHESIS_BEST_OF_N or 1), 1)
            if best_of_n > 1:
                candidates: list[tuple[ProverAction, Any]] = []
                for _sample_index in range(best_of_n):
                    raw_candidate = self._drivers[backend.provider].next_action(
                        backend=backend,
                        prompt=prompt,
                        temperature=0.7,
                    )
                    candidate_action, candidate_metadata = _unwrap_action_response(raw_candidate)
                    candidates.append((candidate_action, candidate_metadata))
                action, metadata = next(
                    (
                        (candidate_action, candidate_metadata)
                        for candidate_action, candidate_metadata in candidates
                        if candidate_action.action_type == "tool"
                        and candidate_action.tool is not None
                        and candidate_action.tool.name == "apply_tactic"
                        and self._proof_synthesizer.premise_match(
                            str(candidate_action.tool.arguments.get("tactic") or ""),
                            merged_premises[:3],
                        ).matched
                    ),
                    candidates[0],
                )
            else:
                raw_action = self._drivers[backend.provider].next_action(
                    backend=backend,
                    prompt=prompt,
                )
                action, metadata = _unwrap_action_response(raw_action)
            telemetry.record_provider(provider_started_at)
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
        provider_tactic_text = str(action.tool.arguments.get("tactic") or "")
        provider_premise_match = self._proof_synthesizer.premise_match(
            provider_tactic_text,
            prompt_premises,
        )
        synthesis_candidate_used = False
        provider_tactic_normalized = provider_tactic_text.strip()
        fast_provider_tactics = {
            "assumption",
            "trivial",
            "rfl",
            "simp",
            "simpa",
            "exact?",
            "norm_num",
            "linarith",
            "ring",
        }
        if not provider_premise_match.matched and provider_tactic_normalized in fast_provider_tactics:
            provider_premise_match = self._proof_synthesizer.premise_match(
                provider_tactic_text,
                prompt_premises,
            )
        tactic_text = str(action.tool.arguments.get("tactic") or "")
        premise_match = self._proof_synthesizer.premise_match(
            tactic_text,
            prompt_premises,
        )
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
        synthesis_event = SynthesisEvent(
            tactic=tactic_text,
            referenced_premises=premise_match.referenced_premises,
            top3_match=premise_match.top3_match,
            success=not tool_result.is_error,
            target_name=target.name,
            claim_id=claim_id,
            decomposition_depth=target.recursion_depth,
        )
        synthesis_payload = synthesis_event.to_dict()
        self._tool_usage_traces.append(tool_payload)
        self._state_transitions.append(transition_payload)
        self._progress_deltas.append(progress_payload)
        self._synthesis_events.append(synthesis_payload)
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
                    "SynthesisEvent": synthesis_payload,
                    "retrieved_premises": retrieval_event.retrieved_premises,
                    "synthesis_candidate_used": synthesis_candidate_used,
                    "provider_tactic": provider_tactic_text
                    if synthesis_candidate_used
                    else None,
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
            ("synthesis_event", synthesis_payload),
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
                event_type="SynthesisEvent",
                provider=backend.provider,
                model=backend.model,
                success=bool(synthesis_payload.get("success"))
                and bool(synthesis_payload.get("referenced_premises")),
                metadata={"turn": turn, "target_name": target.name, **synthesis_payload},
            )
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
            helper_skip_reason: str | None = None
            if not MATHLIB_SYNTHESIS_HELPER_LEMMA_ENABLED:
                helper_skip_reason = "helper_lemma_disabled"
            elif not allow_decomposition:
                helper_skip_reason = "decomposition_disabled"
            elif target.recursion_depth >= max_recursion_depth:
                helper_skip_reason = "max_recursion_depth_reached"
            elif self._extracted_lemmas >= 3:
                helper_skip_reason = "helper_lemma_limit_reached"
            else:
                helper_action = self._proof_synthesizer.helper_lemma_action(
                    packet=packet,
                    target=target,
                    state=after_state,
                    sketch=proof_sketch,
                    premises=merged_premises,
                    index=self._extracted_lemmas + 1,
                )
                if helper_action is None:
                    helper_skip_reason = "no_helper_statement"
                else:
                    helper_trace_start = len(trace)
                    decomposed, rewritten = await self._run_decomposition(
                        packet=packet,
                        target=target,
                        session=session,
                        trace=trace,
                        attempted_backends=attempted_backends,
                        turn=turn,
                        target_timeouts=target_timeouts,
                        max_turns=max_turns,
                        action=helper_action,
                        job_id=job_id,
                        max_recursion_depth=max_recursion_depth,
                        telemetry=telemetry,
                        provider_usage=provider_usage,
                        audit_events=audit_events,
                        on_progress=on_progress,
                    )
                    if decomposed:
                        tactic_sequence = [
                            str(step.tool_arguments.get("tactic") or "")
                            for step in trace[helper_trace_start:]
                            if step.tool_name == "apply_tactic"
                            and str(step.tool_arguments.get("tactic") or "").strip()
                        ]
                        try:
                            self.memory_writer.record_helper_lemma(
                                packet=packet,
                                lemma_name=helper_action.decomposition_name
                                or f"apollo_{packet.theorem_name}_synth",
                                lemma_statement=helper_action.decomposition_statement
                                or target.statement,
                                tactic_sequence=tactic_sequence,
                                parent_claim_id=claim_id,
                                retrieved_premises=merged_premises,
                                prover_backend=backend.name,
                            )
                        except Exception as exc:
                            audit_events.append(
                                AuditEvent(
                                    stage="prover",
                                    event_type="mathlib_helper_memory_write_failed",
                                    provider=backend.provider,
                                    model=backend.model,
                                    success=False,
                                    metadata={
                                        "turn": turn,
                                        "target_name": target.name,
                                        "error": str(exc),
                                    },
                                )
                            )
                        return rewritten, None
                    helper_skip_reason = "helper_decomposition_failed"
            if helper_skip_reason:
                self._emit_progress(
                    on_progress,
                    "mathlib_helper_lemma_skipped",
                    job_id=job_id,
                    stage="prover",
                    status="running_prover",
                    message="Skipped mathlib helper-lemma extraction after stalled tactic.",
                    metadata={
                        "turn": turn,
                        "target_name": target.name,
                        "claim_id": claim_id,
                        "reason": helper_skip_reason,
                    },
                )
                audit_events.append(
                    AuditEvent(
                        stage="prover",
                        event_type="mathlib_helper_lemma_skipped",
                        provider=backend.provider,
                        model=backend.model,
                        success=False,
                        metadata={
                            "turn": turn,
                            "target_name": target.name,
                            "claim_id": claim_id,
                            "reason": helper_skip_reason,
                        },
                    )
                )
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
            final_ok, final_error = self._final_compile_validation(
                session=session,
                target=target,
                turn=turn,
                backend=backend,
                audit_events=audit_events,
                job_id=job_id,
                on_progress=on_progress,
            )
            if not final_ok:
                return None, ProverFailure(
                    reason="repl_compile_disagreement",
                    message="Harness REPL solved the local goal, but the materialized theorem did not compile.",
                    error_code="repl_compile_disagreement",
                    target_name=target.name,
                    turn=turn,
                    backend=backend.name,
                    lean_feedback=[final_error],
                )
            return session.read_code(), None
        return None, None

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
        candidate_failures: list[dict[str, str | None]] = []
        for proof, source, lemma_name in candidates[:MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT]:
            try:
                candidate_code = _replace_target_proof_site(
                    code,
                    theorem_name=theorem_name,
                    target_name=target.name,
                    replacement=proof,
                )
            except ValueError as exc:
                if len(candidate_failures) < MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT:
                    candidate_failures.append(
                        {
                            "proof": proof,
                            "source": source,
                            "lemma_name": lemma_name,
                            "error": str(exc),
                        }
                    )
                continue
            compiled_candidate_count += 1
            self.budget_tracker.record("compile_check")
            try:
                result = _compat_compile_check(candidate_code, timeout=attempt_timeout)
            except Exception as exc:
                if len(candidate_failures) < MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT:
                    candidate_failures.append(
                        {
                            "proof": proof,
                            "source": source,
                            "lemma_name": lemma_name,
                            "error": str(exc),
                        }
                    )
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
            if len(candidate_failures) < MATHLIB_NATIVE_LSP_CANDIDATE_LIMIT:
                error_text = "\n".join(str(item) for item in result.get("errors") or [])
                if not error_text:
                    error_text = str(result.get("output") or "")[:500]
                candidate_failures.append(
                    {
                        "proof": proof,
                        "source": source,
                        "lemma_name": lemma_name,
                        "error": error_text[:500],
                    }
                )

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
            "candidate_failures": candidate_failures,
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
            # Generate intro prefix variants: the base prefix and a variant with a
            # trailing `_` to absorb extra hypotheses (e.g. StrictConcaveOn) that
            # appear after ContinuousOn in ∀-quantified theorem statements.
            intro_prefixes = [intro_prefix]
            if intro_prefix and intro_prefix.rstrip("\n"):
                intro_prefixes.append(intro_prefix.rstrip("\n") + " _\n")
            if "IsConstrainedMaximum" in current_code:
                for ip in intro_prefixes:
                    # Direct bridge via the LeanEcon preamble shortcut (preferred).
                    candidates.append(
                        (
                            f"{ip}exact exists_isConstrainedMaximum_of_isCompact_continuousOn {hcompact} {hnonempty} {hcontinuous}",
                            "local_heuristic",
                            "exists_isConstrainedMaximum_of_isCompact_continuousOn",
                        )
                    )
                    candidates.append(
                        (
                            "\n".join(
                                [
                                    f"{ip}obtain ⟨x, hx, hmax⟩ := IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                                    "refine ⟨x, hx, ?_⟩",
                                    "intro y hy",
                                    "simpa using hmax hy",
                                ]
                            ),
                            "lean_leansearch",
                            "IsCompact.exists_isMaxOn",
                        )
                    )
            else:
                for ip in intro_prefixes:
                    candidates.append(
                        (
                            f"{ip}exact IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                            "lean_leansearch",
                            "IsCompact.exists_isMaxOn",
                        )
                    )
                    candidates.append(
                        (
                            "\n".join(
                                [
                                    f"{ip}obtain ⟨x, hx, hmax⟩ := IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                                    "exact ⟨x, hx, hmax⟩",
                                ]
                            ),
                            "lean_leansearch",
                            "IsCompact.exists_isMaxOn",
                        )
                    )
            if not names or "IsCompact.exists_sSup_image_eq_and_ge" in names:
                for ip in intro_prefixes:
                    candidates.append(
                        (
                            "\n".join(
                                [
                                    f"{ip}obtain ⟨x, hx, _hsup, hmax⟩ := IsCompact.exists_sSup_image_eq_and_ge {hcompact} {hnonempty} {hcontinuous}",
                                    "exact ⟨x, hx, hmax⟩",
                                ]
                            ),
                            "lean_leansearch",
                            "IsCompact.exists_sSup_image_eq_and_ge",
                        )
                    )
        elif "IsConstrainedMaximum" in current_code:
            candidates.extend(self._compact_extreme_value_fallback_candidates())

        monotone = self._monotone_convergence_context(current_code)
        if monotone is not None:
            hmono, hbdd, seq_name = monotone
            candidates.append(
                (
                    f"exact ⟨⨆ i, {seq_name} i, tendsto_atTop_ciSup {hmono} {hbdd}⟩",
                    "lean_leansearch",
                    "tendsto_atTop_ciSup",
                )
            )
            candidates.append(
                (
                    f"exact tendsto_atTop_ciSup {hmono} {hbdd}",
                    "lean_leansearch",
                    "tendsto_atTop_ciSup",
                )
            )
        if monotone is not None and "tendsto_of_monotone" in names:
            hmono, hbdd, _seq_name = monotone
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
        elif all(token in current_code for token in ("Monotone", "BddAbove", "Tendsto")):
            candidates.extend(self._monotone_convergence_fallback_candidates())
        return candidates

    def _compact_extreme_value_fallback_candidates(self) -> list[tuple[str, str, str | None]]:
        candidates: list[tuple[str, str, str | None]] = []
        common_name_sets = [
            ("hcompact", "hnonempty", "hcontinuous"),
            ("hcompact", "hne", "hcontinuous"),
            ("h_compact", "h_nonempty", "h_continuous"),
            ("hs_compact", "hne", "h_continuous"),
            ("hcompact", "h_nonempty", "h_continuousOn"),
            ("h_compact", "hne", "h_continuous_on"),
        ]
        prioritized = [
            ("", *common_name_sets[0]),
            ("", *common_name_sets[1]),
            ("", *common_name_sets[2]),
            # ∀-form: implicit/instance binders are already in tactic context as outer
            # theorem arguments; only the explicit proposition arrows need intro.
            ("intro hcompact hnonempty hcontinuous\n", *common_name_sets[0]),
            ("intro hcompact hne hcontinuous\n", *common_name_sets[1]),
            ("intro h_compact h_nonempty h_continuous\n", *common_name_sets[2]),
            # Variants with trailing `_` to absorb an extra hypothesis (e.g. StrictConcaveOn).
            ("intro hcompact hnonempty hcontinuous _\n", *common_name_sets[0]),
            ("intro hcompact hne hcontinuous _\n", *common_name_sets[1]),
            ("intro h_compact h_nonempty h_continuous _\n", *common_name_sets[2]),
            ("", *common_name_sets[3]),
            ("", *common_name_sets[4]),
            ("", *common_name_sets[5]),
        ]
        for prefix, hcompact, hnonempty, hcontinuous in prioritized:
            candidates.append(
                (
                    "\n".join(
                        [
                            f"{prefix}obtain ⟨x, hx, hmax⟩ := IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                            "refine ⟨x, hx, ?_⟩",
                            "intro y hy",
                            "simpa using hmax hy",
                        ]
                    ),
                    "local_heuristic",
                    "IsCompact.exists_isMaxOn",
                )
            )
        for prefix in (
            "",
            "intro hcompact hnonempty hcontinuous\n",
            "intro hcompact hne hcontinuous\n",
            "intro h_compact h_nonempty h_continuous\n",
            "intro hcompact hnonempty hcontinuous _\n",
            "intro hcompact hne hcontinuous _\n",
        ):
            for hcompact, hnonempty, hcontinuous in common_name_sets:
                proof = "\n".join(
                    [
                        f"{prefix}obtain ⟨x, hx, hmax⟩ := IsCompact.exists_isMaxOn {hcompact} {hnonempty} {hcontinuous}",
                        "refine ⟨x, hx, ?_⟩",
                        "intro y hy",
                        "simpa using hmax hy",
                    ]
                )
                if any(existing[0] == proof for existing in candidates):
                    continue
                candidates.append(
                    (
                        proof,
                        "local_heuristic",
                        "IsCompact.exists_isMaxOn",
                    )
                )
        return candidates

    def _monotone_convergence_fallback_candidates(self) -> list[tuple[str, str, str | None]]:
        candidates: list[tuple[str, str, str | None]] = []
        common_name_sets = [
            ("u", "h_monotone", "h_bddAbove"),
            ("u", "hmono", "hbdd"),
            ("u", "hu_mono", "hu_bdd"),
            ("u", "h_mono", "h_bounded"),
            ("seq", "h_monotone", "h_bddAbove"),
            ("a", "h_monotone", "h_bddAbove"),
        ]
        prioritized: list[tuple[str, str, str, str]] = []
        prioritized.extend(("", *names) for names in common_name_sets[:3])
        prioritized.extend(("intro", *names) for names in common_name_sets[:3])
        prioritized.extend(("", *names) for names in common_name_sets[3:])
        prioritized.extend(("intro", *names) for names in common_name_sets[3:])
        seen: set[str] = set()
        for mode, seq_name, hmono, hbdd in prioritized:
            proof = (
                f"exact ⟨⨆ i, {seq_name} i, tendsto_atTop_ciSup {hmono} {hbdd}⟩"
                if mode == ""
                else (
                    f"intro {seq_name} {hmono} {hbdd}\n"
                    f"exact ⟨⨆ i, {seq_name} i, tendsto_atTop_ciSup {hmono} {hbdd}⟩"
                )
            )
            if proof in seen:
                continue
            seen.add(proof)
            candidates.append((proof, "local_heuristic", "tendsto_atTop_ciSup"))
        for seq_name, hmono, hbdd in common_name_sets:
            for proof in (
                f"exact ⟨⨆ i, {seq_name} i, tendsto_atTop_ciSup {hmono} {hbdd}⟩",
                (
                    f"intro {seq_name} {hmono} {hbdd}\n"
                    f"exact ⟨⨆ i, {seq_name} i, tendsto_atTop_ciSup {hmono} {hbdd}⟩"
                ),
            ):
                if proof in seen:
                    continue
                seen.add(proof)
                candidates.append(
                    (proof, "local_heuristic", "tendsto_atTop_ciSup")
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
            # In Lean 4, `theorem foo : ∀ {α} [T] {f} {s}, P → Q →` is sugar for
            # `theorem foo {α} [T] {f} {s} : P → Q →`.  The implicit/instance binders
            # are already in the tactic context before the proof block starts, so only
            # the explicit proposition arrows need `intro`.
            return (
                "hcompact",
                "hne",
                "hcontinuous",
                "intro hcompact hne hcontinuous\n",
            )
        return None

    def _monotone_convergence_context(self, code: str) -> tuple[str, str, str] | None:
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
        return monotone.group(1), bdd.group(1), seq_name

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
                result = _compat_compile_check(candidate_code, timeout=attempt_timeout)
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
                result = _compat_compile_check(candidate_code, timeout=attempt_timeout)
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
