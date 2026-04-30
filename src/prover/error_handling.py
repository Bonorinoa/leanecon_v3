"""Failure classification, trace enrichment, and stall detection for Prover.

The mixin preserves legacy private method names while isolating failure-event
construction from the main execution loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.formalizer.models import FormalizationPacket
from src.observability import AuditEvent, ProgressDelta, TokenUsage
from src.observability.tool_tracker import LSP_TOOL_NAMES, NATIVE_SEARCH_TOOL_NAMES
from src.prover.budget import SHALLOW_LOOP_WINDOW
from src.prover.drivers import ProverBackend
from src.prover.models import ProverFailure, ProverTarget, ProverTraceStep
from src.prover.tactics import normalized_diagnostic_signature

if TYPE_CHECKING:
    from src.prover.execution import _ActiveProofSession


def _compat_log_event(*args: Any, **kwargs: Any) -> Any:
    from src.prover import prover as prover_module

    return prover_module.log_event(*args, **kwargs)

def _compat_proof_body_fingerprint(code: str, theorem_name: str) -> str:
    from src.prover.execution import _proof_body_fingerprint

    return _proof_body_fingerprint(code, theorem_name)

class ProverErrorHandlingMixin:

    """Mixin extracted from the legacy Prover monolith."""

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
        _compat_log_event(
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
            _compat_proof_body_fingerprint(session.read_code(), theorem_name),
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
        from src.prover.tactics import normalized_goal_text, theorem_goal_statement

        theorem_goal = theorem_goal_statement(session.read_code())
        if theorem_goal is None:
            return False
        return normalized_goal_text(goals[0]) == normalized_goal_text(theorem_goal)

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
