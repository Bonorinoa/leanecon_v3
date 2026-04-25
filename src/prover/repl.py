"""REPL-path helpers for the prover harness."""

from __future__ import annotations

import time
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.observability import LeanLSPUnavailableError, default_lean_lsp_client
from src.observability.models import ProgressDelta, StateTransition, ToolUsageTrace
from src.prover.tools import REPLToolDispatcher
from src.tools import ToolCall, ToolResult

_REPL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "read_current_code",
        "compile_current_code",
        "get_goals",
        "write_current_code",
        "apply_tactic",
        "lean_goal",
        "lean_diagnostic_messages",
        "lean_code_actions",
        "code_actions",
        "lean_local_search",
        "lean_file_outline",
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
            stage_timings_ms["repl_fast_path"] += (time.perf_counter() - repl_started_at) * 1000.0
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
            stage_outcomes["repl_fast_path"] = "success" if repl_report["success"] else "fallback"
            if repl_report["success"]:
                candidate = repl_report["candidate_code"]
                candidate_result = repl_report["candidate_result"]
                if candidate is None or candidate_result is None:
                    raise RuntimeError(
                        "LeanInteract reported success without a materialized proof."
                    )
                file_controller.write_current_code(job_id, candidate)
                file_controller.checkpoint(job_id, len(repl_report["attempts"]))
                return (
                    build_success_status(candidate, candidate_result, repl_report),
                    repl_report,
                    None,
                )
    except Exception as exc:
        stage_timings_ms["repl_fast_path"] += (time.perf_counter() - repl_started_at) * 1000.0
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
        lsp_client: Any | None = None,
        proof_path: Path | None = None,
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
        self.lsp_client = lsp_client or default_lean_lsp_client
        self.proof_path = proof_path
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

    def get_current_state(self) -> dict[str, Any]:
        if self.dispatcher is None:
            raise RuntimeError("Initialize the REPL orchestrator before reading state.")
        code = self.dispatcher.build_final_code()
        context = self.dispatcher.get_analysis_context()
        goals = list(context.get("goals") or [])
        proof_path = self._proof_path()
        diagnostics: Any = None
        outline: Any = None
        if proof_path is not None:
            diagnostics = self._call_lsp(
                "lean_diagnostic_messages",
                lambda: self.lsp_client.lean_diagnostic_messages(proof_path),
            )
            outline = self._call_lsp(
                "lean_file_outline",
                lambda: self.lsp_client.lean_file_outline(proof_path, max_declarations=40),
            )
        code_hash = _stable_payload_hash({"code": code})
        state_hash = _stable_payload_hash(
            {
                "code_hash": code_hash,
                "goals": goals,
                "diagnostics": diagnostics,
            }
        )
        return {
            "code": code,
            "code_hash": code_hash,
            "goals": goals,
            "diagnostics": diagnostics,
            "file_outline": outline,
            "state_hash": state_hash,
        }

    def progress_delta(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> tuple[ProgressDelta, StateTransition]:
        before_goals = list(before.get("goals") or [])
        after_goals = list(after.get("goals") or [])
        before_complexity = _goals_complexity(before_goals)
        after_complexity = _goals_complexity(after_goals)
        delta = ProgressDelta(
            goals_reduced=len(after_goals) < len(before_goals),
            complexity_reduced=after_complexity < before_complexity,
            stall_detected=(
                before.get("state_hash") == after.get("state_hash") and before_goals == after_goals
            ),
            goal_count_before=len(before_goals),
            goal_count_after=len(after_goals),
            complexity_before=before_complexity,
            complexity_after=after_complexity,
        )
        transition = StateTransition(
            goal_count_before=len(before_goals),
            goal_count_after=len(after_goals),
            progress_delta=delta,
            state_hash_before=str(before.get("state_hash") or ""),
            state_hash_after=str(after.get("state_hash") or ""),
        )
        return delta, transition

    def handle_tool_call(
        self,
        tool_call: ToolCall,
        *,
        read_without_act: int,
    ) -> ToolResult:
        if self.dispatcher is None:
            raise RuntimeError("Initialize the REPL orchestrator before handling tools.")

        if tool_call.name in {
            "lean_goal",
            "lean_diagnostic_messages",
            "lean_code_actions",
            "code_actions",
            "lean_local_search",
            "lean_file_outline",
        }:
            return self._handle_lsp_tool_call(tool_call)

        before = self.get_current_state() if tool_call.name == "apply_tactic" else None
        lean_started_at = time.perf_counter()
        try:
            result = self.dispatcher.handle_tool_call(tool_call)
        finally:
            if self.telemetry is not None:
                self.telemetry.record_lean(lean_started_at)

        if before is not None:
            after = self.get_current_state()
            delta, _transition = self.progress_delta(before, after)
            tool_trace = ToolUsageTrace(
                tool_name=tool_call.name,
                args=tool_call.arguments,
                result=result.content,
                state_hash_before=str(before.get("state_hash") or ""),
                state_hash_after=str(after.get("state_hash") or ""),
            )
            if self.verification_trace is not None:
                self.verification_trace.setdefault("tool_usage_traces", []).append(
                    tool_trace.to_dict()
                )
                self.verification_trace.setdefault("progress_deltas", []).append(delta.to_dict())

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
                result.content + "\n\n[NOTE] You've read the code multiple times without acting. "
                "Try a tactic or rewrite the theorem.",
                result.is_error,
            )

        return result

    def _proof_path(self) -> Path | None:
        if self.proof_path is not None:
            return self.proof_path
        if self.file_controller is not None and hasattr(self.file_controller, "proof_path"):
            try:
                return self.file_controller.proof_path(self.job_id)
            except Exception:
                return None
        return None

    def _call_lsp(self, tool_name: str, callback: Callable[[], Any]) -> Any:
        started_at = time.perf_counter()
        try:
            return callback()
        except LeanLSPUnavailableError as exc:
            return {"error": f"lsp_unavailable: {exc}", "tool_name": tool_name}
        finally:
            if self.telemetry is not None:
                self.telemetry.record_lean(started_at)

    def _handle_lsp_tool_call(self, tool_call: ToolCall) -> ToolResult:
        proof_path = self._proof_path()
        try:
            if tool_call.name == "lean_local_search":
                query = str(tool_call.arguments.get("query", "")).strip()
                if not query:
                    return ToolResult(tool_call.id, "Missing query.", is_error=True)
                limit = int(tool_call.arguments.get("limit", 8))
                payload = self.lsp_client.lean_local_search(query, limit=limit)
                return ToolResult(tool_call.id, json.dumps(payload, ensure_ascii=True))
            if proof_path is None:
                return ToolResult(
                    tool_call.id, "lsp_unavailable: no proof file is attached.", is_error=True
                )
            line = int(tool_call.arguments.get("line", 1))
            column = int(tool_call.arguments.get("column", 1))
            if tool_call.name == "lean_goal":
                payload = self.lsp_client.lean_goal(proof_path, line=line, column=column)
            elif tool_call.name in {"lean_code_actions", "code_actions"}:
                payload = self.lsp_client.lean_code_actions(proof_path, line=line)
            elif tool_call.name == "lean_diagnostic_messages":
                payload = self.lsp_client.lean_diagnostic_messages(
                    proof_path,
                    severity=tool_call.arguments.get("severity"),
                    start_line=tool_call.arguments.get("start_line"),
                    end_line=tool_call.arguments.get("end_line"),
                )
            elif tool_call.name == "lean_file_outline":
                payload = self.lsp_client.lean_file_outline(
                    proof_path,
                    max_declarations=tool_call.arguments.get("max_declarations"),
                )
            else:
                return ToolResult(tool_call.id, f"Unknown tool: {tool_call.name}", is_error=True)
        except LeanLSPUnavailableError as exc:
            return ToolResult(tool_call.id, f"lsp_unavailable: {exc}", is_error=True)
        return ToolResult(tool_call.id, json.dumps(payload, ensure_ascii=True))

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


def _stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _goals_complexity(goals: list[str]) -> int:
    return sum(len(goal.strip()) for goal in goals)
