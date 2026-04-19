"""REPL-path helpers for the prover harness."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from src.prover.tools import REPLToolDispatcher
from src.tools import ToolCall, ToolResult

_REPL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read_current_code",
        "compile_current_code",
        "get_goals",
        "write_current_code",
        "apply_tactic",
    }
)


def sync_repl_trace(
    verification_trace: dict[str, Any],
    repl_report: dict[str, Any],
) -> None:
    verification_trace["repl_used"] = bool(repl_report.get("used"))
    verification_trace["repl_success"] = bool(repl_report.get("success"))
    verification_trace["repl_attempt_count"] = len(repl_report.get("attempts") or [])
    verification_trace["repl_fallback_reason"] = repl_report.get("fallback_reason")


async def run_repl_fast_path(
    *,
    repl_session_factory: Callable[[], Any],
    repl_fast_path_fn: Callable[..., Any],
    repl_validation_result_fn: Callable[[dict[str, Any]], dict[str, Any]],
    current_code: str,
    job_id: str,
    max_steps: int,
    tactic_hint: str | None,
    telemetry: Any,
    file_controller: Any,
    attempts: list[dict[str, Any]],
    repl_report: dict[str, Any],
    verification_trace: dict[str, Any],
    stage_timings_ms: dict[str, float],
    stage_outcomes: dict[str, str],
    emit_progress: Callable[[str, dict[str, Any]], None],
    build_success_status: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any] | None]:
    repl_started_at = time.perf_counter()
    try:
        with repl_session_factory() as repl:
            fast_path_started_at = time.perf_counter()
            try:
                repl_report = (
                    await repl_fast_path_fn(
                        repl,
                        current_code,
                        max_attempts=max_steps,
                        job_id=job_id,
                        tactic_hint=tactic_hint,
                    )
                    or repl_report
                )
            finally:
                if telemetry is not None:
                    telemetry.record_lean(fast_path_started_at)
            stage_timings_ms["repl_fast_path"] += (
                time.perf_counter() - repl_started_at
            ) * 1000.0
            sync_repl_trace(verification_trace, repl_report)
            if repl_report["attempts"]:
                attempts.extend(repl_report["attempts"])
            emit_progress(
                "repl_fast_path",
                {
                    "success": repl_report["success"],
                    "attempts": repl_report["attempts"],
                    "fallback_reason": repl_report["fallback_reason"],
                    "tactic_hint": tactic_hint,
                },
            )
            stage_outcomes["repl_fast_path"] = (
                "success" if repl_report["success"] else "fallback"
            )
            if repl_report["success"]:
                candidate = repl_report["candidate_code"]
                candidate_result = repl_report["candidate_result"]
                if candidate is None or candidate_result is None:
                    raise RuntimeError(
                        "LeanInteract reported success without a materialized proof."
                    )
                file_controller.write_current_code(job_id, candidate)
                file_controller.checkpoint(job_id, len(repl_report["attempts"]))
                return build_success_status(candidate, candidate_result, repl_report), repl_report, None
    except Exception as exc:
        stage_timings_ms["repl_fast_path"] += (
            time.perf_counter() - repl_started_at
        ) * 1000.0
        stage_outcomes["repl_fast_path"] = "error"
        repl_report["fallback_reason"] = f"{type(exc).__name__}: {exc}"
        sync_repl_trace(verification_trace, repl_report)
        emit_progress("repl_fast_path_fallback", {"reason": repl_report["fallback_reason"]})

    repl_validation_check = repl_validation_result_fn(repl_report)
    sync_repl_trace(verification_trace, repl_report)
    return None, repl_report, repl_validation_check


class ReplToolOrchestrator:
    """Owns REPL-backed tool dispatch and solved-state materialization."""

    def __init__(
        self,
        *,
        repl: Any,
        theorem_code: str,
        file_controller: Any,
        job_id: str,
        budget_tracker: Any,
        telemetry: Any,
        theorem_name: str,
        attempts: list[dict[str, Any]],
        verification_trace: dict[str, Any] | None,
        timed_compile_check: Callable[..., dict[str, Any]],
        sync_classified_errors: Callable[..., Any],
        build_status: Callable[..., dict[str, Any]],
        completed_status: Callable[..., dict[str, Any]],
        goal_analyst_hint_fn: Callable[..., str | None],
    ) -> None:
        self.repl = repl
        self.theorem_code = theorem_code
        self.file_controller = file_controller
        self.job_id = job_id
        self.budget_tracker = budget_tracker
        self.telemetry = telemetry
        self.theorem_name = theorem_name
        self.attempts = attempts
        self.verification_trace = verification_trace
        self._timed_compile_check = timed_compile_check
        self._sync_classified_errors = sync_classified_errors
        self._build_status = build_status
        self._completed_status = completed_status
        self._goal_analyst_hint_fn = goal_analyst_hint_fn
        self.dispatcher: REPLToolDispatcher | None = None

    async def initialize(self) -> None:
        self.dispatcher = REPLToolDispatcher(
            repl=self.repl,
            theorem_code=self.theorem_code,
            file_controller=self.file_controller,
            job_id=self.job_id,
            budget_tracker=self.budget_tracker,
        )
        repl_initialized_at = time.perf_counter()
        try:
            await self.dispatcher.initialize()
        finally:
            if self.telemetry is not None:
                self.telemetry.record_lean(repl_initialized_at)

    def handles(self, tool_name: str) -> bool:
        return tool_name in _REPL_TOOL_NAMES

    def handle_tool_call(
        self,
        tool_call: ToolCall,
        *,
        read_without_act: int,
    ) -> ToolResult:
        if self.dispatcher is None:
            raise RuntimeError("Initialize the REPL orchestrator before handling tools.")

        lean_started_at = time.perf_counter()
        try:
            result = self.dispatcher.handle_tool_call(tool_call)
        finally:
            if self.telemetry is not None:
                self.telemetry.record_lean(lean_started_at)

        if tool_call.name == "apply_tactic" and result.is_error:
            context = self.dispatcher.get_analysis_context()
            hint = self._goal_analyst_hint_fn(
                tactic=str(tool_call.arguments.get("tactic", "")).strip(),
                lean_error=result.content,
                goals=list(context.get("goals", [])),
                tactic_history=list(context.get("tactic_history", [])),
            )
            if hint:
                self.budget_tracker.record_sub_agent_call("goal_analyst")
                return ToolResult(
                    tool_call.id,
                    f"{result.content}\n\nGoal Analyst Hint: {hint}",
                    is_error=True,
                )

        if tool_call.name == "read_current_code" and read_without_act >= 3:
            return ToolResult(
                tool_call.id,
                result.content
                + "\n\n[NOTE] You've read the code multiple times without acting. "
                "Try a tactic or rewrite the theorem.",
                result.is_error,
            )

        return result

    def should_finalize(self, tool_name: str | None, content: Any) -> bool:
        if self.dispatcher is None:
            return False
        try:
            if self.dispatcher.is_solved():
                return True
        except Exception:
            pass
        return bool(
            tool_name in {"compile_current_code", "apply_tactic", "get_goals", "write_current_code"}
            and isinstance(content, str)
            and "All goals solved." in content
        )

    def terminal_status(self) -> dict[str, Any]:
        if self.dispatcher is None:
            raise RuntimeError("Initialize the REPL orchestrator before finalizing.")

        current_code = self.dispatcher.build_final_code()
        compile_result = self._timed_compile_check(
            self.telemetry,
            current_code,
            filename=f"{self.job_id}_provider_repl_complete.lean",
        )
        self._sync_classified_errors(self.verification_trace, compile_result)
        if compile_result["success"]:
            return self._completed_status(
                compile_result=compile_result,
                current_code=current_code,
            )
        return self._build_status(
            "failed",
            {
                "status": "failed",
                "theorem": self.theorem_name,
                "compile": compile_result,
                "attempts": self.attempts,
                "tool_history": list(self.budget_tracker.tool_history),
                "tool_budget": self.budget_tracker.snapshot(),
                "termination_reason": "repl_materialization_compile_failed",
            },
            error="Provider-backed proof search reached a solved REPL state but the materialized proof did not compile.",
        )
